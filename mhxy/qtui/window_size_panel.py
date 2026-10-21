"""窗口尺寸组及只读尺寸列表；窗口实际调整交给主窗口的后台操作。"""

import copy

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QDialog, QDialogButtonBox, QFrame, QGridLayout, QHBoxLayout,
    QInputDialog, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from ..core import calib_profiles as calib, config as cfg_mod
from . import theme as T
from .widgets import FlowLayout


class WindowSizePanel(QFrame):
    config_changed = Signal(object)
    restore_requested = Signal()
    editing_changed = Signal()

    def __init__(self, parent=None, cfg=None):
        super().__init__(parent)
        self.cfg = copy.deepcopy(cfg) if cfg is not None else cfg_mod.load_config()
        self.states = []
        self.selected = set()
        self._locked = False
        self._error = ""
        self._editing = False
        self._context = None
        self.setObjectName("activityCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        title = QLabel("窗口尺寸", self)
        title.setProperty("role", "section")
        layout.addWidget(title)
        self.summary = QLabel(self)
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        profiles = FlowLayout()
        self.profile_group = QButtonGroup(self)
        self.profile_buttons = []
        for index in range(calib.MAX_PROFILES):
            button = QPushButton(self)
            button.setCheckable(True)
            T.set_role(button, "profile")
            self.profile_group.addButton(button)
            button.clicked.connect(lambda checked=False, i=index: self.activate_profile(i))
            profiles.addWidget(button)
            self.profile_buttons.append(button)
        layout.addLayout(profiles)
        actions = FlowLayout()
        self.restore_button = QPushButton(self)
        T.set_role(self.restore_button, "primary")
        self.restore_button.clicked.connect(self.restore_requested.emit)
        self.record_button = QPushButton("记录参照窗口尺寸", self)
        self.record_button.clicked.connect(self.record_size)
        self.manage_button = QPushButton("管理尺寸组", self)
        self.manage_button.clicked.connect(self.manage_profiles)
        for button in (self.restore_button, self.record_button, self.manage_button):
            actions.addWidget(button)
        layout.addLayout(actions)
        self.reason = QLabel(self)
        self.reason.setWordWrap(True)
        self.reason.setProperty("role", "muted")
        layout.addWidget(self.reason)
        self.status = QLabel(self)
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.hide()
        layout.addWidget(self.status)
        self.window_list = QWidget(self)
        self.rows = QGridLayout(self.window_list)
        self.rows.setContentsMargins(0, 6, 0, 0)
        self.rows.setHorizontalSpacing(12)
        self.rows.setVerticalSpacing(8)
        self.rows.setColumnStretch(0, 1)
        layout.addWidget(self.window_list)
        self.row_labels = {}
        self.refresh_profiles()

    def set_context(self, cfg, states, selected, *, locked=False, error=""):
        context = (cfg.get("calib_profiles"), cfg.get("targets", {}).get("base_size"),
                   tuple(states), frozenset(selected), locked, error)
        if self._context == context:
            return
        self._context = copy.deepcopy(context)
        self.cfg = copy.deepcopy(cfg)
        self.states, self.selected = list(states), set(selected)
        self._locked, self._error = locked, error
        self.refresh_profiles()

    def refresh_profiles(self):
        self.profiles = calib.items(self.cfg)
        active_id = calib.active_id(self.cfg)
        size = calib.active_size(self.cfg)
        active = calib.active(self.cfg)
        target = f"{size[0]}×{size[1]}" if size else "未记录"
        prefix = active["label"] + " · " if active else ""
        count = len(self.selected)
        self.summary.setText(f"当前尺寸组：{prefix}{target}　·　已选 {count} 个角色")
        self.profile_group.setExclusive(False)
        for index, button in enumerate(self.profile_buttons):
            profile = self.profiles[index] if index < len(self.profiles) else None
            button.setText(f"{profile['label']} {calib.size_text(profile)}" if profile else "未记录")
            button.setChecked(bool(profile and profile["id"] == active_id))
            button.setEnabled(bool(profile) and not self._locked)
        self.profile_group.setExclusive(True)
        reason = ("请先停止任务或等待当前操作结束" if self._locked else
                  "窗口检测失败，请刷新后重试" if self._error else
                  "请在顶部选择目标角色" if not count else
                  "请先记录窗口尺寸或完成一次标定" if not size else "")
        self.restore_button.setText(f"恢复选中尺寸（{count}）")
        self.restore_button.setEnabled(not reason)
        self.restore_button.setToolTip(reason or f"恢复到 {target}")
        self.reason.setText(reason)
        self.reason.setVisible(bool(reason))
        self.record_button.setEnabled(not self._locked and not self._error and bool(self._references()))
        self.record_button.setToolTip("" if self._references() else "请先选择尺寸可读取的角色窗口")
        self.manage_button.setEnabled(bool(self.profiles) and not self._locked)
        self._render_windows(size)

    def _render_windows(self, base):
        while self.rows.count():
            item = self.rows.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        self.row_labels = {}
        for column, text in enumerate(("角色", "当前尺寸", "尺寸状态")):
            label = QLabel(text, self.window_list)
            label.setProperty("role", "muted")
            self.rows.addWidget(label, 0, column)
        for row, state in enumerate(self.states, 1):
            size = state.size
            match = bool(base and size and all(abs(a - b) <= calib.SIZE_TOL for a, b in zip(base, size)))
            comparison = ("检测失败" if state.status == "failed" else "未读取" if not size else
                          "无基准" if not base else "一致" if match else "不一致")
            name = ("● " if state.hwnd in self.selected else "") + state.label
            values = [name, f"{size[0]}×{size[1]}" if size else "—", comparison]
            labels = []
            for column, text in enumerate(values):
                label = QLabel(text, self.window_list)
                label.setTextFormat(Qt.TextFormat.PlainText)
                label.setWordWrap(True)
                label.setToolTip(state.label + (" · 已最小化" if state.minimized else ""))
                self.rows.addWidget(label, row, column)
                labels.append(label)
            T.set_tone(labels[2], "error" if state.status == "failed" else
                       "success" if match else "warning" if base and size else "")
            self.row_labels[state.hwnd] = labels
        if not self.states:
            self.rows.addWidget(QLabel("未检测到游戏窗口", self.window_list), 1, 0, 1, 3)

    def set_status(self, message, level="info"):
        self.status.setText(message)
        T.set_tone(self.status, {"warn": "warning", "error": "error", "success": "success"}.get(level, ""))
        self.status.setVisible(bool(message))

    def _busy(self):
        if self._locked or getattr(self.window(), "_selection_locked", lambda: False)():
            self.set_status("请先停止任务或关闭编辑窗口，再调整尺寸组。", "warn")
            self.refresh_profiles()
            return True
        return False

    def _commit(self, draft):
        try:
            cfg_mod.save_config(draft)
        except Exception as exc:
            self.set_status(f"保存失败，本次修改未保留：{exc}", "error")
            self.refresh_profiles()
            return False
        self.cfg = draft
        self._context = None
        self.refresh_profiles()
        self.config_changed.emit(draft)
        self.set_status("已保存", "success")
        return True

    def activate_profile(self, index):
        if self._busy() or not 0 <= index < len(self.profiles):
            return
        try:
            draft = cfg_mod.load_config()
            if calib.set_active(draft, self.profiles[index]["id"]):
                self._commit(draft)
        except Exception as exc:
            self.set_status(f"切换尺寸组失败：{exc}", "error")
            self.refresh_profiles()

    def delete_profile(self, pid):
        """管理弹窗内使用；只删记录，模板文件由 calib.delete 保留。"""
        try:
            draft = cfg_mod.load_config()
            if not calib.get(draft, pid):
                return False
            calib.delete(draft, pid)
            return self._commit(draft)
        except Exception as exc:
            self.set_status(f"删除尺寸组失败：{exc}", "error")
            return False

    def _references(self):
        return [state for state in self.states if state.hwnd in self.selected
                and state.size and state.status != "failed"]

    def record_size(self):
        from .calibration_dialog import resolve_profile

        if self._busy():
            return
        references = self._references()
        if not references or self._error:
            self.set_status("请先选择尺寸可读取的角色窗口。", "warn")
            return
        self._editing = True
        self.editing_changed.emit()
        try:
            index = 0
            if len(references) > 1:
                labels = [f"{i + 1}. {s.label} · {s.size[0]}×{s.size[1]}" for i, s in enumerate(references)]
                label, ok = QInputDialog.getItem(self, "记录窗口尺寸", "参照角色：", labels, 0, False)
                if not ok:
                    return
                index = labels.index(label)
            reference = references[index]
            current = next((s for s in self._references() if s.hwnd == reference.hwnd), None)
            if self._error or current is None or current.size != reference.size:
                self.set_status("参照窗口已变化，请重新记录。", "warn")
                return
            draft = cfg_mod.load_config()
            if resolve_profile(draft, current.size, self) is not None:
                latest = next((s for s in self._references() if s.hwnd == reference.hwnd), None)
                if self._error or latest is None or latest.size != current.size:
                    self.set_status("参照窗口已变化，请重新记录。", "warn")
                    return
                self._commit(draft)
        except Exception as exc:
            self.set_status(f"记录尺寸失败：{exc}", "error")
        finally:
            self._editing = False
            self._context = None
            self.editing_changed.emit()

    def manage_profiles(self):
        if self._busy():
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("管理尺寸组")
        layout = QVBoxLayout(dialog)
        for profile in self.profiles:
            row = QHBoxLayout()
            row.addWidget(QLabel(f"{profile['label']} · {calib.size_text(profile)}", dialog), 1)
            remove = QPushButton("删除（保留模板）", dialog)
            T.set_role(remove, "danger")
            def delete(checked=False, pid=profile["id"], button=remove):
                if self.delete_profile(pid):
                    button.setText("已删除")
                    button.setEnabled(False)
                    feedback.hide()
                else:
                    feedback.setText(self.status.text())
                    feedback.show()
            remove.clicked.connect(delete)
            row.addWidget(remove)
            layout.addLayout(row)
        feedback = QLabel(dialog)
        feedback.setWordWrap(True)
        feedback.hide()
        layout.addWidget(feedback)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, dialog)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        self._editing = True
        self.editing_changed.emit()
        try:
            dialog.exec()
        finally:
            self._editing = False
            self._context = None
            self.editing_changed.emit()
            dialog.deleteLater()
