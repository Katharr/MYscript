"""刷副本 Qt 页面。

页面只负责选择已注册的副本、编辑该副本的运行选项和展示前置条件；
副本任务、组队握手以及解散流程继续由 ``mhxy.tasks`` 和 ``core.teaming`` 执行。
"""

import copy

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QPushButton, QTextBrowser, QWidget, QVBoxLayout,
)
from .widgets import ComboBox as QComboBox

from ..core import config as cfg_mod
from ..core import leader_history as lh
from ..core import vision
from ..core.teaming import (
    DISBAND_REQUIRED_TEMPLATES,
    TEAM_REQUIRED_REGIONS,
    TEAM_REQUIRED_TEMPLATES,
)
from ..tasks import get_task
from ..tasks.base import dungeon_tasks
from .role_window_page import target_indices
from .task_page import TaskPage
from .widgets import FlowLayout


class LeaderIdSelector(QWidget):
    """显示当前队长 ID 与历史项，并把切换写回既有历史结构。"""

    changed = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.combo = QComboBox(self)
        self.combo.setObjectName("leaderIdCombo")
        self.combo.currentIndexChanged.connect(self._activate)
        row.addWidget(self.combo, 1)
        self.refresh_button = QPushButton("刷新", self)
        self.refresh_button.clicked.connect(self.refresh)
        row.addWidget(self.refresh_button)
        row.addStretch(1)
        layout.addLayout(row)
        self.status = QLabel("暂无队长 ID", self)
        self.status.setProperty("role", "muted")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.cfg = None
        self._loading = False

    def refresh(self, cfg=None):
        if cfg is not None:
            self.cfg = copy.deepcopy(cfg)
        if self.cfg is None:
            self.cfg = cfg_mod.load_config()
        try:
            # 兼容旧配置：只有激活图、没有历史时，由现有历史模块一次性收编。
            lh.initialize(self.cfg)
        except Exception as exc:
            self.status.setText("初始化失败：" + str(exc))
        history = lh.get_history(self.cfg)
        active = lh.get_active_index(self.cfg)
        self._loading = True
        self.combo.clear()
        for index, item in enumerate(history):
            label = str(item.get("label") or item.get("slot") or f"队长 ID {index + 1}")
            prefix = "当前 · " if index == active else "历史 · "
            self.combo.addItem(prefix + label, index)
        if history:
            self.combo.setCurrentIndex(max(0, min(active, len(history) - 1)))
            self.status.setText(f"共 {len(history)} 项，当前第 {active + 1} 项")
        else:
            self.status.setText("暂无队长 ID，请先在组队标定中保存")
        self._loading = False

    def _activate(self, row):
        if self._loading or row < 0 or self.cfg is None:
            return
        index = self.combo.itemData(row)
        if not isinstance(index, int):
            return
        try:
            cfg = copy.deepcopy(self.cfg)
            lh.activate(cfg, index)
        except Exception as exc:
            self.status.setText("切换失败：" + str(exc))
            self.refresh(self.cfg)
            return
        self.cfg = cfg
        self.refresh(cfg)
        self.changed.emit(copy.deepcopy(cfg))


