"""窗口管理入口；实际唤窗/关闭由主窗口在后台执行。"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from . import theme as T
from .widgets import FlowLayout


class WindowTools(QWidget):
    wake_requested = Signal()
    close_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        actions = FlowLayout()
        self.wake_button = QPushButton("唤出所有游戏窗口", self)
        self.wake_button.setToolTip("还原所有游戏窗口，并逐窗识别角色名；任务运行时请先停止。")
        self.wake_button.clicked.connect(self.wake_requested.emit)
        actions.addWidget(self.wake_button)
        self.close_button = QPushButton("关闭所有游戏窗口", self)
        T.set_role(self.close_button, "danger")
        self.close_button.clicked.connect(self.close_requested.emit)
        actions.addWidget(self.close_button)
        layout.addLayout(actions)
        self.status = QLabel(self)
        self.status.setWordWrap(True)
        self.status.hide()
        layout.addWidget(self.status)

    def update_state(self, action="", message="", level="info"):
        self.wake_button.setEnabled(not action)
        self.close_button.setEnabled(not action)
        self.wake_button.setText("正在唤出并识别…" if action == "wake" else "唤出所有游戏窗口")
        self.close_button.setText("正在关闭…" if action == "close" else "关闭所有游戏窗口")
        self.status.setText(message)
        T.set_tone(self.status, {"warn": "warning", "error": "error"}.get(level, ""))
        self.status.setVisible(bool(message))
