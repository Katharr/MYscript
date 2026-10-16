"""Qt 主窗口及小型线程桥；检测和启动结果只通过 signal 回到 UI。"""

import copy
from collections import deque
from dataclasses import replace
import threading

from PySide6.QtCore import QObject, QSettings, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import theme as T
from ..core import config as cfg_mod
from .role_strip import RoleStrip, WindowState
from .role_window_page import RoleWindowPage, target_indices


class _Bridge(QObject):
    windows_ready = Signal(object, str)
    launch_ready = Signal(bool, object)
    launcher_ready = Signal(object, str)


def scan_windows(cfg):
    """后台只读快照；不唤窗、截图或 OCR，最小化不视作消失。"""
    from ..core import accounts, window as win_mod

    win_mod.set_game_process(cfg.get("window_process") or win_mod.DEFAULT_GAME_PROCESS_SPEC)
    accounts.set_game_dir(cfg.get("game_dir"))
    accounts.set_launcher_path((cfg.get("account_launch") or {}).get("launcher_path"))
    wins = win_mod.locate_all(cfg.get("window_title", "梦幻西游"),
                              cfg.get("window_offset", [0, 0]), include_minimized=True)
    labels = accounts.sync_window_labels(wins)
    states = []
    for index, win in enumerate(wins):
        fallback = accounts.fallback_label(index)
        label = labels[index] if index < len(labels) and labels[index] else fallback
        hwnd = int(win._win._hWnd)
        try:
            minimized = bool(win._win.isMinimized)
            valid = minimized or win.rect() is not None
            status = ("ready" if label != fallback else "pending") if valid else "failed"
        except Exception:
            minimized, status = False, "failed"
        states.append(WindowState(hwnd, label, status, minimized))
    return states


