"""日常一条龙：配置串联顺序，后台复用子任务 preflight 显示本次计划。"""

import copy
import threading
from types import SimpleNamespace

from PySide6.QtCore import QEvent, QRect, QSize, Qt, Signal, Slot
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QPushButton, QTextBrowser, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QWidget,
)

from ..core import config as cfg_mod
from ..core.context import TaskContext
from ..core.input_session import InputSession
from ..tasks.daily import CHAINABLE, DailyTask
from . import theme as T
from .icons import line_icon
from .task_page import TaskPage
from .widgets import DoubleSpinBox as QDoubleSpinBox, FlowLayout


class _DailyRowDelegate(QStyledItemDelegate):
    """保留原生选择、勾选与排序，仅把任务信息按列绘制。"""

    ROW_ROLE = int(Qt.ItemDataRole.UserRole) + 1
    mode_requested = Signal(int)
    configure_requested = Signal(int)

    @staticmethod
    def action_rects(rect):
        configure = QRect(rect.right() - 39, rect.y() + 14, 30, rect.height() - 28)
        status = QRect(configure.left() - 108, rect.y(), 104, rect.height())
        mode = QRect(status.left() - 76, rect.y() + 17, 68, rect.height() - 34)
        return mode, status, configure

    def editorEvent(self, event, model, option, index):
        if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            mode, _, configure = self.action_rects(option.rect)
            if mode.contains(event.position().toPoint()):
                self.mode_requested.emit(index.row())
                return True
            if configure.contains(event.position().toPoint()):
                self.configure_requested.emit(index.row())
                return True
        return super().editorEvent(event, model, option, index)

    def sizeHint(self, option, index):
        return QSize(240, max(T.ROW_HEIGHT, option.fontMetrics.height() + 24))

    def paint(self, painter, option, index):
        row = index.data(self.ROW_ROLE)
        if not row:
            return super().paint(painter, option, index)
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        style = opt.widget.style()
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        opt.state &= ~QStyle.StateFlag.State_Selected
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        area = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, opt, opt.widget).adjusted(8, 0, -8, 0)
        mode, status, configure = self.action_rects(option.rect)
        painter.save()
        painter.setClipRect(option.rect)
        painter.setPen(QColor(T.TEXT_DIM))
        painter.drawText(QRect(area.x(), area.y(), 22, area.height()), Qt.AlignmentFlag.AlignVCenter, row[0])
        key = index.data(Qt.ItemDataRole.UserRole)
        badge = QRect(area.x() + 26, option.rect.y() + (option.rect.height() - 32) // 2, 32, 32)
        painter.setPen(Qt.PenStyle.NoPen)
        ink, fill = T.TASK_TONES.get(key, (T.ACCENT, T.ROLE_SELECTED))
        painter.setBrush(QColor(fill))
        painter.drawRoundedRect(badge, 5, 5)
        line_icon(key, ink, 21).paint(painter, badge.adjusted(5, 5, -5, -5))
        name_rect = QRect(badge.right() + 12, area.y(), max(24, mode.left() - badge.right() - 20), area.height())
        painter.setPen(QColor(T.TEXT))
        painter.drawText(name_rect, Qt.AlignmentFlag.AlignVCenter,
                         opt.fontMetrics.elidedText(row[1], Qt.TextElideMode.ElideRight, name_rect.width()))
        painter.setPen(QColor(T.BORDER))
        painter.setBrush(QColor(T.AUX_SURFACE))
        painter.drawRoundedRect(mode, 4, 4)
        painter.setPen(QColor(T.WARNING if row[2] == "实战" else T.TEXT_DIM))
        painter.drawText(mode.adjusted(0, 0, -10, 0), Qt.AlignmentFlag.AlignCenter, row[2])
        x, y = mode.right() - 12, mode.center().y()
        painter.drawLine(x - 3, y - 1, x, y + 2)
        painter.drawLine(x, y + 2, x + 3, y - 1)
        painter.setPen(QColor(T.WARNING if row[3] in ("缺少标定", "未选窗口") else T.TEXT_DIM))
        painter.drawText(status, Qt.AlignmentFlag.AlignCenter,
                         opt.fontMetrics.elidedText(row[3], Qt.TextElideMode.ElideRight, status.width()))
        line_icon("settings", T.ACCENT if selected else T.TEXT_DIM).paint(painter, configure.adjusted(5, 5, -5, -5))
        if selected:
            painter.setPen(QColor(T.ACCENT))
            painter.drawLine(option.rect.left(), option.rect.top() + 12, option.rect.left(), option.rect.bottom() - 12)
        painter.restore()


def normalize_steps(stored):
    """保留已存顺序与开关，过滤非法/重复项，缺项补到末尾。"""
    steps, seen = [], set()
    for step in stored or []:
        if isinstance(step, dict) and step.get("task") in CHAINABLE and step["task"] not in seen:
            steps.append({"task": step["task"], "enabled": bool(step.get("enabled", True))})
            seen.add(step["task"])
    steps.extend({"task": name, "enabled": True} for name in CHAINABLE if name not in seen)
    return steps


def summarize_daily(ctx, steps):
    """只读计划；解析副本、演练优先跳过、真实 preflight 均沿用原任务。"""
    daily = DailyTask()
    wins = ctx.select_windows()
    rows = []
    for step in steps:
        name = step["task"]
        effective, task_cls = daily._resolve(name, ctx)
        dry = ctx.task_cfg(effective).get("dry_run", True) if effective else True
        row = dict(step, title=daily._title_of(name, ctx),
                   mode="演练" if dry else "实战", result="", problems=[])
        if not step["enabled"]:
            row["result"] = "已停用"
        elif task_cls is None:
            row.update(result="缺少标定", problems=["未选/未收录可运行的任务，请检查对应任务页"])
        elif name != "dungeon" and not getattr(task_cls, "CHAINS_PER_WINDOW", False):
            row.update(result="缺少标定", problems=["该任务不支持每窗口独立链，将跳过"])
        elif dry:
            # 与 DailyTask 一致：演练不做自检、不建驱动器，直接跳过。
            row.update(result="演练将跳过", problems=["一条龙不运行演练任务；请在对应任务页切换实战"])
        else:
            ok, problems = task_cls().preflight(ctx)
            row.update(result="未选窗口" if not wins else "将执行" if ok else "缺少标定",
                       problems=list(problems or []))
            if not wins and not row["problems"]:
                row["problems"] = ["没找到/没选中目标窗口，请先在「角色与窗口」中选择"]
        rows.append(row)
    return rows, len(wins)


class _PlannedDailyTask(DailyTask):
    """仅在原启动检查前报告计划；继承原 run，不改变编排和跳过策略。"""

    def __init__(self, report):
        self._report = report

    def preflight(self, ctx):
        rows, count = summarize_daily(ctx, ctx.task_cfg(self.name).get("steps", []))
        self._report(rows, count)
        return super().preflight(ctx)


class DailyPage(TaskPage):
    TASK_NAME = "daily"
    TITLE = "日常一条龙"
    LOG_SOURCE = "一条龙"
    START_TEXT = "开始一条龙"
    # 仅发送 Python 数据，所有控件操作都在主线程的 slot 中执行。
    summary_ready = Signal(int, object, int, str)
    config_changed = Signal(object)

    def __init__(self, *args, **kwargs):
        self._summary_generation = 0
        self._summary_rows = {}
        self.log_consumption_paused = False
        super().__init__(*args, **kwargs)
        self.start_requested.connect(lambda: self._set_editable(False))

    def _build_ui(self):
        super()._build_ui()
        self.summary_ready.connect(self._apply_summary, Qt.ConnectionType.QueuedConnection)
        self.time_limit = QDoubleSpinBox(self)
        self.time_limit.setRange(0, 999999)
        self.time_limit.setDecimals(1)
        self.time_limit.setSuffix(" 分钟")
        self.time_limit.setSpecialValueText("不限")
        self.time_limit.editingFinished.connect(self._save_edits)
        self.refresh_button = QPushButton("刷新配置", self)
        self.refresh_button.setToolTip("重新读取配置并在后台检查本次执行条件")
        self.refresh_button.clicked.connect(self.refresh_config)
        T.set_role(self.refresh_button, "link")
        self.layout().itemAt(0).layout().insertWidget(1, self.refresh_button)
        self.content_layout.setSpacing(T.SPACE_SM)
        self.steps_list = QListWidget(self)
        self.steps_list.setObjectName("dailySteps")
        delegate = _DailyRowDelegate(self.steps_list)
        delegate.mode_requested.connect(self._toggle_row_mode)
        delegate.configure_requested.connect(self._configure_row)
        self.steps_list.setItemDelegate(delegate)
        self.steps_list.setToolTip("拖动任务行排序，勾选启用。模式与参数沿用各任务页。")
        self.steps_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.steps_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.steps_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.steps_list.setSpacing(0)
        self.steps_list.setMinimumHeight(220)
        self.steps_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.steps_list.itemChanged.connect(self._steps_changed)
        self.steps_list.model().rowsMoved.connect(self._steps_changed)
        self.steps_list.currentItemChanged.connect(self._show_details)
        self.content_layout.addWidget(self.steps_list, 1)
        actions_widget = QWidget(self)
        actions = FlowLayout(actions_widget)
        self.configure_button = QPushButton("配置", self)
        self.configure_button.clicked.connect(self._configure_step)
        T.set_role(self.configure_button, "link")
        self.move_up_button = QPushButton("上移", self)
        self.move_down_button = QPushButton("下移", self)
        T.set_role(self.move_up_button, "link")
        T.set_role(self.move_down_button, "link")
        self.move_up_button.clicked.connect(lambda: self._move_step(-1))
        self.move_down_button.clicked.connect(lambda: self._move_step(1))
        self.step_live_mode = QCheckBox("实战模式", self)
        self.step_live_mode.toggled.connect(self._save_step_mode)
        for widget in (self.configure_button, self.move_up_button, self.move_down_button, self.step_live_mode):
            actions.addWidget(widget)
        self.content_layout.addWidget(actions_widget)
        actions_widget.hide()
        self.order_button = QPushButton("调整顺序", self)
        self.order_button.setCheckable(True)
        T.set_role(self.order_button, "link")
        self.order_button.toggled.connect(actions_widget.setVisible)
        self.steps_list.itemActivated.connect(lambda *_: self._configure_step())
        time_row = QHBoxLayout()
        time_row.addWidget(QLabel("整体时间上限", self))
        time_row.addWidget(self.time_limit)
        time_row.addStretch()
        time_row.addWidget(self.order_button)
        self.content_layout.addLayout(time_row)
        self.summary_label = QLabel("尚未检查", self)
        self.summary_label.setWordWrap(True)
        self.content_layout.addWidget(self.summary_label)
        self.details = QTextBrowser(self)
        self.details.setMinimumHeight(80)
        self.details.setMaximumHeight(120)
        self.details.hide()
        self.details.setPlaceholderText("选择任务行查看具体缺失项")
        self.content_layout.addWidget(self.details)

    def refresh_config(self):
        if self._start_pending or self._runtime_running():
            return self.cfg
        super().refresh_config()
        self.mode_label.setText("沿用子任务模式")
        tc = cfg_mod.task_config(self.cfg, self.TASK_NAME)
        self.steps_list.blockSignals(True)
        self.steps_list.clear()
        for step in normalize_steps(tc.get("steps", [])):
            item = QListWidgetItem(self.steps_list)
            item.setData(Qt.ItemDataRole.UserRole, step["task"])
            item.setFlags((item.flags() | Qt.ItemFlag.ItemIsUserCheckable) & ~Qt.ItemFlag.ItemIsDropEnabled)
            item.setCheckState(Qt.CheckState.Checked if step["enabled"] else Qt.CheckState.Unchecked)
        self.steps_list.blockSignals(False)
        self.steps_list.setFixedHeight(self.steps_list.count() * max(T.ROW_HEIGHT, self.steps_list.fontMetrics().height() + 24) + 2)
        self.time_limit.setValue(float(tc.get("loop", {}).get("time_limit_min", 0) or 0))
        self._request_summary()
        return self.cfg

    def _steps(self):
        return [{"task": self.steps_list.item(i).data(Qt.ItemDataRole.UserRole),
                 "enabled": self.steps_list.item(i).checkState() == Qt.CheckState.Checked}
                for i in range(self.steps_list.count())]

    def _save_edits(self):
        if self._start_pending or self._runtime_running():
            return False
        try:
            # 读盘后只覆盖本页字段，避免覆盖其它任务刚保存的参数。
            cfg = cfg_mod.load_config()
            tc = cfg_mod.task_config(cfg, self.TASK_NAME)
            tc["steps"] = self._steps()
            tc.setdefault("loop", {})["time_limit_min"] = self.time_limit.value()
            cfg_mod.set_task_config(cfg, self.TASK_NAME, tc)
            cfg_mod.save_config(cfg)
        except Exception as exc:
            self.summary_label.setText("保存失败：" + str(exc))
            return False
        self.cfg = cfg
        self.config_changed.emit(cfg)
        return True

    def _steps_changed(self, *args):
        if self._save_edits():
            self._request_summary()

    def _render_rows(self):
        self.steps_list.blockSignals(True)
        order = 0
        for i, step in enumerate(self._steps()):
            item = self.steps_list.item(i)
            row = self._summary_rows.get(step["task"], {})
            order += int(step["enabled"])
            number = str(order) if step["enabled"] else "·"
            item.setData(_DailyRowDelegate.ROW_ROLE, [number, str(row.get("title", step["task"])),
                         str(row.get("mode", "检查中…")),
                         str(row.get("result", "检查中…") if step["enabled"] else "已停用")])
            item.setText(f"{number}  {row.get('title', step['task'])}  ·  "
                         f"{row.get('mode', '检查中…')}  ·  "
                         f"{row.get('result', '检查中…') if step['enabled'] else '已停用'}")
            item.setToolTip("\n".join([str(row.get("title", step["task"])), *row.get("problems", [])]))
        self.steps_list.blockSignals(False)

    def _request_summary(self):
        self._summary_generation += 1
        generation = self._summary_generation
        self._summary_rows = {}
        self._render_rows()
        self.details.clear()
        self.summary_label.setText("正在后台检查本次计划…")
        cfg, steps = copy.deepcopy(self.cfg), self._steps()
        signal = self.summary_ready

        def work():
            try:
                cancel = threading.Event()
                session = InputSession(cfg, cancel, "daily")
                ctx = TaskContext(cfg, stop_event=cancel, input_session=session)
                with session.activate(preflight=True):
                    rows, count = summarize_daily(ctx, steps)
                error = ""
            except Exception as exc:
                rows, count, error = [], 0, str(exc)
            try:
                signal.emit(generation, rows, count, error)
            except RuntimeError:
                pass

        threading.Thread(target=work, name="mhxy-daily-summary", daemon=True).start()

    @Slot(int, object, int, str)
    def _apply_summary(self, generation, rows, count, error):
        if self._closed or generation != self._summary_generation:
            return
        if error:
            self.summary_label.setText("计划检查失败：" + error)
            self.details.setPlainText(error)
            return
        self._summary_rows = {row["task"]: row for row in rows}
        self._render_rows()
        self.window_status.setText(f"已选择 {count} 个窗口" if count else "未选窗口")
        execute = sum(row["result"] == "将执行" for row in rows)
        dry = sum(row["result"] == "演练将跳过" for row in rows)
        missing = sum(row["result"] in ("缺少标定", "未选窗口") for row in rows)
        self.summary_label.setText(f"执行 {execute} 项 · 演练跳过 {dry} 项 · 缺少条件 {missing} 项")
        if self.steps_list.currentRow() < 0 and self.steps_list.count():
            self.steps_list.setCurrentRow(0)
        self._show_details()

    def _show_details(self, *args):
        item = self.steps_list.currentItem()
        row = self._summary_rows.get(item.data(Qt.ItemDataRole.UserRole), {}) if item else {}
        self.details.setPlainText("\n".join([row.get("title", ""), *row.get("problems", [])]))
        self.details.setVisible(bool(row.get("problems")) and row.get("result") in ("缺少标定", "未选窗口"))
        self.step_live_mode.blockSignals(True)
        name = item.data(Qt.ItemDataRole.UserRole) if item else None
        if name == "dungeon":
            name = DailyTask._selected_dungeon(SimpleNamespace(task_cfg=lambda key: cfg_mod.task_config(self.cfg, key)))
        self.step_live_mode.setChecked(bool(name) and not cfg_mod.task_config(self.cfg, name).get("dry_run", True))
        self.step_live_mode.blockSignals(False)

    def _toggle_row_mode(self, row):
        if not self._start_pending and not self._runtime_running():
            self.steps_list.setCurrentRow(row)
            self.step_live_mode.setChecked(not self.step_live_mode.isChecked())

    def _configure_row(self, row):
        self.steps_list.setCurrentRow(row)
        self._configure_step()

    def _configure_step(self):
        item = self.steps_list.currentItem()
        if item is not None and not self._start_pending and not self._runtime_running():
            navigate = getattr(self.window(), "show_page", None)
            if navigate:
                navigate(item.data(Qt.ItemDataRole.UserRole))

    def _move_step(self, offset):
        row = self.steps_list.currentRow()
        target = row + offset
        if self._start_pending or self._runtime_running() or row < 0 or not 0 <= target < self.steps_list.count():
            return
        self.steps_list.blockSignals(True)
        item = self.steps_list.takeItem(row)
        self.steps_list.insertItem(target, item)
        self.steps_list.setCurrentRow(target)
        self.steps_list.blockSignals(False)
        self._steps_changed()
        self._show_details()

    def _save_step_mode(self, checked):
        item = self.steps_list.currentItem()
        if item is None or self._start_pending or self._runtime_running():
            return
        name = item.data(Qt.ItemDataRole.UserRole)
        try:
            cfg = cfg_mod.load_config()
            if name == "dungeon":
                name = DailyTask._selected_dungeon(SimpleNamespace(task_cfg=lambda key: cfg_mod.task_config(cfg, key)))
            if not name:
                raise ValueError("请先选择副本")
            tc = cfg_mod.task_config(cfg, name)
            tc["dry_run"] = not checked
            cfg_mod.set_task_config(cfg, name, tc)
            cfg_mod.save_config(cfg)
        except Exception as exc:
            self._show_details()
            self.summary_label.setText("保存失败：" + str(exc))
            return
        self.cfg = cfg
        self.config_changed.emit(cfg)
        self._request_summary()

    def _make_runner(self):
        # TaskPage 的 worker 调 runtime.start；本计划也在同一个受保护的
        # preflight 中先算完，再由原 TaskRunner 启动 DailyTask.run。
        self._summary_generation += 1
        generation, signal = self._summary_generation, self.summary_ready

        def report(rows, count):
            try:
                signal.emit(generation, rows, count, "")
            except RuntimeError:
                pass

        self.task_factory = lambda: _PlannedDailyTask(report)
        return super()._make_runner()

    def toggle_run(self):
        if self._start_pending or self._runtime_running():
            self.stop()
        elif self._save_edits():
            super().toggle_run()

    start = toggle_run

    def _set_editable(self, editable):
        for widget in (self.steps_list, self.time_limit, self.refresh_button, self.configure_button,
                       self.move_up_button, self.move_down_button, self.step_live_mode, self.order_button):
            widget.setEnabled(editable)

    def _start_result(self, ok, problems):
        super()._start_result(ok, problems)
        if not self._closed:
            self._set_editable(not self._start_pending and not self._runtime_running())

    def _pump(self):
        # 空闲的一条龙页不消费其它任务（例如启动登录）的日志。
        if self.runner is not None and not self.log_consumption_paused:
            super()._pump()
        if self._visible and not self._closed:
            self._set_editable(not self._start_pending and not self._runtime_running())

    def showEvent(self, event):
        super().showEvent(event)
        if not self._start_pending and not self._runtime_running():
            self.refresh_config()
