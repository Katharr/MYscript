"""窗口工具和启动档案；执行窗口及队长统一在顶部选择。"""

import uuid

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QLayout, QLineEdit,
    QPushButton, QScrollArea, QVBoxLayout, QWidget,
)
from .widgets import FormLayout as QFormLayout

from ..core import config as cfg_mod
from . import theme as T
from .widgets import CollapsibleSection, FlowLayout, set_field_width
from .window_size_panel import WindowSizePanel


def target_indices(targets, count, *, apply_limit=False, states=None):
    """与 resolve_targets 同序；编辑时保留勾选集，展示实际目标时应用数量上限。"""
    if not count:
        return []
    if "selected_hwnds" in targets:
        if states is not None:
            selected = set(targets.get("window_handles", targets["selected_hwnds"]))
            return [i for i, state in enumerate(states) if state.hwnd in selected]
        return [i for i in targets.get("multi_indices", []) if 0 <= i < count]
    if targets.get("multi"):
        indices = [i for i in (targets.get("multi_indices") or range(count)) if 0 <= i < count]
        indices = indices or list(range(count))
        limit = targets.get("max_windows", 0)
        return indices[:limit] if apply_limit and limit and limit > 0 else indices
    index = targets.get("single_index", 0)
    return [index if isinstance(index, int) and 0 <= index < count else 0]