class MainWindow(QMainWindow):
    NAV = (
        ("role_window", "角色与窗口"),
        ("general", "通用 / 工具"),
        ("daily", "日常一条龙"),
        ("sniper", "秒装备"),
        ("treasure_map", "宝图"),
        ("escort", "运镖"),
        ("secret_realm", "秘境降妖"),
        ("dungeon", "刷副本"),
        ("freeclick", "自由点击"),
        ("settings", "设置"),
        ("about", "关于"),
    )

    def __init__(self, settings: QSettings | None = None):
        super().__init__()
        # 此存储仅包含窗口几何信息，任务配置仍由 core.config 管理。
        self._settings = settings if settings is not None else QSettings("MHXY", "QtUI")
        self.pages: dict[str, QWidget] = {}
        self.nav_buttons: dict[str, QPushButton] = {}
        self.current_page: str | None = None
        self.cfg = cfg_mod.load_config()
        self.window_states = []
        self._closed = False
        self._scanning = False
        self._detecting_launcher = False
        self._launching = False
        self._hotkey_down = False
        self.runner = None
        self._runtime = None
        self.bridge = _Bridge(self)
        self.bridge.windows_ready.connect(self._apply_windows, Qt.ConnectionType.QueuedConnection)
        self.bridge.launch_ready.connect(self._launch_result, Qt.ConnectionType.QueuedConnection)
        self.bridge.launcher_ready.connect(self._launcher_result, Qt.ConnectionType.QueuedConnection)
        self.setWindowTitle("梦幻小助手 · Qt")
        self.resize(*T.WINDOW_SIZE)
        self.setMinimumSize(*T.MIN_WINDOW_SIZE)

        shell = QWidget(self)
        shell.setObjectName("shell")
        layout = QHBoxLayout(shell)
        layout.setContentsMargins(0, 0, T.SPACE_LG, 0)
        layout.setSpacing(T.SPACE_LG)
        self.setCentralWidget(shell)

        navigation = QFrame(shell)
        navigation.setObjectName("navigation")
        navigation.setFixedWidth(T.NAV_WIDTH)
        nav_layout = QVBoxLayout(navigation)
        nav_layout.setContentsMargins(T.SPACE_SM, T.SPACE_LG, T.SPACE_SM, T.SPACE_MD)
        nav_layout.setSpacing(T.SPACE_SM)
        brand = QLabel("梦幻小助手", navigation)
        brand.setProperty("role", "brand")
        nav_layout.addWidget(brand)
        nav_layout.addSpacing(T.SPACE_MD)
        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        for key, title in self.NAV:
            button = QPushButton(title, navigation)
            button.setProperty("role", "navigation")
            button.setCheckable(True)
            button.clicked.connect(lambda checked=False, page_key=key: self.show_page(page_key))
            self._nav_group.addButton(button)
            self.nav_buttons[key] = button
            nav_layout.addWidget(button)
        nav_layout.addStretch()
        layout.addWidget(navigation)

        self.stack = QStackedWidget(shell)
        stack_layout = QVBoxLayout()
        stack_layout.setContentsMargins(0, T.SPACE_LG, 0, T.SPACE_LG)
        self.role_strip = RoleStrip(shell)
        stack_layout.addWidget(self.role_strip)
        stack_layout.addWidget(self.stack)
        layout.addLayout(stack_layout, 1)

        status = self.statusBar()
        status.setSizeGripEnabled(True)
        status.showMessage("就绪")
        self._page_status = QLabel(self)
        self._page_status.setProperty("role", "muted")
        status.addPermanentWidget(self._page_status)
        geometry = self._settings.value("window/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        self.show_page(self.NAV[0][0])
        self.scan_timer = QTimer(self)
        self.scan_timer.setInterval(1500)
        self.scan_timer.timeout.connect(self.refresh_windows)
        self.scan_timer.start()
        self.runtime_timer = QTimer(self)
        self.runtime_timer.setInterval(60)
        self.runtime_timer.timeout.connect(self._pump_launch)
        self.runtime_timer.start()
        # 窗口先显示，再开始后台检测；零窗口和检测慢都不改变启动入口。
        QTimer.singleShot(0, self.refresh_windows)

    def _create_page(self, key: str, title: str) -> QWidget:
        if key == "role_window":
            page = RoleWindowPage(self.cfg, self.stack)
            page.config_changed.connect(self._config_changed)
            page.refresh_requested.connect(self.refresh_windows)
            page.launch_requested.connect(self.toggle_launch)
            page.detect_launcher_requested.connect(self.detect_launcher)
            return page
        if key == "daily":
            from .daily_page import DailyPage

            page = DailyPage(parent=self.stack)
            self._manage_task_page(page)
            page.config_changed.connect(self._config_changed)
            page.window_selection_requested.connect(lambda: self.show_page("role_window"))
            return page
        if key in ("freeclick", "general"):
            from .freeclick_page import FreeClickPage
            from .inventory_panel import InventoryPanel

            page = (FreeClickPage if key == "freeclick" else InventoryPanel)(parent=self.stack)
            self._manage_task_page(page)
            page.config_changed.connect(self._config_changed)
            page.window_selection_requested.connect(lambda: self.show_page("role_window"))
            page.update_windows(self.window_states)
            return page
        if key == "dungeon":
            from .dungeon_page import DungeonPage

            page = DungeonPage(parent=self.stack)
            self._manage_task_page(page)
            page.config_changed.connect(self._config_changed)
            page.window_selection_requested.connect(lambda: self.show_page("role_window"))
            page.update_windows(self.window_states)
            return page
        if key in ("sniper", "treasure_map", "escort", "secret_realm"):
            from .sniper_page import SniperPage
            from .treasure_map_page import TreasureMapPage
            from .escort_page import EscortPage
            from .secret_realm_page import SecretRealmPage

            page_cls = {"sniper": SniperPage, "treasure_map": TreasureMapPage, "escort": EscortPage,
                        "secret_realm": SecretRealmPage}[key]
            page = page_cls(parent=self.stack)
            self._manage_task_page(page)
            page.config_changed.connect(self._config_changed)
            page.window_selection_requested.connect(lambda: self.show_page("role_window"))
            page.update_windows(self.window_states)
            return page
        page = QWidget(self.stack)
        page.setObjectName(f"page_{key}")
        page.setProperty("role", "page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(T.SPACE_LG, T.SPACE_LG, T.SPACE_LG, T.SPACE_LG)
        layout.setSpacing(T.SPACE_MD)
        heading = QLabel(title, page)
        heading.setProperty("role", "heading")
        heading.setWordWrap(True)
        layout.addWidget(heading)
        state = QLabel("待迁移", page)
        state.setProperty("role", "muted")
        layout.addWidget(state)
        layout.addStretch()
        return page

    def show_page(self, key: str) -> None:
        titles = dict(self.NAV)
        if key not in titles:
            raise KeyError(f"未知页面: {key}")
        if key not in self.pages:
            page = self._create_page(key, titles[key])
            self.stack.addWidget(page)
            self.pages[key] = page
        self.stack.setCurrentWidget(self.pages[key])
        self.current_page = key
        self.nav_buttons[key].setChecked(True)
        self._page_status.setText(titles[key])

    @property
    def role_page(self) -> RoleWindowPage:
        return self.pages["role_window"]

    def _config_changed(self, cfg):
        self.cfg = cfg
        self._update_strip()
        for page in self._activity_pages():
            if (not page._start_pending and not page._runtime_running()
                    and not getattr(page, "_capturing", False) and getattr(page, "_editor", None) is None):
                page.cfg = copy.deepcopy(cfg)
                page.update_windows(self.window_states)

    def _activity_pages(self):
        return [self.pages[key] for key in ("sniper", "treasure_map", "escort", "secret_realm", "dungeon", "freeclick", "general")
                if key in self.pages]

    def _task_pages(self):
        return [page for key, page in self.pages.items()
                if key in ("daily", "sniper", "treasure_map", "escort", "secret_realm", "dungeon", "freeclick", "general")]

    def _manage_task_page(self, page):
        # RuntimeService 只有一个日志流；窗口按 runner 分发，避免空闲页抢日志。
        # 隐藏页只缓存数据，回到该页后再以固定批量显示。
        page.log_consumption_paused = True
        page._pending_logs = deque(maxlen=page.log_panel.max_lines)
        page.log_panel.cleared.connect(page._pending_logs.clear)

    def _pump_task_logs(self):
        from ..core.runtime import get_runtime

        pages = self._task_pages()
        runtime = self._runtime or get_runtime()
        for event in runtime.drain_logs(64):
            source_runner, _level, msg, _source = event
            for page in pages:
                if source_runner is page.runner:
                    page._pending_logs.append(event)
                    break
            if source_runner is self.runner:
                self.role_page.status.setText(msg)
                self.statusBar().showMessage(msg)
        for page in pages:
            if not page._visible:
                continue
            for _ in range(min(page.log_batch_size, len(page._pending_logs))):
                page.log_panel.append_event(page._pending_logs.popleft())
            if page.runner is not None:
                if page._start_pending or page._runtime_running():
                    page._set_status(page._status_key())
                elif not page._terminal_rendered:
                    page._terminal_rendered = True
                    page._set_status(page._status_key())
                    page.start_button.setEnabled(True)
                    page.stop_button.setEnabled(False)

    def _update_strip(self, error=""):
        selected = target_indices(self.cfg.get("targets") or {}, len(self.window_states), apply_limit=True)
        index = cfg_mod.task_config(self.cfg, "teaming").get("captain_index", 0)
        captain = selected[index] if 0 <= index < len(selected) else (selected[0] if selected else None)
        self.role_strip.update_windows(self.window_states, selected, captain, error)

    def refresh_windows(self):
        if self._closed or self._scanning:
            return
        self._scanning = True
        cfg, bridge = copy.deepcopy(self.cfg), self.bridge

        def work():
            try:
                states, error = scan_windows(cfg), ""
            except Exception as exc:
                states, error = [], str(exc)
            try:
                bridge.windows_ready.emit(states, error)
            except RuntimeError:
                pass  # 窗口已关闭，不等待后台系统枚举。

        threading.Thread(target=work, daemon=True).start()

    @Slot(object, str)
    def _apply_windows(self, states, error):
        if self._closed:
            return
        self._scanning = False
        self.window_states = ([replace(state, status="failed") for state in self.window_states]
                              if error else states)
        self._update_strip(error)
        self.role_page.update_windows(self.window_states, error)
        for page in self._activity_pages():
            page.update_windows(self.window_states, error)

    def detect_launcher(self):
        if self._closed or self._detecting_launcher:
            return
        self._detecting_launcher = True
        self.role_page.detect_button.setEnabled(False)
        bridge = self.bridge

        def work():
            try:
                from ..core import launcher
                paths, error = launcher.discover_candidates(), ""
            except Exception as exc:
                paths, error = [], str(exc)
            try:
                bridge.launcher_ready.emit(paths, error)
            except RuntimeError:
                pass

        threading.Thread(target=work, daemon=True).start()

    @Slot(object, str)
    def _launcher_result(self, paths, error):
        if self._closed:
            return
        self._detecting_launcher = False
        self.role_page.detect_button.setEnabled(not self._launching and not self._runner_running())
        if error or not paths:
            self.role_page.status.setText("启动器检测失败：" + (error or "未发现启动器"))
            return
        self.role_page.path.setText(paths[0])
        self.role_page.save_path()

    def _runner_running(self):
        return self._runtime is not None and self._runtime.is_running(self.runner)

    def toggle_launch(self):
        if self._closed:
            return
        if self._launching or self._runner_running():
            self.stop_all_tasks()
            return
        # 这里只构造已有 runner；耗时的 preflight/start 全部放在线程里。
        from ..core.runtime import get_runtime
        from ..tasks.launch_login import LaunchLoginTask

        self.role_page.save_path()
        self.cfg = cfg_mod.load_config()
        self._runtime = get_runtime()
        self.runner = self._runtime.create_runner(LaunchLoginTask(), self.cfg)
        self._launching = True
        self.role_page.set_running(True)
        self.role_page.status.setText("启动检查中…")
        runner, runtime, bridge = self.runner, self._runtime, self.bridge
        # 独立取消标记覆盖线程尚未开始的停止请求。
        cancel = self._launch_cancel = threading.Event()

        def work():
            if cancel.is_set():
                ok, problems = False, ["启动已取消"]
            else:
                ok, problems = runtime.start(runner)
            if cancel.is_set():
                runtime.stop(runner)
            try:
                bridge.launch_ready.emit(ok, problems)
            except RuntimeError:
                runtime.stop(runner)

        threading.Thread(target=work, daemon=True).start()

    @Slot(bool, object)
    def _launch_result(self, ok, problems):
        if self._closed:
            return
        self._launching = False
        if not ok:
            self.role_page.set_running(False)
            self.role_page.status.setText("无法启动：" + "；".join(problems))
            self.runner = None
        else:
            self.role_page.status.setText("启动登录运行中")
        self.refresh_windows()

    def stop_all_tasks(self):
        for page in self._task_pages():
            page.stop()
        if self._runtime is not None:
            if hasattr(self, "_launch_cancel"):
                self._launch_cancel.set()
            self._runtime.stop(self.runner)
            self._runtime.stop_all()
            if self.runner is not None:
                self.role_page.set_running(True, stopping=True)

    def _pump_launch(self):
        if self._closed:
            return
        active_pages = [page for page in self._task_pages()
                        if page._start_pending or page._runtime_running()]
        launch_active = self._launching or self._runner_running()
        # 沿用原 GUI 的热键解析与 core 的只读急停判据，不引入 Tk 窗口。
        from ..gui.shortcuts import _parse_stop_hotkey
        from ..core.emergency import get_cursor, in_failsafe_corner, vk_down

        if launch_active or active_pages:
            cfg = active_pages[0].cfg if active_pages else self.cfg
            try:
                cx, cy = get_cursor()
            except Exception:
                cx, cy = None, None
            corner = in_failsafe_corner(cx, cy, cfg.get("failsafe_corner", "top_right"))
            mods, key = _parse_stop_hotkey(cfg.get("hotkey_stop", "ctrl+alt+F12"))
            down = key is not None and all(vk_down(mod) for mod in mods) and vk_down(key)
            if corner or (down and not self._hotkey_down):
                self.stop_all_tasks()
            if launch_active and self._launch_cancel.is_set():
                self._runtime.stop(self.runner)
            self._hotkey_down = down
        else:
            self._hotkey_down = False
        self._pump_task_logs()
        if self._runtime is None or self.runner is None:
            return
        if self.runner is not None:
            if not self._launching and not self._runner_running():
                outcome = self.runner.outcome
                names = {"completed": "已完成", "stopped": "已停止", "failed": "失败",
                         "timeout": "超时", "partial": "部分完成"}
                self.role_page.status.setText("启动登录：" + names.get(outcome, outcome))
                self.role_page.set_running(False)
                self.runner = None
                self.refresh_windows()

    def closeEvent(self, event: QCloseEvent) -> None:
        self._closed = True
        self.scan_timer.stop()
        self.runtime_timer.stop()
        self.stop_all_tasks()
        for page in self.pages.values():
            page.close()
        self._settings.setValue("window/geometry", self.saveGeometry())
        super().closeEvent(event)