class DungeonPage(TaskPage):
    """副本选择与运行页，任务实例动态来自 ``dungeon_tasks()`` 注册表。"""

    TASK_NAME = "dungeon"
    TITLE = "刷副本"
    LOG_SOURCE = "刷副本"
    START_TEXT = "开始刷副本"
    config_changed = Signal(object)
    calibration_ready = Signal(str)

    def __init__(self, *args, **kwargs):
        self._dungeons = list(dungeon_tasks())
        self._dungeon_names = [task.name for task in self._dungeons]
        self._selected = None
        self._window_snapshot = ([], "")
        self._calibration_dialog = None
        self._summary = ""
        super().__init__(*args, **kwargs)
        self.start_requested.connect(lambda: self._set_editable(False))

    def _build_ui(self):
        super()._build_ui()

        self.dungeon_combo = QComboBox(self)
        self.dungeon_combo.setObjectName("dungeonCombo")
        self.dungeon_combo.currentIndexChanged.connect(self._select_dungeon)
        self.add_control("选择副本", self.dungeon_combo)


        self.live_mode = QCheckBox("实战模式", self)
        self.live_mode.setToolTip("关闭时只做识别自检，不发送游戏输入")
        self.live_mode.toggled.connect(self._save_options)
        self.add_control("运行模式", self.live_mode)

        self.skip_team = QCheckBox("已组队", self)
        self.skip_team.setToolTip("跳过组队，直接开刷")
        self.skip_team.toggled.connect(self._save_options)
        self.add_control("组队方式", self.skip_team)
        self.auto_disband = QCheckBox("跑完解散队伍", self)
        self.auto_disband.setToolTip("所有所选角色退出队伍")
        self.auto_disband.toggled.connect(self._save_options)
        self.add_control("收尾方式", self.auto_disband)

        tools = FlowLayout()
        self.refresh_button = QPushButton("刷新配置", self)
        self.refresh_button.clicked.connect(self.refresh_config)
        tools.addWidget(self.refresh_button)
        self.calibration_button = QPushButton("标定清单 / 状态", self)
        self.calibration_button.clicked.connect(self._open_calibration)
        tools.addWidget(self.calibration_button)
        self.content_layout.addLayout(tools)

        leader_title = QLabel("队长 ID 库", self)
        leader_title.setProperty("role", "muted")
        self.content_layout.addWidget(leader_title)
        self.leader_selector = LeaderIdSelector(self)
        self.leader_selector.changed.connect(self._leader_changed)
        self.content_layout.addWidget(self.leader_selector)

        self.readiness = QLabel("正在检查运行条件…", self)
        self.readiness.setWordWrap(True)
        self.readiness.setTextFormat(Qt.TextFormat.PlainText)
        self.content_layout.addWidget(self.readiness)
        self.calibration_details = QTextBrowser(self)
        self.calibration_details.setReadOnly(True)
        self.calibration_details.setMinimumHeight(120)
        self.content_layout.addWidget(self.calibration_details, 1)
        self.calibration_ready.connect(self._apply_summary, Qt.ConnectionType.QueuedConnection)

    @property
    def selected_task(self):
        if not self._selected:
            return None
        for task in self._dungeons:
            if getattr(task, "name", None) == self._selected:
                return task
        return get_task(self._selected)

    def refresh_config(self):
        if getattr(self, "_start_pending", False) or self._runtime_running():
            return self.cfg
        if not self._cfg_supplied:
            self.cfg = cfg_mod.load_config()
        selection = getattr(self.window(), "selection", None)
        if selection is not None:
            selection.write(self.cfg)
        hub = cfg_mod.task_config(self.cfg, self.TASK_NAME)
        selected = hub.get("selected")
        if selected not in self._dungeon_names:
            selected = self._dungeon_names[0] if self._dungeon_names else None
        self._selected = selected
        task = self.selected_task
        self.task_name = selected or self.TASK_NAME
        self.task_factory = task
        self._populate_dungeons()
        tc = cfg_mod.task_config(self.cfg, self._selected) if self._selected else {}
        for control, value in ((self.live_mode, not tc.get("dry_run", True)),
                               (self.skip_team, bool(tc.get("skip_team", False))),
                               (self.auto_disband, bool(tc.get("auto_disband", False)))):
            control.blockSignals(True)
            control.setChecked(value)
            control.blockSignals(False)
        self.mode_label.setText("实战" if self.live_mode.isChecked() else "演练")
        self.leader_selector.refresh(self.cfg)
        if self._window_snapshot:
            self.update_windows(*self._window_snapshot)
        else:
            self._render_readiness()
        return self.cfg

    def _populate_dungeons(self):
        if not hasattr(self, "dungeon_combo"):
            return
        current = self._selected
        self.dungeon_combo.blockSignals(True)
        self.dungeon_combo.clear()
        for task in self._dungeons:
            self.dungeon_combo.addItem(str(task.title), task.name)
        row = self.dungeon_combo.findData(current)
        self.dungeon_combo.setCurrentIndex(row if row >= 0 else (-1 if not self._dungeons else 0))
        self.dungeon_combo.blockSignals(False)

    def _save_options(self, _checked=False):
        if self._start_pending or self._runtime_running() or not self._selected:
            return False
        try:
            cfg = copy.deepcopy(self.cfg) if self._cfg_supplied else cfg_mod.load_config()
            tc = cfg_mod.task_config(cfg, self._selected)
            tc["dry_run"] = not self.live_mode.isChecked()
            tc["skip_team"] = self.skip_team.isChecked()
            tc["auto_disband"] = self.auto_disband.isChecked()
            cfg_mod.set_task_config(cfg, self._selected, tc)
            cfg_mod.save_config(cfg)
        except Exception as exc:
            self.readiness.setText("保存失败：" + str(exc))
            return False
        self.cfg = cfg
        self.mode_label.setText("实战" if self.live_mode.isChecked() else "演练")
        self.config_changed.emit(copy.deepcopy(cfg))
        self._render_readiness()
        return True

    def _select_dungeon(self, row):
        if row < 0 or self._start_pending or self._runtime_running():
            return
        selected = self.dungeon_combo.itemData(row)
        if selected not in self._dungeon_names or selected == self._selected:
            return
        try:
            cfg = copy.deepcopy(self.cfg) if self._cfg_supplied else cfg_mod.load_config()
            hub = cfg_mod.task_config(cfg, self.TASK_NAME)
            hub["selected"] = selected
            cfg_mod.set_task_config(cfg, self.TASK_NAME, hub)
            cfg_mod.save_config(cfg)
        except Exception as exc:
            self.readiness.setText("切换副本失败：" + str(exc))
            self.refresh_config()
            return
        self.cfg = cfg
        self._selected = selected
        self.task_name = selected
        self.task_factory = self.selected_task
        self.refresh_config()
        self.log_panel.append(f"已切换副本：{self._selected_title()}", "info", self.LOG_SOURCE)
        self.config_changed.emit(copy.deepcopy(cfg))

    def _selected_title(self):
        task = self.selected_task
        return str(getattr(task, "title", self._selected or "（无副本）"))

    def _leader_changed(self, cfg):
        self.cfg = cfg
        self.config_changed.emit(copy.deepcopy(cfg))
        self._render_readiness()

    def update_windows(self, states, error=""):
        self._window_snapshot = (states, error)
        indices = target_indices(self.cfg.get("targets") or {}, len(states), apply_limit=True, states=states)
        self._selected_count = len(indices)
        captain_hwnd = (self.cfg.get("targets") or {}).get("captain_hwnd")
        self._captain_label = next((state.label for state in states if state.hwnd == captain_hwnd), "—")
        if error:
            self.window_status.setText("窗口检测失败：" + str(error))
        elif not indices:
            self.window_status.setText("未选目标窗口")
        else:
            self.window_status.setText("已选 " + str(len(indices)) + " 个目标窗口")
        self._render_readiness()

    def _required_calibration(self):
        task = self.selected_task
        tc = cfg_mod.task_config(self.cfg, self._selected) if self._selected else {}
        spec = getattr(task, "CALIBRATION", {}) or {}
        regions = tc.get("regions", {})
        templates = tc.get("templates", {})
        required_regions = [entry[0] for entry in spec.get("regions", [])
                            if not (len(entry) >= 4 and entry[3])]
        required_templates = [entry[0] for entry in spec.get("templates", [])]
        rdone = sum(bool(regions.get(key)) for key in required_regions)
        tdone = sum(self._template_ready(templates.get(key)) for key in required_templates)
        return rdone, len(required_regions), tdone, len(required_templates)

    def _team_calibration(self):
        team = cfg_mod.task_config(self.cfg, "teaming")
        regions, templates = team.get("regions", {}), team.get("templates", {})
        rdone = sum(bool(regions.get(key)) for key in TEAM_REQUIRED_REGIONS)
        tdone = sum(self._template_ready(templates.get(key)) for key in TEAM_REQUIRED_TEMPLATES)
        return rdone, len(TEAM_REQUIRED_REGIONS), tdone, len(TEAM_REQUIRED_TEMPLATES)

    @staticmethod
    def _template_ready(path):
        if not path:
            return False
        try:
            return vision.load_template(path) is not None
        except Exception:
            return False

    def _render_readiness(self):
        if not hasattr(self, "readiness"):
            return
        n = getattr(self, "_selected_count", 0)
        if not self._selected:
            text = "未收录副本：请先注册一个 is_dungeon=True 的副本任务。"
            self.readiness.setText(text)
            self.calibration_details.setPlainText(text)
            return
        rdone, rneed, tdone, tneed = self._required_calibration()
        trdone, trneed, ttdone, ttneed = self._team_calibration()
        tc = cfg_mod.task_config(self.cfg, self._selected)
        lines = [f"副本标定（{self._selected_title()}）：区域 {rdone}/{rneed}，模板 {tdone}/{tneed}"]
        skip = bool(tc.get("skip_team", False))
        if skip:
            lines.append("组队标定：已组队，本次跳过组队（无需组队标定）")
        else:
            lines.append(f"组队标定：区域 {trdone}/{trneed}，模板 {ttdone}/{ttneed}")
        lines.append(f"已选 {n} 个号，队长={getattr(self, '_captain_label', '—')}")
        if not n:
            summary = "未选窗口：请先在顶部选择目标窗口。"
        elif not skip and n < 2:
            summary = "未选窗口：组队需要至少 2 个目标窗口。"
        elif not skip and (trdone < trneed or ttdone < ttneed):
            summary = "缺组队标定：请在组队标定中补齐必要区域和模板。"
        elif rdone < rneed or tdone < tneed:
            summary = "缺副本标定：请在本页查看标定清单并补齐必要项目。"
        elif tc.get("auto_disband") and not self._has_disband_template():
            summary = "缺解散标定：已勾选跑完解散，但未找到退出队伍模板。"
        else:
            summary = "可运行：启动检查还会再次确认窗口、组队和副本条件。"
        self._summary = summary + "\n" + "\n".join(lines)
        self.readiness.setText(summary)
        self.calibration_details.setPlainText(self._summary)

    @Slot(str)
    def _apply_summary(self, summary):
        """把后台或测试桥接传回的摘要安全地落到主线程控件。"""
        if self._closed:
            return
        self._summary = str(summary)
        self.readiness.setText(self._summary.split("\n", 1)[0])
        self.calibration_details.setPlainText(self._summary)

    def _has_disband_template(self):
        templates = cfg_mod.task_config(self.cfg, "teaming").get("templates", {})
        return all(self._template_ready(templates.get(key)) for key in DISBAND_REQUIRED_TEMPLATES)

    def _open_calibration(self, *args):
        from .calibration_dialog import open_calibration

        open_calibration(self)

    def _make_runner(self):
        self.refresh_config()
        if self.selected_task is None:
            raise ValueError("未收录可运行的副本任务")
        self.task_name = self._selected
        self.task_factory = self.selected_task
        return super()._make_runner()

    def _set_editable(self, enabled):
        for widget in (self.dungeon_combo, self.live_mode,
                       self.skip_team, self.auto_disband, self.refresh_button,
                       self.calibration_button, self.leader_selector):
            widget.setEnabled(bool(enabled))

    @Slot(bool, object)
    def _start_result(self, ok, problems):
        super()._start_result(ok, problems)
        if not self._closed:
            self._set_editable(not self._start_pending and not self._runtime_running())

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_config()

