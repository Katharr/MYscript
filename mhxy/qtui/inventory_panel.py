"""通用页背包入口；执行和自动维护策略沿用原任务及 core。"""

import copy

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..core import calibration_assets, config as cfg_mod
from ..tasks.organize_bag import OrganizeBagTask
from .inventory_items_dialog import InventoryItemsDialog
from .task_page import TaskPage
from . import theme as T
from .icons import line_icon
from .widgets import CollapsibleSection, FlowLayout, set_window_summary


class _ConfigTaskPage(TaskPage):
    """清单页的保存、窗口摘要和运行期间编辑门禁。"""

    config_changed = Signal(object)

    def __init__(self, *args, **kwargs):
        self.log_consumption_paused = False
        self._edit_controls = []
        self._editor = None
        self._capturing = False
        self._window_snapshot = None
        super().__init__(*args, **kwargs)
        self.start_requested.connect(lambda: self._set_editable(False))

    def _build_ui(self):
        super()._build_ui()
        self.live_mode = QCheckBox("实战模式", self)
        self.live_mode.setToolTip("默认演练，只识别，不执行物品动作或点击。")
        self.live_mode.toggled.connect(self._save_mode)
        self.add_control("运行模式", self.live_mode)
        self._edit_controls.append(self.live_mode)

    def _busy(self):
        return (self._start_pending or self._runtime_running() or self._capturing
                or self._editor is not None or getattr(self, "_calibration_dialog", None) is not None)

    def refresh_config(self):
        if self._busy():
            return self.cfg
        super().refresh_config()
        tc = cfg_mod.task_config(self.cfg, self.TASK_NAME)
        self.live_mode.blockSignals(True)
        self.live_mode.setChecked(not tc.get("dry_run", True))
        self.live_mode.blockSignals(False)
        if self._window_snapshot is not None:
            self.update_windows(*self._window_snapshot)
        return self.cfg

    def _draft(self):
        return copy.deepcopy(self.cfg) if self._cfg_supplied else cfg_mod.load_config()

    def _commit(self, draft, image=None):
        try:
            if image is not None:
                calibration_assets.commit_image(draft, *image)
            else:
                cfg_mod.save_config(draft)
        except Exception as exc:
            self.log_panel.append(f"保存失败，本次修改未保留：{exc}", "error", self.LOG_SOURCE)
            return False
        self.cfg = draft
        self.config_changed.emit(draft)
        return True

    def _save_mode(self, checked):
        if self._busy():
            return
        draft = self._draft()
        tc = cfg_mod.task_config(draft, self.TASK_NAME)
        tc["dry_run"] = not checked
        cfg_mod.set_task_config(draft, self.TASK_NAME, tc)
        self._commit(draft)
        self.refresh_config()

    def update_windows(self, states, error=""):
        from .role_window_page import target_indices

        self._window_snapshot = (states, error)
        selected = target_indices(self.cfg.get("targets") or {}, len(states), apply_limit=True, states=states)
        labels = [states[i].label for i in selected if states[i].status != "failed"]
        set_window_summary(self.window_status, labels, error)

    def toggle_run(self):
        if self._start_pending or self._runtime_running():
            self.stop()
        elif self._busy():
            self.log_panel.append("请先结束清单编辑或截图。", "warn", self.LOG_SOURCE)
        else:
            super().toggle_run()

    start = toggle_run

    def _set_editable(self, editable):
        for control in self._edit_controls:
            control.setEnabled(editable)
        model = getattr(self, "model", None)
        if model is not None:
            model.editable = editable

    def _start_result(self, ok, problems):
        super()._start_result(ok, problems)
        if not self._closed:
            self._set_editable(not self._busy())

    def _pump(self):
        if not self.log_consumption_paused and self.runner is not None:
            super()._pump()
        if self._visible and not self._closed:
            self._set_editable(not self._busy())

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_config()


