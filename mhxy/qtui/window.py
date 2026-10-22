"""Qt 主窗口及小型线程桥；检测和启动结果只通过 signal 回到 UI。"""

import copy
from dataclasses import replace
import threading

from PySide6.QtCore import QObject, QSettings, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import theme as T
from ..core import config as cfg_mod
from .role_strip import RoleStrip, WindowState
from .role_window_page import RoleWindowPage
from .window_selection import WindowSelection
from .log_panel import LogPanel
from .log_workspace import LogWorkspace
from .window_tools import WindowTools


class _Bridge(QObject):
    windows_ready = Signal(object, str)
    launch_ready = Signal(bool, object)
    launcher_ready = Signal(object, str)
    window_action_ready = Signal(str, object, str)


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
            rect = win.rect() if not minimized else None
            valid = minimized or rect is not None
            status = ("ready" if label != fallback else "pending") if valid else "failed"
        except Exception:
            minimized, status = False, "failed"
        size = None
        if status != "failed":
            try:
                size = win.normal_size() if minimized else tuple(rect[2:4]) if rect else None
            except Exception:
                pass  # 尺寸暂不可读不影响窗口存在或角色身份。
        states.append(WindowState(hwnd, label, status, minimized, size))
    return states


def run_window_action(cfg, action):
    """显式窗口操作；普通定时扫描仍只读身份缓存。"""
    from ..core import accounts, window as win_mod

    win_mod.set_game_process(cfg.get("window_process") or win_mod.DEFAULT_GAME_PROCESS_SPEC)
    title, offset = cfg.get("window_title", "梦幻西游"), cfg.get("window_offset", [0, 0])
    if action == "close":
        return win_mod.close_all(title, offset)
    if action == "restore_size":
        from ..core import calib_profiles as calib
        targets = cfg.get("targets") or {}
        base = calib.active_size(cfg)
        if not targets.get("window_handles") or not base:
            raise ValueError("未选择目标窗口或未记录基准尺寸")
        return win_mod.restore_targets_size(title, offset, targets, base)
    if action != "wake":
        raise ValueError("未知窗口操作")
    accounts.set_game_dir(cfg.get("game_dir"))
    accounts.set_launcher_path((cfg.get("account_launch") or {}).get("launcher_path"))

    def identify(win):
        # 切过角色的窗口需两次一致观察才更新身份；首次识别后第二次会命中缓存。
        accounts.labels_for([win])
        accounts.labels_for([win])

    total, woken = win_mod.wake_all_to_front(title, offset, on_woken=identify)
    states = scan_windows(cfg)
    return total, woken, sum(s.status == "ready" for s in states), accounts.ocr_status()


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
        ("calibration", "标定中心"),
        ("settings", "设置"),
        ("about", "关于"),
    )

    def __init__(self, settings: QSettings | None = None):
        super().__init__()
        # 此存储仅包含界面布局信息，任务配置仍由 core.config 管理。
        self._settings = settings if settings is not None else QSettings("MHXY", "QtUI")
        self.pages: dict[str, QWidget] = {}
        self.nav_buttons: dict[str, QPushButton] = {}
        self.current_page: str | None = None
        self.cfg = cfg_mod.load_config()
        T.apply_theme(QApplication.instance(), self.cfg.get("appearance", "light"))
        self.window_states = []
        self.selection = WindowSelection()
        self._role_runs = []
        self._missing_captain_notified = False
        self._window_scan_error = ""
        self._windows_observed = False
        self._closed = False
        self._scanning = False
        self._detecting_launcher = False
        self._launching = False
        self._window_action = ""
        self._window_action_message = ""
        self._window_action_level = "info"
        self._rescan_after_action = False
        self._hotkey_down = False
        self.runner = None
        self._runtime = None
        self.float_log = None
        self.bridge = _Bridge(self)
        self.bridge.windows_ready.connect(self._apply_windows, Qt.ConnectionType.QueuedConnection)
        self.bridge.launch_ready.connect(self._launch_result, Qt.ConnectionType.QueuedConnection)
        self.bridge.launcher_ready.connect(self._launcher_result, Qt.ConnectionType.QueuedConnection)
        self.bridge.window_action_ready.connect(self._window_action_result, Qt.ConnectionType.QueuedConnection)
        self.setWindowTitle("梦幻小助手")
        self.resize(*T.WINDOW_SIZE)
        self.setMinimumSize(*T.MIN_WINDOW_SIZE)

        shell = QWidget(self)
        shell.setObjectName("shell")
        layout = QHBoxLayout(shell)
        layout.setContentsMargins(0, 0, T.SPACE_MD, 0)
        layout.setSpacing(T.SPACE_MD)
        self.setCentralWidget(shell)

        navigation = QFrame(shell)
        navigation.setObjectName("navigation")
        navigation.setFixedWidth(T.NAV_WIDTH)
        nav_layout = QVBoxLayout(navigation)
        nav_layout.setContentsMargins(T.SPACE_SM, T.SPACE_MD, T.SPACE_SM, T.SPACE_MD)
        nav_layout.setSpacing(T.SPACE_SM)
        brand = QLabel("梦幻小助手", navigation)
        brand.setProperty("role", "brand")
        nav_layout.addWidget(brand)
        nav_scroll = QScrollArea(navigation)
        nav_scroll.setWidgetResizable(True)
        nav_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        nav_body = QWidget(nav_scroll)
        nav_body.setObjectName("navigationBody")
        buttons_layout = QVBoxLayout(nav_body)
        buttons_layout.setContentsMargins(0, 0, 0, 0)
        buttons_layout.setSpacing(4)
        nav_scroll.setWidget(nav_body)
        nav_layout.addWidget(nav_scroll, 1)
        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        for key, title in self.NAV:
            button = QPushButton(title, navigation)
            button.setProperty("role", "navigation")
            button.setCheckable(True)
            button.clicked.connect(lambda checked=False, page_key=key: self.show_page(page_key))
            self._nav_group.addButton(button)
            self.nav_buttons[key] = button
            buttons_layout.addWidget(button)
        buttons_layout.addStretch()
        self.float_button = QPushButton("悬浮信息条", navigation)
        self.float_button.clicked.connect(self.collapse_to_float)
        nav_layout.addWidget(self.float_button)
        layout.addWidget(navigation)

        self.stack = QStackedWidget(shell)
        self.stack.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        script = QWidget(shell)
        stack_layout = QVBoxLayout(script)
        stack_layout.setContentsMargins(0, 0, 0, 0)
        stack_layout.setSpacing(12)
        self.role_strip = RoleStrip(shell)
        self.role_strip.selection_toggled.connect(self._toggle_window)
        self.role_strip.captain_requested.connect(self._select_captain)
        window_management = QFrame(script)
        window_management.setObjectName("windowManagement")
        window_management_layout = QHBoxLayout(window_management)
        window_management_layout.setContentsMargins(0, 0, 0, 0)
        window_management_layout.setSpacing(T.SPACE_SM)
        window_management_title = QLabel("窗口管理", window_management)
        window_management_title.setProperty("role", "section")
        window_management_layout.addWidget(window_management_title)
        self.window_tools = WindowTools(window_management)
        self._connect_window_tools(self.window_tools)
        window_management_layout.addWidget(self.window_tools, 1)
        stack_layout.addWidget(window_management)
        selection_actions = QHBoxLayout()
        self.selection_count = QLabel("已选 0 / 0", shell)
        selection_actions.addWidget(self.selection_count)
        selection_actions.addStretch()
        self.select_all_button = QPushButton("全选", shell)
        self.clear_selection_button = QPushButton("清空", shell)
        self.select_all_button.clicked.connect(lambda: self._change_selection("all"))
        self.clear_selection_button.clicked.connect(lambda: self._change_selection("clear"))
        selection_actions.addWidget(self.select_all_button)
        selection_actions.addWidget(self.clear_selection_button)
        stack_layout.addLayout(selection_actions)
        stack_layout.addWidget(self.role_strip)
        stack_layout.addWidget(self.stack, 1)
        self.log_panel = LogPanel(shell, collapsed=False, sidebar=True)
        self.log_panel.message_added.connect(self._float_message)
        self.log_workspace = LogWorkspace(script, self.log_panel, self._settings, shell)
        layout.addWidget(self.log_workspace, 1)

        status = self.statusBar()
        status.setSizeGripEnabled(True)
        status.showMessage("就绪")
        self._page_status = QLabel(self)
        self._page_status.setProperty("role", "muted")
        status.addPermanentWidget(self._page_status)
        geometry = self._settings.value("window/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        self._fit_work_area()
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

    def _fit_work_area(self, screen=None):
        """高缩放或小屏幕下，最小尺寸也不能超过实际可用逻辑空间。"""
        screen = screen or self.screen()
        area = screen.availableGeometry()
        frame_width = max(0, self.frameGeometry().width() - self.width())
        frame_height = max(0, self.frameGeometry().height() - self.height())
        if QApplication.platformName() == "offscreen":
            frame_width = frame_height = 0
        width, height = max(1, area.width() - frame_width), max(1, area.height() - frame_height)
        self.setMinimumSize(min(T.MIN_WINDOW_SIZE[0], width), min(T.MIN_WINDOW_SIZE[1], height))
        self.resize(min(self.width(), width), min(self.height(), height))

    def showEvent(self, event):
        super().showEvent(event)
        if not getattr(self, "_screen_connected", False) and self.windowHandle():
            self.windowHandle().screenChanged.connect(self._fit_work_area)
            self._screen_connected = True
        self._fit_work_area()

    def _create_page(self, key: str, title: str) -> QWidget:
        if key == "settings":
            from .settings_page import SettingsPage

            page = SettingsPage(self.stack)
            page.config_changed.connect(self._config_changed)
            return page
        if key == "about":
            from .about_page import AboutPage

            return AboutPage(self.stack)
        if key == "calibration":
            from .calibration_page import CalibrationPage

            page = CalibrationPage(parent=self.stack)
            page.config_changed.connect(self._config_changed)
            page.manage_sizes_requested.connect(self.show_window_sizes)
            return page
        if key == "role_window":
            page = RoleWindowPage(self.cfg, self.stack)
            page.config_changed.connect(self._config_changed)
            page.refresh_requested.connect(self.refresh_windows)
            page.launch_requested.connect(self.toggle_launch)
            page.detect_launcher_requested.connect(self.detect_launcher)
            page.size_panel.restore_requested.connect(self.restore_selected_size)
            page.size_panel.editing_changed.connect(self._update_strip)
            return page
        if key == "daily":
            from .daily_page import DailyPage

            page = DailyPage(parent=self.stack)
            self._manage_task_page(page)
            page.config_changed.connect(self._config_changed)
            return page
        if key in ("freeclick", "general"):
            from .freeclick_page import FreeClickPage
            from .inventory_panel import InventoryPanel

            page = (FreeClickPage if key == "freeclick" else InventoryPanel)(parent=self.stack)
            self._manage_task_page(page)
            page.config_changed.connect(self._config_changed)
            page.update_windows(self.window_states)
            return page
        if key == "dungeon":
            from .dungeon_page import DungeonPage

            page = DungeonPage(parent=self.stack)
            self._manage_task_page(page)
            page.config_changed.connect(self._config_changed)
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
        self._update_strip()
        self.log_workspace.set_page(self.pages[key])

    @property
    def role_page(self) -> RoleWindowPage:
        return self.pages["role_window"]

    def show_window_sizes(self):
        self.show_page("role_window")
        self.role_page.body_scroll.ensureWidgetVisible(self.role_page.size_panel)
        self.role_page.size_panel.restore_button.setFocus()

    def _config_changed(self, cfg):
        self.selection.write(cfg)
        self.cfg = cfg
        self.role_page.cfg = copy.deepcopy(cfg)
        calibration = self.pages.get("calibration")
        if calibration is not None:
            calibration.cfg = copy.deepcopy(cfg)
            calibration.refresh_config()
        app = QApplication.instance()
        if app.property("appearance") != cfg.get("appearance", "light"):
            T.apply_theme(app, cfg.get("appearance", "light"))
        self._update_strip()
        for page in self._task_pages():
            if (not page._start_pending and not page._runtime_running()
                    and not getattr(page, "_capturing", False) and getattr(page, "_editor", None) is None):
                page.cfg = copy.deepcopy(cfg)
                if hasattr(page, "update_windows"):
                    page.update_windows(self.window_states)

    def _calibration_running(self):
        return (bool(self._window_action) or self._launching or self._runner_running()
                or any(page._start_pending or page._runtime_running() for page in self._task_pages()))

    def _connect_window_tools(self, tools):
        tools.wake_requested.connect(self.wake_game_windows)
        tools.close_requested.connect(self.close_game_windows)
        tools.update_state(self._window_action, self._window_action_message, self._window_action_level)

    def _show_window_action(self, message, level="info", *, action=""):
        self._window_action_message, self._window_action_level = message, level
        action = action or self._window_action
        text = "" if action == "restore_size" else message
        self.window_tools.update_state(self._window_action, text, level)
        if action == "restore_size":
            self.role_page.size_panel.set_status(message, level)
        self.statusBar().showMessage(message)

    def _window_editor_open(self):
        return (self.role_page.size_panel._editing
                or any(getattr(page, "_capturing", False) or getattr(page, "_editor", None) is not None
                       or getattr(page, "_calibration_dialog", None) is not None for page in self.pages.values()))

    def restore_selected_size(self):
        from ..core import calib_profiles as calib
        if self._closed or self._window_action:
            return
        panel = self.role_page.size_panel
        if self._selection_locked():
            panel.set_status("请先停止任务或关闭编辑窗口，再恢复尺寸。", "warn")
            return
        if self._window_scan_error:
            panel.set_status("窗口检测失败，请刷新后重试。", "warn")
            return
        if not calib.active_size(self.cfg):
            panel.set_status("请先记录窗口尺寸或完成一次标定。", "warn")
            return
        try:
            draft = self.selection.snapshot(copy.deepcopy(self.cfg))
        except ValueError as exc:
            panel.set_status(str(exc), "warn")
            return
        self._start_window_action("restore_size", draft)

    def wake_game_windows(self):
        from ..core.runtime import get_runtime

        if self._closed or self._window_action:
            return
        if self._calibration_running() or get_runtime().any_running():
            self._show_window_action("请先停止任务，再唤出并识别角色名。", "warn")
            return
        if self._window_editor_open():
            self._show_window_action("请先关闭编辑或标定窗口。", "warn")
            return
        self._start_window_action("wake")

    def close_game_windows(self):
        if self._closed or self._window_action:
            return
        if self._window_editor_open():
            self._show_window_action("请先关闭编辑或标定窗口。", "warn")
            return
        dialog = QMessageBox(QMessageBox.Icon.Warning, "关闭所有游戏窗口",
                             "将强制结束全部游戏进程，未保存的进度会丢失；正在运行的任务会先停止。",
                             QMessageBox.StandardButton.NoButton, self)
        confirm = dialog.addButton("确认关闭", QMessageBox.ButtonRole.DestructiveRole)
        T.set_role(confirm, "danger")
        cancel = dialog.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        dialog.setDefaultButton(cancel)
        dialog.setEscapeButton(cancel)
        dialog.exec()
        accepted = dialog.clickedButton() is confirm
        dialog.deleteLater()
        if accepted and not self._closed:
            self._start_window_action("close")

    def _start_window_action(self, action, cfg=None):
        from ..core import calib_profiles as calib
        cfg = copy.deepcopy(self.cfg if cfg is None else cfg)
        self._window_action_size = calib.active_size(cfg)
        self._window_action_handles = list(cfg.get("targets", {}).get("window_handles", []))
        self._window_action = action
        self._show_window_action({"wake": "正在唤出窗口并识别角色名…", "close": "正在关闭游戏窗口…",
                                  "restore_size": "正在恢复选中窗口尺寸…"}[action])
        self._update_strip()
        if action == "close":
            self.stop_all_tasks()
        bridge = self.bridge

        def work():
            try:
                result, error = run_window_action(cfg, action), ""
            except Exception as exc:
                result, error = None, str(exc)
            try:
                bridge.window_action_ready.emit(action, result, error)
            except RuntimeError:
                pass

        threading.Thread(target=work, name="mhxy-window-" + action, daemon=True).start()

    @Slot(str, object, str)
    def _window_action_result(self, action, result, error):
        if self._closed:
            return
        self._window_action = ""
        level = "info"
        if error:
            message, level = "窗口操作失败：" + error, "error"
        elif action == "wake":
            total, woken, identified, ocr = result
            message = f"检测到 {total} 个窗口 · 已唤出 {woken} 个 · 角色名已确认 {identified} 个"
            if woken < total or identified < total:
                level = "warn"
            if total and identified < total and ocr.startswith("unavailable:"):
                message += "；角色名识别不可用：" + ocr.partition(":")[2].strip()
        elif action == "restore_size":
            ok, total, actual = result
            size = self._window_action_size
            target = f"{size[0]}×{size[1]}"
            message = f"已恢复 {ok}/{total} 个窗口至 {target}"
            actual_by_handle = dict(zip(self._window_action_handles, actual))
            self.window_states = [replace(s, size=tuple(actual_by_handle[s.hwnd]) if actual_by_handle[s.hwnd] else None)
                                  if s.hwnd in actual_by_handle else s for s in self.window_states]
            if not total:
                message, level = "目标窗口已变化，请刷新并重新选择。", "warn"
            elif ok < total:
                level = "warn"
                valid = [s for s in actual if s]
                if (len(valid) == total
                        and any(abs(valid[0][i] - size[i]) > 4 for i in (0, 1))
                        and all(abs(s[0] - valid[0][0]) <= 4 and abs(s[1] - valid[0][1]) <= 4 for s in valid)):
                    message += f"；实际尺寸 {valid[0][0]}×{valid[0][1]}，目标尺寸可能受客户端比例限制，请检查后重新标定。"
                else:
                    message += "；部分窗口调整失败，请检查窗口状态或运行权限。"
        else:
            total, killed = result
            message = f"已关闭 {killed}/{total} 个游戏进程" if total else "未检测到游戏进程"
            if killed < total:
                message += "；部分进程未退出，请检查运行权限后重试。"
                level = "warn"
        self._update_strip()
        self._show_window_action(message, level, action=action)
        self.log_panel.append(message, level, "窗口管理")
        if self._scanning:
            self._rescan_after_action = True
        else:
            self.refresh_windows()

    def _activity_pages(self):
        return [self.pages[key] for key in ("sniper", "treasure_map", "escort", "secret_realm", "dungeon", "freeclick", "general")
                if key in self.pages]

    def _task_pages(self):
        return [page for key, page in self.pages.items()
                if key in ("daily", "sniper", "treasure_map", "escort", "secret_realm", "dungeon", "freeclick", "general")]

    def _manage_task_page(self, page):
        # 独立页面仍可使用原日志面板；嵌入主窗口时只显示一个全局抽屉。
        page.start_requested.connect(lambda: self._task_starting(page))
        page.log_consumption_paused = True
        page.log_panel.hide()
        page.log_panel.message_added.connect(self.log_panel.append)

    def _pump_task_logs(self):
        from ..core.runtime import get_runtime

        pages = self._task_pages()
        runtime = self._runtime or get_runtime()
        for event in runtime.drain_logs(64):
            source_runner, _level, msg, _source = event
            self.log_panel.append_event(event)
            if source_runner is self.runner:
                self.role_page.status.setText(msg)
                self.statusBar().showMessage(msg)
        for page in pages:
            if not page._visible:
                continue
            if page.runner is not None:
                if page._start_pending or page._runtime_running():
                    page._set_status(page._status_key())
                elif not page._terminal_rendered:
                    page._terminal_rendered = True
                    page._set_status(page._status_key())
                    page.start_button.setEnabled(True)
                    page.stop_button.setEnabled(False)

    def collapse_to_float(self):
        if self._closed:
            return
        if self.float_log is None:
            from .float_log import FloatLogWindow

            self.float_log = FloatLogWindow(self.cfg.get("float_log"))
            self.float_log.restore_requested.connect(self.restore_from_float)
            self.float_log.stop_requested.connect(self.stop_all_tasks)
        self._update_float_state()
        self.float_log.show()
        self.hide()

    def restore_from_float(self):
        floating, self.float_log = self.float_log, None
        self.showNormal()
        self.raise_()
        self.activateWindow()
        if floating is not None:
            preferences = floating.preferences()
            floating.dispose()
            self._save_float_preferences(preferences)

    def _save_float_preferences(self, preferences):
        try:
            cfg = cfg_mod.load_config()
            cfg.setdefault("float_log", {}).update(preferences)
            cfg_mod.save_config(cfg)
            self.cfg["float_log"] = cfg["float_log"]
        except (OSError, cfg_mod.ConfigWriteRejected) as exc:
            self.log_panel.append(f"悬浮条偏好未保存：{exc}", "error", "界面")

    def _float_message(self, message, level, source):
        if self.float_log is not None:
            self.float_log.append(message, level, source)

    def _update_float_state(self):
        if self.float_log is None:
            return
        from ..core.runtime import get_runtime

        phase, label, _ = (self._runtime or get_runtime()).state()
        # start 线程尚未登记或返回时，悬浮条也应能取消启动。
        pending = next((p for p in self._task_pages() if p._start_pending), None)
        if pending is not None:
            phase, label = pending._status_key(), pending.page_title
        elif self._launching:
            phase = "stopping" if self._launch_cancel.is_set() else "running"
            label = "启动登录"
        self.float_log.update_state(phase, label)

    def _selection_locked(self):
        from ..core.runtime import get_runtime
        return self._calibration_running() or get_runtime().any_running() or self._window_editor_open()

    def prepare_task_config(self, cfg, task_name):
        if self._selection_locked():
            raise ValueError("已有任务运行或停止中，请等待结束")
        draft = self.selection.snapshot(copy.deepcopy(cfg))
        if self._needs_captain(draft, task_name) and self.selection.captain not in self.selection.selected:
            raise ValueError("请将顶部队长角色选入本次执行窗口")
        return draft

    @staticmethod
    def _needs_captain(cfg, task_name):
        from ..tasks import get_task
        task = get_task(task_name)
        if task_name == "daily":
            return any(s.get("enabled", True) and s.get("task") == "dungeon"
                       for s in cfg_mod.task_config(cfg, "daily").get("steps", []))
        return task_name == "dungeon" or bool(task and getattr(task, "is_dungeon", False))

    def _task_starting(self, page):
        if page.runner is not None:
            self._role_runs.append(page.runner)
            self._role_runs = self._role_runs[-16:]
        self._update_strip()

    def _toggle_window(self, hwnd):
        self._change_selection("toggle", hwnd)

    def _select_captain(self, hwnd):
        self._change_selection("captain", hwnd)

    def _change_selection(self, action, hwnd=None):
        if self._selection_locked() or (hwnd is not None and hwnd not in self.selection.order):
            self._update_strip()
            return
        previous = copy.deepcopy(self.selection)
        if action == "all":
            self.selection.selected = set(self.selection.order)
        elif action == "clear":
            self.selection.selected.clear()
        elif action == "toggle":
            self.selection.selected.symmetric_difference_update({hwnd})
        elif action == "captain":
            self.selection.captain = hwnd
        try:
            draft = cfg_mod.load_config()
            self.selection.write(draft)
            cfg_mod.save_config(draft)
        except Exception as exc:
            self.selection = previous
            self.log_panel.append("窗口选择保存失败：" + str(exc), "error", "界面")
            self._update_strip()
            return
        self._config_changed(draft)

    def _window_activities(self):
        activities = {}
        for runner in self._role_runs:
            handles = runner.cfg.get("targets", {}).get("window_handles", [])
            reported = getattr(runner, "window_activities", lambda: {})()
            pending = any(p.runner is runner and p._start_pending for p in self._task_pages())
            running = runner.is_running() or pending
            stopping = runner.stop_event.is_set() and running
            for h in handles:
                if reported.get(h, ("", ""))[0] in {"已完成", "执行异常", "部分完成"}:
                    value = reported[h]
                elif stopping:
                    value = ("停止中…", "warning")
                elif running:
                    value = reported.get(h, ("等待执行…", "warning"))
                else:
                    outcome = runner.outcome
                    value = {"completed": ("已完成", "success"), "stopped": ("已停止", ""),
                             "failed": ("执行异常", "error"), "timeout": ("已超时", "warning"),
                             "partial": ("部分完成", "warning")}.get(outcome, ("启动失败", "error"))
                activities[h] = value
        return activities

    def _update_strip(self, error=None):
        error = self._window_scan_error if error is None else error
        locked = self._selection_locked()
        changed = self.selection.reconcile(self.window_states, self.cfg, locked=locked, observed=self._windows_observed)
        team_running = any((p._start_pending or p._runtime_running())
                           and self._needs_captain(p.cfg, p.task_name) for p in self._task_pages())
        if (team_running and self.selection.captain is not None
                and self.selection.captain not in self.selection.order and not self._missing_captain_notified):
            self._missing_captain_notified = True
            self.stop_all_tasks()
            self.log_panel.append("队长窗口已消失，任务已请求停止；退出后重新指定队长。", "warn", "窗口")
        if not locked:
            self._missing_captain_notified = False
        if changed:
            self.log_panel.append("队长窗口已变化，已重新指定顶部队长。", "warn", "窗口")
        self.selection.write(self.cfg)
        selected = [i for i, h in enumerate(self.selection.order) if h in self.selection.selected]
        captain = self.selection.order.index(self.selection.captain) if self.selection.captain in self.selection.order else None
        self.role_strip.update_windows(self.window_states, selected, captain, error,
                                       activities=self._window_activities(), locked=locked)
        self.selection_count.setText(f"已选 {len(selected)} / {len(self.window_states)}")
        self.select_all_button.setEnabled(not locked and bool(self.window_states))
        self.clear_selection_button.setEnabled(not locked and bool(selected))
        self.role_page.size_panel.set_context(self.cfg, self.window_states, self.selection.selected,
                                              locked=locked, error=error)
        self.role_page.set_interaction_locked(locked)
        for page in self._task_pages():
            if not page._start_pending and not page._runtime_running():
                page.start_button.setEnabled(not locked and bool(selected) and not error)
                page.start_button.setToolTip("" if selected else "请在顶部选择目标窗口")

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
        self._window_scan_error = error
        if not error:
            self._windows_observed = True
        self.window_states = ([replace(state, status="failed") for state in self.window_states]
                              if error else states)
        self._update_strip(error)
        self.role_page.update_windows(self.window_states, error)
        for page in self._activity_pages():
            if not page._start_pending and not page._runtime_running():
                page.cfg = copy.deepcopy(self.cfg)
            page.update_windows(self.window_states, error)
        if self._rescan_after_action:
            self._rescan_after_action = False
            self.refresh_windows()

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
        if self._window_action:
            self.role_page.status.setText("请等待窗口操作完成。")
            return
        if self._launching or self._runner_running():
            self.stop_all_tasks()
            return
        if self._selection_locked():
            self.role_page.status.setText("请先停止任务或关闭编辑窗口。")
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
        from ..core.runtime import get_runtime

        for page in self._task_pages():
            page.stop()
        if self._runtime is not None:
            if hasattr(self, "_launch_cancel"):
                self._launch_cancel.set()
            self._runtime.stop(self.runner)
            if self.runner is not None:
                self.role_page.set_running(True, stopping=True)
        (self._runtime or get_runtime()).stop_all()
        self._update_float_state()

    def _pump_launch(self):
        if self._closed:
            return
        active_pages = [page for page in self._task_pages()
                        if page._start_pending or page._runtime_running()]
        launch_active = self._launching or self._runner_running()
        # 沿用原 GUI 的热键解析与 core 的只读急停判据，不引入 Tk 窗口。
        from .shortcuts import _parse_stop_hotkey
        from ..core.emergency import get_cursor, in_failsafe_corner, vk_down

        if launch_active or active_pages:
            cfg = self.cfg
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
        self._update_float_state()
        self._update_strip()
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
        if self.float_log is not None:
            floating, self.float_log = self.float_log, None
            self._save_float_preferences(floating.preferences())
            floating.dispose()
        for page in self.pages.values():
            page.close()
        self._settings.setValue("window/geometry", self.saveGeometry())
        self.log_workspace.save_settings()
        super().closeEvent(event)
