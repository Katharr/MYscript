"""活动任务共用的 Qt 布局和宝图页；运行仍交给原 TaskPage / 任务类。"""

import copy
import threading

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QLabel, QPushButton, QTextBrowser,
    QVBoxLayout, QWidget,
)

from ..core import config as cfg_mod
from ..core import vision
from ..core.context import TaskContext
from ..core.input_session import InputSession
from ..tasks.treasure_map import TreasureMapTask
from . import theme as T
from .task_page import TaskPage
from .widgets import DoubleSpinBox as QDoubleSpinBox, SpinBox as QSpinBox, FlowLayout, set_window_summary


class _ActivityPage(TaskPage):
    """仅复用 UI，不合并任务配置、识别或状态机。"""

    TASK_CLASS = None
    # (字段, 标签, 默认值, 最小值, 是否整数)。识别参数没有编辑时不回写。
    PARAMETERS = ()
    REQUIRED_TEMPLATES = ()
    config_changed = Signal(object)
    calibration_ready = Signal(int, object, str)

    def __init__(self, *args, **kwargs):
        self._calibration_generation = 0
        self._calibration_text = "正在检查标定…"
        self._calibration_dialog = None
        self._window_snapshot = None
        self.log_consumption_paused = False
        super().__init__(*args, **kwargs)
        self.start_requested.connect(lambda: self._set_editable(False))

    def _build_ui(self):
        super()._build_ui()
        self.calibration_ready.connect(self._apply_calibration, Qt.ConnectionType.QueuedConnection)
        self.live_mode = QCheckBox("实战模式", self)
        self.live_mode.setToolTip("关闭时只识别自检，不发送游戏输入")
        self.live_mode.toggled.connect(self._save_edits)
        self.add_control("运行模式", self.live_mode)
        self.skip_collect = None
        if self.TASK_NAME == "treasure_map":
            self.skip_collect = QCheckBox("已有宝图", self)
            self.skip_collect.setToolTip("跳过领取，直接开背包挖图")
            self.skip_collect.toggled.connect(self._save_edits)
            self.add_control("宝图来源", self.skip_collect)
        self.parameters = {}
        self._parameter_values = {}
        for key, label, default, minimum, integer in self.PARAMETERS:
            control = QSpinBox(self) if integer else QDoubleSpinBox(self)
            control.setRange(minimum, 2147483647 if integer else 999999999)
            if not integer:
                control.setDecimals(1)
            if key == "time_limit_min":
                control.setSuffix(" 分钟")
                control.setSpecialValueText("不限")
            elif key.endswith("_sec"):
                control.setSuffix(" 秒")
            control.setToolTip(label)
            control.setKeyboardTracking(False)
            control.editingFinished.connect(self._save_edits)
            self.parameters[key] = control
            self.add_control(label.partition("（")[0], control)

        body = QWidget(self)
        body.setObjectName("activityBody")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        card = QFrame(body)
        card.setObjectName("activityCard")
        card_layout = QVBoxLayout(card)
        card_layout.setSpacing(T.SPACE_SM)
        card_layout.addWidget(QLabel("标定与运行条件", card))
        tools = FlowLayout()
        self.calibration_button = QPushButton("标定区域与模板", card)
        self.calibration_button.clicked.connect(self._open_calibration)
        tools.addWidget(self.calibration_button)
        self.refresh_button = QPushButton("刷新配置", card)
        self.refresh_button.clicked.connect(self.refresh_config)
        tools.addWidget(self.refresh_button)
        card_layout.addLayout(tools)
        self.calibration_status = QLabel("正在检查标定…", card)
        self.calibration_status.setWordWrap(True)
        self.calibration_status.setTextFormat(Qt.TextFormat.PlainText)
        card_layout.addWidget(self.calibration_status)
        self.calibration_details = QTextBrowser(card)
        self.calibration_details.setMinimumHeight(80)
        self.calibration_details.setMaximumHeight(160)
        card_layout.addWidget(self.calibration_details)
        body_layout.addWidget(card)
        body_layout.addStretch()
        self.content_layout.addWidget(body, 1)

    def refresh_config(self):
        if self._start_pending or self._runtime_running():
            return self.cfg
        super().refresh_config()
        tc = cfg_mod.task_config(self.cfg, self.TASK_NAME)
        self.live_mode.blockSignals(True)
        self.live_mode.setChecked(not tc.get("dry_run", True))
        self.live_mode.blockSignals(False)
        if self.skip_collect is not None:
            self.skip_collect.blockSignals(True)
            self.skip_collect.setChecked(bool(tc.get("skip_collect", False)))
            self.skip_collect.blockSignals(False)
        for key, label, default, minimum, integer in self.PARAMETERS:
            control = self.parameters[key]
            control.setValue(tc.get("loop", {}).get(key, default))
            self._parameter_values[key] = control.value()
        if self._window_snapshot is not None:
            self.update_windows(*self._window_snapshot)
        self._request_calibration()
        return self.cfg

    def _save_edits(self, *args):
        if self._start_pending or self._runtime_running():
            return False
        try:
            # 只覆盖用户在本页修改的字段；保留其它页面刚保存的标定和参数。
            cfg = cfg_mod.load_config()
            tc = cfg_mod.task_config(cfg, self.TASK_NAME)
            tc["dry_run"] = not self.live_mode.isChecked()
            if self.skip_collect is not None:
                tc["skip_collect"] = self.skip_collect.isChecked()
            loop = tc.setdefault("loop", {})
            for key, control in self.parameters.items():
                if control.value() != self._parameter_values[key]:
                    loop[key] = control.value()
            cfg_mod.set_task_config(cfg, self.TASK_NAME, tc)
            cfg_mod.save_config(cfg)
        except Exception as exc:
            self.calibration_status.setText("保存失败：" + str(exc))
            return False
        self.cfg = cfg
        self._parameter_values = {key: control.value() for key, control in self.parameters.items()}
        self.mode_label.setText("演练" if tc["dry_run"] else "实战")
        self.config_changed.emit(cfg)
        self._request_calibration()
        return True

    def _required_assets(self, tc):
        return {"activity_list"}, set(self.REQUIRED_TEMPLATES)

    def _request_calibration(self):
        self._calibration_generation += 1
        generation = self._calibration_generation
        cfg, signal, task_cls = copy.deepcopy(self.cfg), self.calibration_ready, self.TASK_CLASS
        tc = cfg_mod.task_config(cfg, self.TASK_NAME)
        need_regions, need_templates = self._required_assets(tc)
        self.calibration_status.setText("正在后台检查标定与运行条件…")

        def work():
            try:
                cancel = threading.Event()
                session = InputSession(cfg, cancel, task_cls.name)
                ctx = TaskContext(cfg, stop_event=cancel, input_session=session)
                with session.activate(preflight=True):
                    ok, problems = task_cls().preflight(ctx)
                    lines, missing = [], 0
                    for kind, required in (("regions", need_regions), ("templates", need_templates)):
                        for entry in task_cls.CALIBRATION[kind]:
                            key, label = entry[:2]
                            value = tc.get(kind, {}).get(key)
                            present = bool(value) if kind == "regions" else (
                                bool(value) and vision.load_template(value) is not None)
                            necessary = key in required
                            missing += int(necessary and not present)
                            state = "已标定" if present else (
                                "缺少标定" if necessary else "整窗检测" if key == "scene" else "可选 / 未使用")
                            lines.append(f"{'必需' if necessary else '可选'} · {label}：{state}"
                                         + (f"\n  {value}" if value else ""))
                    lines.extend(["", "启动检查：" + ("通过" if ok else "未通过"), *problems])
                    result = {"text": "\n".join(lines), "missing": missing, "ok": ok}
                error = ""
            except Exception as exc:
                result, error = {}, str(exc)
            try:
                signal.emit(generation, result, error)
            except RuntimeError:
                pass

        threading.Thread(target=work, name=f"mhxy-{self.TASK_NAME}-calibration", daemon=True).start()

    @Slot(int, object, str)
    def _apply_calibration(self, generation, result, error):
        if self._closed or generation != self._calibration_generation:
            return
        self._calibration_text = error or result["text"]
        if error:
            summary = "检查失败：" + error
        elif result["missing"]:
            summary = f"缺少 {result['missing']} 项必要标定；请查看清单。"
        else:
            summary = "必要标定齐全；" + ("启动检查通过。" if result["ok"] else "仍有启动条件未满足。")
        self.calibration_status.setText(summary)
        self.calibration_details.setPlainText(self._calibration_text)

    def _open_calibration(self, *args):
        from .calibration_dialog import open_calibration

        open_calibration(self)

    def update_windows(self, states, error=""):
        from .role_window_page import target_indices

        self._window_snapshot = (states, error)
        indices = target_indices(self.cfg.get("targets") or {}, len(states), apply_limit=True)
        labels = [states[i].label for i in indices if states[i].status != "failed"]
        set_window_summary(self.window_status, labels, error)

    def toggle_run(self):
        if self._start_pending or self._runtime_running():
            self.stop()
        elif self._save_edits():
            super().toggle_run()

    start = toggle_run

    def _set_editable(self, editable):
        for widget in (self.live_mode, self.skip_collect, self.refresh_button, *self.parameters.values()):
            if widget is not None:
                widget.setEnabled(editable)

    def _start_result(self, ok, problems):
        super()._start_result(ok, problems)
        if not self._closed:
            self._set_editable(not self._start_pending and not self._runtime_running())

    def _pump(self):
        # 嵌入主窗口时由窗口统一分发日志，独立页面仍复用 TaskPage 的消费方式。
        if not self.log_consumption_paused and self.runner is not None:
            super()._pump()
        if self._visible and not self._closed:
            self._set_editable(not self._start_pending and not self._runtime_running())

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_config()


class TreasureMapPage(_ActivityPage):
    TASK_NAME = "treasure_map"
    TITLE = "宝图"
    LOG_SOURCE = "宝图"
    START_TEXT = "开始刷宝图"
    TASK_CLASS = TreasureMapTask
    PARAMETERS = (
        ("time_limit_min", "时间上限（分钟，0 = 不限）", 30, 0, False),
        ("still_diff", "静止判定阈值（收集 / 挖宝完成）", 8.0, 0.5, False),
    )

    def _required_assets(self, tc):
        regions = {"bag_list"}
        templates = {"flag_next_map", "treasure_item"}
        if not tc.get("skip_collect", False):
            regions.add("activity_list")
            templates.update(("flag_treasure_entry", "flag_join", "flag_tingting"))
        return regions, templates
