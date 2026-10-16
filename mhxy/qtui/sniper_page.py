"""秒装备 Qt 页：只展示配置和任务快照，不参与识别或购买。"""

import copy
import os
import threading

from PySide6.QtCore import Qt, QSize, Signal, Slot
from PySide6.QtGui import QIcon, QImage, QPixmap, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QHBoxLayout,
    QHeaderView, QLabel, QPushButton, QScrollArea, QSpinBox, QTableView,
    QVBoxLayout, QWidget,
)

from ..core import config as cfg_mod
from ..tasks.sniper import SniperTask
from . import theme as T
from .task_page import TaskPage


def _template_signature(path):
    """与旧 widgets.load_thumb 一样支持项目相对路径及中文绝对路径。"""
    if not path:
        return None
    absolute = os.path.abspath(path if os.path.isabs(path) else cfg_mod.PROJECT_ROOT / path)
    try:
        stamp = os.stat(absolute).st_mtime_ns
    except OSError:
        stamp = None
    return absolute, stamp


def _load_thumb(path, max_h=64):
    """仅在 worker 调用；返回独立 QImage，QPixmap 留到 Qt 主线程创建。

    沿用旧 widgets.load_thumb 的 PIL 读取、RGB fallback 和失败返回空值
    约束，避免为复用 CTkImage 引入 Tk。
    """
    if not path:
        return None
    try:
        from PIL import Image

        absolute = path if os.path.isabs(path) else str(cfg_mod.PROJECT_ROOT / path)
        if not os.path.exists(absolute):
            return None
        try:
            with Image.open(absolute) as source:
                source.load()
                img = source.convert("RGBA")
        except Exception:
            from ..core import vision

            pixels = vision.load_template_rgb(path)
            if pixels is None:
                return None
            img = Image.fromarray(pixels).convert("RGBA")
        scale = min(max_h / max(1, img.height), 240 / max(1, img.width))
        img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))))
        return QImage(img.tobytes(), img.width, img.height, img.width * 4,
                      QImage.Format.Format_RGBA8888).copy()
    except Exception:
        return None


def _table(parent, headers):
    view = QTableView(parent)
    model = QStandardItemModel(0, len(headers), view)
    model.setHorizontalHeaderLabels(headers)
    view.setModel(model)
    view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    view.verticalHeader().hide()
    view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    view.horizontalHeader().setStretchLastSection(True)
    view.setWordWrap(False)
    return view, model