class RoleWindowPage(QWidget):
    launch_requested = Signal()
    detect_launcher_requested = Signal()
    refresh_requested = Signal()
    config_changed = Signal(object)

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.states = []
        self._busy = False
        self._stopping = False
        self._interaction_locked = False
        self._launch_expanded = None
        self.setObjectName("page_role_window")
        T.set_role(self, "page")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(T.SPACE_LG, T.SPACE_LG, T.SPACE_LG, T.SPACE_LG)
        layout.setSpacing(T.SPACE_MD)
        header = QHBoxLayout()
        title = QLabel("角色与窗口", self)
        title.setProperty("role", "heading")
        header.addWidget(title, 1)
        self.window_status = QLabel("检测中…", self)
        self.window_status.setProperty("role", "muted")
        self.window_status.setWordWrap(True)
        header.addWidget(self.window_status)
        self.refresh_button = QPushButton("刷新", self)
        self.refresh_button.clicked.connect(self.refresh_requested.emit)
        header.addWidget(self.refresh_button)
        layout.addLayout(header)
        self.body_scroll = QScrollArea(self)
        self.body_scroll.setWidgetResizable(True)
        self.body_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget(self.body_scroll)
        body.setObjectName("taskBody")
        content = QVBoxLayout(body)
        self._body_layout = content
        content.setContentsMargins(0, 0, 4, 0)
        content.setSpacing(T.SPACE_MD)
        content.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.body_scroll.setWidget(body)
        layout.addWidget(self.body_scroll, 1)
        layout = content

        self.size_panel = WindowSizePanel(body, cfg)
        self.size_panel.config_changed.connect(self._sizes_changed)
        layout.addWidget(self.size_panel)

        self.launch_section = CollapsibleSection("启动与登录", body)
        self.launch_section.toggle.clicked.connect(self._remember_launch_expanded)
        layout.addWidget(self.launch_section)

        self.launch_panel = self.launch_section.content
        launch_layout = QVBoxLayout(self.launch_panel)
        launch_layout.setContentsMargins(0, 0, 0, 0)
        form = QFormLayout()
        path_row = QVBoxLayout()
        self.path = QLineEdit((cfg.get("account_launch") or {}).get("launcher_path", ""), self)
        set_field_width(self.path, "path")
        self.path.editingFinished.connect(self.save_path)
        path_row.addWidget(self.path)
        path_actions = FlowLayout()
        self.browse_button = QPushButton("浏览…", self)
        self.browse_button.clicked.connect(self.browse)
        path_actions.addWidget(self.browse_button)
        self.detect_button = QPushButton("自动检测", self)
        self.detect_button.clicked.connect(self.detect_launcher_requested.emit)
        path_actions.addWidget(self.detect_button)
        path_row.addLayout(path_actions)
        form.addRow("启动器", path_row)
        launch_layout.addLayout(form)
        self.profile_body = QWidget(self)
        self.profile_body.setObjectName("profileBody")
        self.profile_rows = QVBoxLayout(self.profile_body)
        self.profile_rows.setContentsMargins(0, 0, 0, 0)
        launch_actions = FlowLayout()
        self.add_button = QPushButton("新增档案", self)
        self.add_button.clicked.connect(self.add_profile)
        launch_actions.addWidget(self.add_button)
        self.live = QCheckBox("实战模式", self)
        self.live.setChecked(not cfg_mod.task_config(cfg, "launch_login").get("dry_run", True))
        self.live.toggled.connect(self.save_mode)
        launch_actions.addWidget(self.live)
        self.run_button = QPushButton("开始启动登录", self)
        T.set_role(self.run_button, "primary")
        self.run_button.clicked.connect(self.launch_requested.emit)
        launch_actions.addWidget(self.run_button)
        launch_layout.addLayout(launch_actions)
        self.status = QLabel("", self.launch_panel)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        launch_layout.addWidget(self.status)
        launch_layout.addWidget(self.profile_body)
        layout.addStretch()
        self.render_profiles()
        self._update_launch_section()

    def _sizes_changed(self, cfg):
        self.cfg = cfg
        self.config_changed.emit(cfg)

    def _remember_launch_expanded(self, expanded):
        self._launch_expanded = expanded

    def _update_launch_section(self):
        expanded = self._busy or (not self.states if self._launch_expanded is None else self._launch_expanded)
        self.launch_section.set_expanded(expanded)
        # 零窗口时先提供启动入口；登录运行时也让停止按钮在页面最上方。
        first = not self.states or self._busy
        current = self._body_layout.indexOf(self.launch_section)
        expected = 0 if first else self._body_layout.indexOf(self.size_panel) + 1
        if current != expected:
            self._body_layout.removeWidget(self.launch_section)
            self._body_layout.insertWidget(0 if first else self._body_layout.indexOf(self.size_panel) + 1,
                                          self.launch_section)

    def update_windows(self, states, error=""):
        self.states = states
        self._update_launch_section()
        if error:
            self.window_status.setText("检测失败：" + error)
        else:
            self.window_status.setText(f"{len(states)} 个游戏窗口" if states else "未检测到游戏窗口")

    def _save(self, update):
        try:
            cfg = cfg_mod.load_config()
            update(cfg)
            cfg_mod.save_config(cfg)
        except Exception as exc:
            self.status.setText("保存失败：" + str(exc))
            return False
        self.cfg = cfg
        self.config_changed.emit(cfg)
        return True

    def save_path(self):
        path = self.path.text().strip()
        if path != (self.cfg.get("account_launch") or {}).get("launcher_path", ""):
            self._save(lambda cfg: cfg.setdefault("account_launch", {}).update(launcher_path=path))

    def browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择启动器", self.path.text(), "启动程序 (*.exe *.lnk)")
        if path:
            self.path.setText(path)
            self.save_path()

    def save_mode(self, live):
        def update(cfg):
            task = cfg_mod.task_config(cfg, "launch_login")
            task["dry_run"] = not live
            cfg_mod.set_task_config(cfg, "launch_login", task)
        if not self._save(update):
            self.live.blockSignals(True)
            self.live.setChecked(not live)
            self.live.blockSignals(False)

    def render_profiles(self):
        while self.profile_rows.count():
            item = self.profile_rows.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        profiles = (self.cfg.get("account_launch") or {}).get("profiles") or []
        if not profiles:
            self.profile_rows.addWidget(QLabel("暂无启动档案", self.profile_body))
        for profile in profiles:
            row = QWidget(self.profile_body)
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 0, 0, 0)
            box = QCheckBox(profile.get("label") or profile.get("expected_role_name") or "未命名档案", row)
            box.setChecked(profile.get("enabled", True))
            box.toggled.connect(lambda enabled, pid=profile["id"]: self.set_profile_enabled(pid, enabled))
            layout.addWidget(box, 1)
            remove = QPushButton("删除", row)
            T.set_role(remove, "danger")
            remove.clicked.connect(lambda checked=False, pid=profile["id"]: self.delete_profile(pid))
            layout.addWidget(remove)
            self.profile_rows.addWidget(row)
        self.profile_rows.addStretch()

    def set_profile_enabled(self, pid, enabled):
        def update(cfg):
            for profile in cfg["account_launch"]["profiles"]:
                if profile.get("id") == pid:
                    profile["enabled"] = enabled
        self._save(update)

    def add_profile(self):
        name, ok = QInputDialog.getText(self, "新增档案", "目标角色名")
        if not ok or not name.strip():
            return
        def update(cfg):
            cfg.setdefault("account_launch", {}).setdefault("profiles", []).append({
                "id": uuid.uuid4().hex, "label": name.strip(), "enabled": True,
                "expected_role_id": "", "expected_role_name": name.strip(),
            })
        if self._save(update):
            self.render_profiles()

    def delete_profile(self, pid):
        def update(cfg):
            launch = cfg.setdefault("account_launch", {})
            launch["profiles"] = [p for p in launch.get("profiles", []) if p.get("id") != pid]
        if self._save(update):
            self.render_profiles()

    def set_running(self, busy, stopping=False):
        self._busy = busy
        self._stopping = stopping
        self.set_interaction_locked(self._interaction_locked)
        self.run_button.setText("停止中…" if stopping else "停止" if busy else "开始启动登录")
        self._update_launch_section()

    def set_interaction_locked(self, locked):
        self._interaction_locked = locked
        for widget in (self.path, self.browse_button, self.detect_button, self.profile_body,
                       self.add_button, self.live):
            widget.setEnabled(not locked and not self._busy)
        if getattr(self.window(), "_detecting_launcher", False):
            self.detect_button.setEnabled(False)
        self.run_button.setEnabled(not self._stopping if self._busy else not locked)
        self.launch_section.toggle.setEnabled(not self._busy)