class InventoryPanel(_ConfigTaskPage):
    TASK_NAME = "organize_bag"
    TITLE = "通用工具"
    LOG_SOURCE = "整理背包"
    START_TEXT = "整理背包"

    def __init__(self, *args, **kwargs):
        self.tool_task = "organize_bag"
        kwargs.setdefault("task_factory", OrganizeBagTask)
        super().__init__(*args, **kwargs)

    def _build_ui(self):
        super()._build_ui()
        self.auto_organize = QCheckBox("背包满时自动整理", self)
        self.auto_organize.setToolTip("所有任务共享此设置")
        self.auto_organize.toggled.connect(self._save_auto)
        self.add_control("自动整理", self.auto_organize)
        self._edit_controls.append(self.auto_organize)
        self.body.layout().removeItem(self.form)
        self.team_button = QPushButton("一键组队", self)
        self.disband_button = QPushButton("一键解散", self)
        self.organize_button = QPushButton("整理背包", self)
        self.items_button = QPushButton("管理物品", self)
        self.team_button.clicked.connect(lambda: self._start_tool("dungeon"))
        self.disband_button.clicked.connect(lambda: self._start_tool("disband"))
        self.organize_button.clicked.connect(lambda: self._start_tool("organize_bag"))
        self.items_button.clicked.connect(self.open_items)
        self.tool_groups = []
        for title, icon, buttons in (("队伍", "daily", (self.team_button, self.disband_button)),
                                     ("背包", "tasks", (self.organize_button, self.items_button))):
            group = QFrame(self)
            group.setObjectName("toolGroup")
            group_layout = QHBoxLayout(group)
            group_layout.setContentsMargins(16, 16, 16, 16)
            image = QLabel(group)
            image.setPixmap(line_icon(icon).pixmap(20, 20))
            group_layout.addWidget(image)
            label = QLabel(title, group)
            label.setProperty("role", "section")
            group_layout.addWidget(label)
            group_layout.addStretch()
            actions_box = QWidget(group)
            actions_box.setMinimumWidth(180)
            actions = FlowLayout(actions_box)
            for button in buttons:
                actions.addWidget(button)
                self._edit_controls.append(button)
            group_layout.addWidget(actions_box)
            self.content_layout.addWidget(group)
            self.tool_groups.append(group)
        self.team_options = CollapsibleSection("队伍设置", self)
        team_form = QVBoxLayout(self.team_options.content)
        self.team_live_mode = QCheckBox("实战模式", self)
        self.team_live_mode.toggled.connect(self._save_team_mode)
        self.team_calibration_button = QPushButton("标定（队伍）", self)
        self.team_calibration_button.clicked.connect(self._open_team_calibration)
        team_form.addWidget(self.team_live_mode)
        team_form.addWidget(self.team_calibration_button)
        self.bag_options = CollapsibleSection("背包设置", self)
        bag_form = QVBoxLayout(self.bag_options.content)
        bag_form.addLayout(self.form)
        self.calibrate_button = QPushButton("标定（整理背包）", self)
        self.calibrate_button.clicked.connect(self.open_calibration)
        self.refresh_button = QPushButton("刷新配置", self)
        self.refresh_button.clicked.connect(self.refresh_config)
        controls = FlowLayout()
        controls.addWidget(self.calibrate_button)
        controls.addWidget(self.refresh_button)
        bag_form.addLayout(controls)
        self.summary = QLabel(self)
        self.summary.setWordWrap(True)
        bag_form.addWidget(self.summary)
        for section in (self.team_options, self.bag_options):
            T.set_role(section.toggle, "link")
            self.content_layout.addWidget(section)
        self._edit_controls.extend((self.team_live_mode, self.team_calibration_button,
                                    self.calibrate_button, self.refresh_button))
        self.content_layout.addStretch()

    def _save_team_mode(self, checked):
        if self._busy():
            return
        draft = self._draft()
        tc = cfg_mod.task_config(draft, "teaming")
        tc["dry_run"] = not checked
        cfg_mod.set_task_config(draft, "teaming", tc)
        self._commit(draft)
        self.refresh_config()

    def _open_team_calibration(self):
        from .calibration_dialog import open_calibration
        if not self._busy():
            open_calibration(self, "teaming")

    def _start_tool(self, name):
        if self._busy():
            return
        from ..tasks import get_task

        self.tool_task = self.task_name = name
        self.task_factory = get_task(name)
        self.LOG_SOURCE = {"dungeon": "组队", "disband": "解散", "organize_bag": "整理背包"}[name]
        super().toggle_run()

    def _set_status(self, status):
        super()._set_status(status)
        active = status in ("running", "stopping")
        namespace = "teaming" if active and self.tool_task in ("dungeon", "disband") else self.TASK_NAME
        tc = cfg_mod.task_config(self.cfg, namespace)
        live = not tc.get("dry_run", True)
        self.mode_label.setText("实战" if live else "演练")
        T.set_tone(self.mode_label, "warning" if live else "")

    def toggle_run(self):
        if self._start_pending or self._runtime_running():
            self.stop()
        else:
            self._start_tool("organize_bag")

    def refresh_config(self):
        if self._busy():
            return self.cfg
        super().refresh_config()
        tc = cfg_mod.task_config(self.cfg, self.TASK_NAME)
        self.auto_organize.blockSignals(True)
        self.auto_organize.setChecked(bool(tc.get("auto_organize", False)))
        self.auto_organize.blockSignals(False)
        self.summary.setText(f"物品清单：{len(tc.get('items') or [])} 件；"
                             f"背包列表区：{'已标定' if tc.get('regions', {}).get('bag_list') else '整窗检测'}；"
                             f"背包满图标：{'已标定' if tc.get('templates', {}).get('bag_full_icon') else '未标定'}")
        team = cfg_mod.task_config(self.cfg, "teaming")
        self.team_live_mode.blockSignals(True)
        self.team_live_mode.setChecked(not team.get("dry_run", True))
        self.team_live_mode.blockSignals(False)
        self.mode_label.setText("实战" if not tc.get("dry_run", True) else "演练")
        return self.cfg

    def _save_auto(self, checked):
        if self._busy():
            return
        draft = self._draft()
        tc = cfg_mod.task_config(draft, self.TASK_NAME)
        tc["auto_organize"] = bool(checked)
        cfg_mod.set_task_config(draft, self.TASK_NAME, tc)
        self._commit(draft)
        self.refresh_config()

    def open_items(self):
        if self._busy():
            return
        dialog = InventoryItemsDialog(self, self._draft())
        self._editor = dialog
        self._set_editable(False)
        dialog.config_changed.connect(self._items_saved)
        try:
            dialog.exec()
        finally:
            self._editor = None
            dialog.deleteLater()
            self.refresh_config()
            self._set_editable(True)

    def _items_saved(self, cfg):
        self.cfg = cfg
        self.config_changed.emit(cfg)

    def open_calibration(self):
        from .calibration_dialog import open_calibration

        if not self._busy():
            open_calibration(self, self.TASK_NAME)