class SniperPage(TaskPage):
    TASK_NAME = "sniper"
    TITLE = "秒装备"
    LOG_SOURCE = "秒装备"
    START_TEXT = "开始秒装备"
    config_changed = Signal(object)
    thumbnails_ready = Signal(object)

    def __init__(self, *args, **kwargs):
        self._thumb_cache = {}
        self._thumb_pending = set()
        self._template_signatures = {}
        self._progress_snapshot = None
        self._calibration_dialog = None
        self._window_snapshot = None
        self.log_consumption_paused = False
        kwargs.setdefault("task_factory", SniperTask)
        super().__init__(*args, **kwargs)
        self.start_requested.connect(lambda: self._set_editable(False))

    def _build_ui(self):
        super()._build_ui()
        self.setStyleSheet(f"""
            QTableView, QSpinBox, QTextBrowser {{ background: {T.BG};
                color: {T.TEXT}; border: 1px solid {T.BORDER}; }}
            QTableView {{ gridline-color: {T.BORDER};
                selection-background-color: {T.BUTTON_HOVER}; }}
            QHeaderView::section {{ background: {T.SURFACE}; color: {T.TEXT_DIM};
                border: 1px solid {T.BORDER}; padding: 4px; }}
            QCheckBox {{ spacing: 8px; }}
            QScrollArea {{ border: none; background: transparent; }}
            QWidget#sniperBody {{ background: {T.SURFACE}; }}
        """)
        self.thumbnails_ready.connect(self._apply_thumbnails, Qt.ConnectionType.QueuedConnection)
        self.live_mode = QCheckBox("实战模式（命中会真正购买）", self)
        self.live_mode.setToolTip("默认演练：只识别，不点击、不刷新、不计数。")
        self.live_mode.toggled.connect(self._save_edits)
        self.add_control("运行模式", self.live_mode)
        self.target_count = QSpinBox(self)
        self.target_count.setRange(1, 2147483647)
        self.target_count.setKeyboardTracking(False)
        self.target_count.editingFinished.connect(self._save_edits)
        self.add_control("每窗口购买数量", self.target_count)

        area = QScrollArea(self)
        area.setWidgetResizable(True)
        body = QWidget(area)
        body.setObjectName("sniperBody")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(T.SPACE_SM)
        area.setWidget(body)
        self.content_layout.addWidget(area, 1)
        tools = QHBoxLayout()
        hint = QLabel("打开商品页后开始；成功提示确认后计数。", self)
        tools.addWidget(hint, 1)
        self.calibration_button = QPushButton("标定区域与模板", self)
        self.calibration_button.clicked.connect(self._open_calibration)
        tools.addWidget(self.calibration_button)
        self.refresh_button = QPushButton("刷新配置", self)
        self.refresh_button.clicked.connect(self.refresh_config)
        tools.addWidget(self.refresh_button)
        body_layout.addLayout(tools)
        self.regions_status = QLabel(self)
        self.regions_status.setTextFormat(Qt.TextFormat.PlainText)
        body_layout.addWidget(self.regions_status)

        self.templates_view, self.templates_model = _table(
            self, ("商品 / 按钮模板", "缩略图", "标定状态", "模板路径"))
        self.templates_view.setIconSize(QSize(240, 64))
        self.templates_view.verticalHeader().setDefaultSectionSize(72)
        self.templates_view.setMinimumHeight(100)
        for key, label, _ in SniperTask.CALIBRATION["templates"]:
            self.templates_model.appendRow([QStandardItem(label), QStandardItem("未标定"),
                                            QStandardItem("未标定"), QStandardItem("")])
        self.templates_view.doubleClicked.connect(self._open_calibration)
        body_layout.addWidget(self.templates_view, 3)
        self.progress_label = QLabel("购买进度：待启动", self)
        body_layout.addWidget(self.progress_label)
        self.progress_view, self.progress_model = _table(
            self, ("目标窗口", "成功数量", "目标数量", "当前状态"))
        self.progress_view.setMinimumHeight(65)
        body_layout.addWidget(self.progress_view, 2)

    def refresh_config(self):
        if self._start_pending or self._runtime_running():
            return self.cfg
        super().refresh_config()
        tc = cfg_mod.task_config(self.cfg, self.TASK_NAME)
        self.live_mode.blockSignals(True)
        self.live_mode.setChecked(not tc.get("dry_run", True))
        self.live_mode.blockSignals(False)
        self.target_count.setValue(tc.get("target_count", 1))
        self._edit_values = (self.live_mode.isChecked(), self.target_count.value())
        self._refresh_templates(tc)
        self.regions_status.setText("  ·  ".join(
            f"{label}：{'已标定' if tc.get('regions', {}).get(key) else '整窗检测'}"
            for key, label, *_ in SniperTask.CALIBRATION["regions"]))
        if self._window_snapshot is not None:
            self.update_windows(*self._window_snapshot)
        return self.cfg

    def _save_edits(self, *args):
        if self._start_pending or self._runtime_running():
            return False
        values = (self.live_mode.isChecked(), self.target_count.value())
        try:
            cfg = copy.deepcopy(self.cfg) if self._cfg_supplied else cfg_mod.load_config()
            tc = cfg_mod.task_config(cfg, self.TASK_NAME)
            # 仅覆盖本页实际修改项，保留其它页面刚保存的配置与标定。
            if values[0] != self._edit_values[0]:
                tc["dry_run"] = not values[0]
            if values[1] != self._edit_values[1]:
                tc["target_count"] = values[1]
            if values != self._edit_values:
                cfg_mod.set_task_config(cfg, self.TASK_NAME, tc)
                cfg_mod.save_config(cfg)
        except Exception as exc:
            self.log_panel.append("配置保存失败：" + str(exc), "error", self.LOG_SOURCE)
            return False
        self.cfg = cfg
        self._edit_values = values
        self.mode_label.setText("演练" if tc.get("dry_run", True) else "实战")
        self.config_changed.emit(cfg)
        return True

    def _refresh_templates(self, tc):
        pending = {}
        for row, (key, _label, _hint) in enumerate(SniperTask.CALIBRATION["templates"]):
            path = tc.get("templates", {}).get(key)
            signature = _template_signature(path)
            self.templates_model.item(row, 3).setText(str(path or ""))
            self.templates_model.item(row, 3).setToolTip(str(path or "未标定；双击打开标定"))
            if key in self._template_signatures and self._template_signatures[key] == signature:
                continue
            self._template_signatures[key] = signature
            if signature in self._thumb_cache:
                self._render_thumbnail(row, self._thumb_cache[signature])
            elif signature is None or signature[1] is None:
                self._render_thumbnail(row, None)
            else:
                self._render_thumbnail(row, None, loading=True)
                if signature not in self._thumb_pending:
                    pending[signature] = path
        if not pending:
            return
        self._thumb_pending.update(pending)
        signal = self.thumbnails_ready

        def work():
            results = [(signature, _load_thumb(path)) for signature, path in pending.items()]
            try:
                signal.emit(results)
            except RuntimeError:
                pass

        threading.Thread(target=work, name="mhxy-sniper-thumbnails", daemon=True).start()

    def _render_thumbnail(self, row, image, loading=False):
        item = self.templates_model.item(row, 1)
        item.setIcon(QIcon(QPixmap.fromImage(image)) if image is not None else QIcon())
        item.setText("" if image is not None else "加载中…" if loading else "未标定")
        self.templates_model.item(row, 2).setText(
            "已标定" if image is not None else "加载中…" if loading else "未标定")

    @Slot(object)
    def _apply_thumbnails(self, results):
        if self._closed:
            return
        for signature, image in results:
            self._thumb_pending.discard(signature)
            if signature not in self._template_signatures.values():
                continue
            # 只保留同一路径的当前版本，避免标定后积累大批旧缩略图。
            for old in list(self._thumb_cache):
                if old[0] == signature[0] and old != signature:
                    del self._thumb_cache[old]
            self._thumb_cache[signature] = image
            for row, (key, *_rest) in enumerate(SniperTask.CALIBRATION["templates"]):
                if self._template_signatures.get(key) == signature:
                    self._render_thumbnail(row, image)

    def _render_progress(self, snapshot):
        snapshot = tuple(snapshot)
        if snapshot == self._progress_snapshot:
            return
        self._progress_snapshot = snapshot
        self.progress_model.setRowCount(len(snapshot))
        for row, record in enumerate(snapshot):
            for column, value in enumerate(record):
                item = self.progress_model.item(row, column)
                if item is None:
                    item = QStandardItem()
                    self.progress_model.setItem(row, column, item)
                if item.text() != str(value):
                    item.setText(str(value))
        self.progress_label.setText("购买进度：成功 " + str(sum(row[1] for row in snapshot))
                                    + " 件" if snapshot else "购买进度：待启动")

    def _open_calibration(self, *args):
        from .calibration_dialog import open_calibration

        open_calibration(self)

    def update_windows(self, states, error=""):
        from .role_window_page import target_indices

        self._window_snapshot = (states, error)
        indices = target_indices(self.cfg.get("targets") or {}, len(states), apply_limit=True)
        labels = [states[i].label for i in indices if states[i].status != "failed"]
        self.window_status.setText("窗口检测失败：" + error if error else
                                   "已连接：" + "、".join(labels) if labels else "未连接目标窗口")
        self.window_status.setWordWrap(True)

    def toggle_run(self):
        if self._start_pending or self._runtime_running():
            self.stop()
        elif self._calibration_dialog is not None:
            self.log_panel.append("无法启动：请先关闭标定窗口", "error", self.LOG_SOURCE)
        elif self._save_edits():
            self._render_progress(())
            super().toggle_run()

    start = toggle_run

    def _set_editable(self, editable):
        for widget in (self.live_mode, self.target_count, self.refresh_button, self.calibration_button):
            widget.setEnabled(editable)

    def _start_result(self, ok, problems):
        super()._start_result(ok, problems)
        if not self._closed:
            self._set_editable(not self._start_pending and not self._runtime_running())

    def _pump(self):
        if not self.log_consumption_paused and self.runner is not None:
            super()._pump()
        if self._visible and not self._closed:
            if self.runner is not None:
                self._render_progress(getattr(self.runner.task, "progress", ()))
            self._set_editable(not self._start_pending and not self._runtime_running())

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_config()
