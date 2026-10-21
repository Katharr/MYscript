"""标定中心：通用标定及活动逐项覆盖的统一入口。"""

import copy

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from ..core import calib_profiles as calib, config as cfg_mod
from .calibration_dialog import CalibrationDialog, calibration_tasks
from . import theme as T
from .widgets import ComboBox as QComboBox, FlowLayout


class CalibrationPage(QWidget):
    config_changed = Signal(object)
    manage_sizes_requested = Signal()

    def __init__(self, parent=None, cfg=None):
        super().__init__(parent)
        self._cfg_supplied = cfg is not None
        self.cfg = copy.deepcopy(cfg) if cfg is not None else cfg_mod.load_config()
        self._editor = None
        self.setObjectName("page_calibration")
        T.set_role(self, "page")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*([T.SPACE_LG] * 4))
        layout.setSpacing(T.SPACE_MD)
        title = QLabel("标定中心", self)
        title.setProperty("role", "heading")
        layout.addWidget(title)
        self.profile_note = QLabel(self)
        self.profile_note.setWordWrap(True)
        layout.addWidget(self.profile_note)
        tools = FlowLayout()
        self.common_button = QPushButton("通用标定", self)
        T.set_role(self.common_button, "primary")
        self.common_button.clicked.connect(self.open_common)
        tools.addWidget(self.common_button)
        self.manage_sizes_button = QPushButton("管理窗口尺寸", self)
        self.manage_sizes_button.clicked.connect(self.manage_sizes_requested.emit)
        tools.addWidget(self.manage_sizes_button)
        self.refresh_button = QPushButton("刷新配置", self)
        self.refresh_button.clicked.connect(self.refresh_config)
        tools.addWidget(self.refresh_button)
        layout.addLayout(tools)
        self.tasks = QComboBox(self)
        for name, title in calibration_tasks():
            self.tasks.addItem(title, name)
        layout.addWidget(self.tasks)
        self.open_button = QPushButton("打开标定", self)
        T.set_role(self.open_button, "primary")
        self.open_button.clicked.connect(self.open_task)
        layout.addWidget(self.open_button)
        self.status = QLabel("", self)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addStretch()
        self.refresh_config()

    def _busy(self):
        running = getattr(self.window(), "_selection_locked", lambda: False)
        if self._editor is not None or running():
            self.status.setText("请先停止运行中的任务或关闭编辑窗口。")
            return True
        return False

    def refresh_config(self):
        if not self._cfg_supplied:
            self.cfg = cfg_mod.load_config()
        size = calib.active_size(self.cfg)
        profile = calib.active(self.cfg)
        text = f"{size[0]}×{size[1]}" if size else "未记录"
        prefix = profile["label"] + " · " if profile else ""
        self.profile_note.setText("当前尺寸组：" + prefix + text)

    def _publish(self, cfg):
        self.cfg = cfg
        self.refresh_config()
        self.config_changed.emit(cfg)

    def open_task(self):
        self._open_task(self.tasks.currentData())

    def open_common(self):
        self._open_task("common")

    def _open_task(self, task_name):
        if self._busy():
            return
        dialog = CalibrationDialog(self, task_name, self.cfg if self._cfg_supplied else None)
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
