"""标定中心：三个尺寸组、任务与共享资产的 Qt 入口。"""

import copy

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QInputDialog, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from ..core import calib_profiles as calib, config as cfg_mod, window as win_mod
from .calibration_dialog import CalibrationDialog, calibration_tasks, resolve_profile
from . import theme as T


class CalibrationPage(QWidget):
    config_changed = Signal(object)

    def __init__(self, parent=None, cfg=None):
        super().__init__(parent)
        self._cfg_supplied = cfg is not None
        self.cfg = copy.deepcopy(cfg) if cfg is not None else cfg_mod.load_config()
        self._editor = None
        self.setObjectName("page_calibration")
        self.setProperty("role", "page")
        self.setStyleSheet(f"QComboBox {{ background: {T.BG}; color: {T.TEXT}; padding: 6px; }} "
                          f"QComboBox QAbstractItemView {{ background: {T.SURFACE}; color: {T.TEXT}; }}")
        layout = QVBoxLayout(self)
        title = QLabel("标定中心", self)
        title.setProperty("role", "heading")
        layout.addWidget(title)
        hint = QLabel("最多保留三个尺寸组；激活组为绿色。切换尺寸组会更新识别基准，模板文件共用。", self)
        hint.setWordWrap(True)
        layout.addWidget(hint)
        profiles = QHBoxLayout()
        self.profile_buttons = []
        for index in range(calib.MAX_PROFILES):
            button = QPushButton(self)
            button.clicked.connect(lambda checked=False, i=index: self.activate_profile(i))
            profiles.addWidget(button)
            self.profile_buttons.append(button)
        layout.addLayout(profiles)
        tools = QHBoxLayout()
        self.record_button = QPushButton("记录当前窗口尺寸", self)
        self.record_button.clicked.connect(self.record_size)
        self.delete_button = QPushButton("删除当前组（保留模板）", self)
        self.delete_button.clicked.connect(self.delete_active)
        self.refresh_button = QPushButton("刷新配置", self)
        self.refresh_button.clicked.connect(self.refresh_config)
        for button in (self.record_button, self.delete_button, self.refresh_button):
            tools.addWidget(button)
        layout.addLayout(tools)
        self.tasks = QComboBox(self)
        for name, title in calibration_tasks():
            self.tasks.addItem(title, name)
        layout.addWidget(self.tasks)
        self.open_button = QPushButton("打开标定（区域 / 模板 / 装备 / 物品）", self)
        self.open_button.clicked.connect(self.open_task)
        layout.addWidget(self.open_button)
        self.status = QLabel("将游戏目标窗口放到前面，再打开标定并框选。取消框选不保存。", self)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addStretch()
        self.refresh_config()

    def _busy(self):
        running = getattr(self.window(), "_calibration_running", lambda: False)
        if self._editor is not None or running():
            self.status.setText("请先停止运行中的任务或关闭标定窗口。")
            return True
        return False

    def _draft(self):
        return copy.deepcopy(self.cfg) if self._cfg_supplied else cfg_mod.load_config()

    def refresh_config(self):
        if not self._cfg_supplied:
            self.cfg = cfg_mod.load_config()
        self.profiles = calib.items(self.cfg)
        aid = calib.active_id(self.cfg)
        for index, button in enumerate(self.profile_buttons):
            prof = self.profiles[index] if index < len(self.profiles) else None
            button.setEnabled(prof is not None)
            button.setText(f"{prof['label']} · {calib.size_text(prof)}" if prof else "未记录")
            button.setStyleSheet("QPushButton { color: "
                                + ("#45d6a2" if prof and prof["id"] == aid else "#8a93a3") + "; }")
        self.delete_button.setEnabled(bool(self.profiles))

    def _publish(self, cfg):
        self.cfg = cfg
        self.refresh_config()
        self.config_changed.emit(cfg)

    def _commit(self, draft):
        try:
            cfg_mod.save_config(draft)
        except Exception as exc:
            self.status.setText(f"保存失败，本次修改未保留：{exc}")
            return False
        self._publish(draft)
        self.status.setText("已保存")
        return True

    def activate_profile(self, index):
        if self._busy() or not 0 <= index < len(self.profiles):
            return
        try:
            draft = self._draft()
            if calib.set_active(draft, self.profiles[index]["id"]):
                self._commit(draft)
        except Exception as exc:
            self.status.setText(f"切换尺寸组失败：{exc}")

    def delete_active(self):
        if self._busy():
            return
        try:
            draft = self._draft()
            if calib.active_id(draft) is None:
                return
            calib.delete(draft, calib.active_id(draft))
            self._commit(draft)
        except Exception as exc:
            self.status.setText(f"删除尺寸组失败：{exc}")

    def record_size(self):
        if self._busy():
            return
        try:
            draft = self._draft()
            win_mod.set_game_process(draft.get("window_process") or win_mod.DEFAULT_GAME_PROCESS_SPEC)
            wins = win_mod.locate_all(draft.get("window_title", "梦幻西游"), draft.get("window_offset", [0, 0]))
            if not wins:
                raise ValueError("没找到游戏窗口，请先打开游戏。")
            index = 0
            if len(wins) > 1:
                labels = [f"窗口 {n + 1}" for n in range(len(wins))]
                label, ok = QInputDialog.getItem(self, "窗口尺寸", "选择参照窗口：", labels, 0, False)
                if not ok:
                    return
                index = labels.index(label)
            rect = wins[index].rect()
            if rect is None:
                raise ValueError("窗口已失效，请重试。")
            if resolve_profile(draft, rect[2:4], self) is not None:
                self._commit(draft)
        except Exception as exc:
            self.status.setText(f"记录尺寸失败：{exc}")

    def open_task(self):
        if self._busy():
            return
        dialog = CalibrationDialog(self, self.tasks.currentData(), self.cfg if self._cfg_supplied else None)
        self._editor = dialog
        dialog.config_changed.connect(self._publish)
        try:
            dialog.exec()
        finally:
            self._editor = None
            dialog.deleteLater()
            self.refresh_config()

    def showEvent(self, event):
        self.refresh_config()
        super().showEvent(event)
