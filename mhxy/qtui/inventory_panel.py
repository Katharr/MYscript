"""通用页背包入口；执行和自动维护策略沿用原任务及 core。"""

import copy

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
    QPushButton, QVBoxLayout,
)

from ..core import calib_profiles as calib, calibration_assets, config as cfg_mod, vision
from ..tasks.organize_bag import OrganizeBagTask
from .inventory_items_dialog import InventoryItemsDialog, capture_item
from .task_page import TaskPage


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
        return self._start_pending or self._runtime_running() or self._capturing or self._editor is not None

    def refresh_config(self):
        if self._busy():
            return self.cfg
        super().refresh_config()
        tc = cfg_mod.task_config(self.cfg, self.task_name)
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
        tc = cfg_mod.task_config(draft, self.task_name)
        tc["dry_run"] = not checked
        cfg_mod.set_task_config(draft, self.task_name, tc)
        self._commit(draft)
        self.refresh_config()

    def update_windows(self, states, error=""):
        from .role_window_page import target_indices

        self._window_snapshot = (states, error)
        selected = target_indices(self.cfg.get("targets") or {}, len(states), apply_limit=True)
        labels = [states[i].label for i in selected if states[i].status != "failed"]
        self.window_status.setText("窗口检测失败：" + error if error else
                                   "已连接：" + "、".join(labels) if labels else "未连接目标窗口")
        self.window_status.setWordWrap(True)

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
    TITLE = "通用 / 工具 · 整理背包"
    LOG_SOURCE = "整理背包"
    START_TEXT = "整理背包"

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("task_factory", OrganizeBagTask)
        super().__init__(*args, **kwargs)

    def _build_ui(self):
        super()._build_ui()
        self.auto_organize = QCheckBox("背包满时自动整理（共享设置）", self)
        self.auto_organize.toggled.connect(self._save_auto)
        self.add_control("自动整理", self.auto_organize)
        self._edit_controls.append(self.auto_organize)
        controls = QHBoxLayout()
        self.items_button = QPushButton("管理物品", self)
        self.items_button.clicked.connect(self.open_items)
        self.calibrate_button = QPushButton("标定（整理背包）", self)
        self.calibrate_button.clicked.connect(self.open_calibration)
        self.refresh_button = QPushButton("刷新配置", self)
        self.refresh_button.clicked.connect(self.refresh_config)
        for button in (self.items_button, self.calibrate_button, self.refresh_button):
            controls.addWidget(button)
            self._edit_controls.append(button)
        self.content_layout.addLayout(controls)
        self.summary = QLabel(self)
        self.summary.setWordWrap(True)
        self.content_layout.addWidget(self.summary)
        hint = QLabel("自动整理沿用各任务现有的背包维护入口；演练只识别当前一屏。\n"
                      "运行前请配置 open_bag 快捷键，并将游戏包裹保持可见。", self)
        hint.setWordWrap(True)
        self.content_layout.addWidget(hint)
        self.content_layout.addStretch()

    def refresh_config(self):
        if self._busy():
            return self.cfg
        super().refresh_config()
        tc = cfg_mod.task_config(self.cfg, self.task_name)
        self.auto_organize.blockSignals(True)
        self.auto_organize.setChecked(bool(tc.get("auto_organize", False)))
        self.auto_organize.blockSignals(False)
        self.summary.setText(f"物品清单：{len(tc.get('items') or [])} 件；"
                             f"背包列表区：{'已标定' if tc.get('regions', {}).get('bag_list') else '整窗检测'}；"
                             f"背包满图标：{'已标定' if tc.get('templates', {}).get('bag_full_icon') else '未标定'}")
        return self.cfg

    def _save_auto(self, checked):
        if self._busy():
            return
        draft = self._draft()
        tc = cfg_mod.task_config(draft, self.task_name)
        tc["auto_organize"] = bool(checked)
        cfg_mod.set_task_config(draft, self.task_name, tc)
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
        if self._busy():
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("整理背包 · 标定")
        layout = QVBoxLayout(dialog)
        choices = QComboBox(dialog)
        choices.addItem("背包列表区（留空使用整窗）", ("regions", "bag_list"))
        for key, label, hint in OrganizeBagTask.CALIBRATION["templates"]:
            choices.addItem(label, ("templates", key))
            choices.setItemData(choices.count() - 1, hint, Qt.ItemDataRole.ToolTipRole)
        layout.addWidget(choices)
        status = QLabel("选择一项后框选；模板和尺寸组一起保存。", dialog)
        status.setWordWrap(True)
        layout.addWidget(status)
        capture = QPushButton("框选 / 重新标定", dialog)
        layout.addWidget(capture)
        clear = QPushButton("背包列表区恢复整窗", dialog)
        layout.addWidget(clear)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, dialog)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        def save(clear_region=False):
            if self._capturing:
                return
            self._capturing = True
            try:
                draft = self._draft()
                tc = cfg_mod.task_config(draft, self.task_name)
                image = None
                if clear_region:
                    tc.setdefault("regions", {})["bag_list"] = None
                else:
                    captured = capture_item(dialog, draft, choices.currentText())
                    if captured is None:
                        return
                    roi, crop, pid = captured
                    group, key = choices.currentData()
                    if group == "regions":
                        tc.setdefault(group, {})[key] = roi
                    else:
                        path = tc.setdefault(group, {}).get(key) or f"templates/tm_{key}.png"
                        tc[group][key] = path
                        image = (path, vision.encode_png(crop))
                    cfg_mod.set_task_config(draft, self.task_name, tc)
                    calib.set_task_profile(draft, self.task_name, pid)
                    if group == "templates":
                        calib.set_template_profile(draft, self.task_name, key, pid)
                if clear_region:
                    cfg_mod.set_task_config(draft, self.task_name, tc)
                if self._commit(draft, image):
                    status.setText("已保存")
                else:
                    status.setText("保存失败，配置和模板已回滚；详情见任务日志。")
            except Exception as exc:
                status.setText(f"标定失败：{exc}")
            finally:
                self._capturing = False

        capture.clicked.connect(lambda: save(False))
        clear.clicked.connect(lambda: save(True))
        self._editor = dialog
        self._set_editable(False)
        try:
            dialog.exec()
        finally:
            self._editor = None
            dialog.deleteLater()
            self.refresh_config()
            self._set_editable(True)
