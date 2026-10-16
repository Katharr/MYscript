"""角色与窗口页：保存旧 targets/队长索引，启动仍交给既有运行服务。"""

import uuid

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QLayout, QLineEdit,
    QPushButton, QScrollArea, QVBoxLayout, QWidget,
)
from .widgets import ComboBox as QComboBox, FormLayout as QFormLayout

from ..core import config as cfg_mod
from . import theme as T
from .widgets import FlowLayout, set_field_width
from .window_tools import WindowTools


def target_indices(targets, count, *, apply_limit=False):
    """与 resolve_targets 同序；编辑时保留勾选集，展示实际目标时应用数量上限。"""
    if not count:
        return []
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
        self.boxes = {}
        self._order = []
        self._busy = False
        self.captain_ns = "teaming"
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
        self.window_tools = WindowTools(self)
        layout.addWidget(self.window_tools)
        self.status = QLabel("", self)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.body_scroll = QScrollArea(self)
        self.body_scroll.setWidgetResizable(True)
        self.body_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget(self.body_scroll)
        body.setObjectName("taskBody")
        content = QVBoxLayout(body)
        content.setContentsMargins(0, 0, 4, 0)
        content.setSpacing(T.SPACE_MD)
        content.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.body_scroll.setWidget(body)
        layout.addWidget(self.body_scroll, 1)
        layout = content

        self.window_area = QScrollArea(self)
        self.window_area.setMinimumHeight(140)
        self.window_area.setWidgetResizable(True)
        self.window_body = QWidget(self.window_area)
        self.window_body.setObjectName("windowBody")
        self.window_rows = QVBoxLayout(self.window_body)
        self.window_rows.addStretch()
        self.window_area.setWidget(self.window_body)
        layout.addWidget(self.window_area, 1)
        self.selection_panel = QWidget(self)
        selection = QHBoxLayout(self.selection_panel)
        selection.setContentsMargins(0, 0, 0, 0)
        selection.addWidget(QLabel("组队队长", self))
        self.captain_combo = QComboBox(self)
        selection.addWidget(self.captain_combo, 1)
        self.save_button = QPushButton("保存选择", self)
        T.set_role(self.save_button, "primary")
        self.save_button.clicked.connect(self.save_selection)
        selection.addWidget(self.save_button)
        selection.addStretch(1)
        layout.addWidget(self.selection_panel)
        self.selection_panel.hide()

        self.launch_panel = QWidget(self)
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
        profiles = QScrollArea(self)
        profiles.setWidgetResizable(True)
        profiles.setMinimumHeight(120)
        profiles.setWidget(self.profile_body)
        launch_layout.addWidget(profiles)
        launch_actions = QHBoxLayout()
        self.add_button = QPushButton("新增档案", self)
        self.add_button.clicked.connect(self.add_profile)
        launch_actions.addWidget(self.add_button)
        self.live = QCheckBox("实战模式", self)
        self.live.setChecked(not cfg_mod.task_config(cfg, "launch_login").get("dry_run", True))
        self.live.toggled.connect(self.save_mode)
        launch_actions.addWidget(self.live)
        launch_actions.addStretch()
        self.run_button = QPushButton("开始启动登录", self)
        T.set_role(self.run_button, "primary")
        self.run_button.clicked.connect(self.launch_requested.emit)
        launch_actions.addWidget(self.run_button)
        launch_layout.addLayout(launch_actions)
        layout.addWidget(self.launch_panel, 2)
        self.render_profiles()
        self.save_button.setEnabled(False)

    def update_windows(self, states, error=""):
        self.states = states
        order = [state.hwnd for state in states]
        if order != self._order:
            for box in self.boxes.values():
                self.window_rows.removeWidget(box)
                box.deleteLater()
            selected = target_indices(self.cfg.get("targets") or {}, len(states))
            self.boxes = {}
            for index, state in enumerate(states):
                box = QCheckBox(self.window_body)
                box.setChecked(index in selected)
                box.toggled.connect(self.update_captains)
                self.window_rows.insertWidget(index, box)
                self.boxes[state.hwnd] = box
            self._order = order
            self.update_captains(from_config=True)
        for state in states:
            box = self.boxes[state.hwnd]
            text = state.label + (" · 已最小化" if state.minimized else "")
            if box.text() != text:
                box.setText(text)
            box.setToolTip(text)
        self.update_captains()
        self.save_button.setEnabled(bool(states) and not self._busy and not error)
        self.window_area.setVisible(bool(states))
        self.selection_panel.setVisible(bool(states))
        self.launch_panel.setVisible(not states or self._busy)
        if error:
            self.window_status.setText("检测失败：" + error)
        else:
            self.window_status.setText(f"{len(states)} 个游戏窗口" if states else "未检测到游戏窗口")

    def selected_indices(self):
        return [i for i, state in enumerate(self.states) if self.boxes[state.hwnd].isChecked()]

    def update_captains(self, _checked=False, *, from_config=False):
        selected = self.selected_indices()
        previous = self.captain_combo.currentData()
        if from_config:
            index = cfg_mod.task_config(self.cfg, self.captain_ns).get("captain_index", 0)
            previous = selected[index] if 0 <= index < len(selected) else None
        entries = [(self.states[i].label, i) for i in selected]
        current = [(self.captain_combo.itemText(i), self.captain_combo.itemData(i))
                   for i in range(self.captain_combo.count())]
        if not from_config and current == entries:
            return
        self.captain_combo.clear()
        for i in selected:
            self.captain_combo.addItem(self.states[i].label, i)
        position = self.captain_combo.findData(previous)
        self.captain_combo.setCurrentIndex(max(0, position) if selected else -1)

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

    def save_selection(self):
        indices = self.selected_indices()
        if not indices:
            self.status.setText("请至少勾选 1 个窗口")
            return
        captain = self.captain_combo.currentData()
        captain = captain if captain in indices else indices[0]

        def update(cfg):
            cfg.setdefault("targets", {}).update(
                multi=len(indices) > 1, single_index=indices[0],
                multi_indices=indices if len(indices) != len(self.states) else [],
            )
            task = cfg_mod.task_config(cfg, self.captain_ns)
            task["captain_index"] = indices.index(captain)
            cfg_mod.set_task_config(cfg, self.captain_ns, task)

        if self._save(update):
            self.status.setText("已保存窗口选择")

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
        for widget in (self.path, self.browse_button, self.detect_button, self.profile_body,
                       self.add_button, self.live, self.save_button):
            widget.setEnabled(not busy)
        self.run_button.setText("停止中…" if stopping else "停止" if busy else "开始启动登录")
        self.run_button.setEnabled(not stopping)
        self.save_button.setEnabled(not busy and bool(self.states))
        self.launch_panel.setVisible(not self.states or busy)
