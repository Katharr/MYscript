"""可选悬浮信息条；关闭和展开均交回主窗口，不退出应用。"""

import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)
from . import theme as T


class FloatLogWindow(QWidget):
    restore_requested = Signal()
    stop_requested = Signal()
    STATUS_NAMES = {
        "idle": "待机", "running": "运行中", "stopping": "停止中",
        "completed": "已完成", "stopped": "已停止", "failed": "失败",
        "timeout": "超时", "partial": "部分完成",
    }

    def __init__(self, preferences=None):
        super().__init__(None, Qt.WindowType.Window)
        preferences = preferences or {}
        self._disposing = False
        self.setWindowTitle("梦幻小助手 · 悬浮信息条")
        self.setMinimumSize(380, 130)
        self.resize(520, 140)
        layout = QVBoxLayout(self)
        self.state_label = QLabel("待机", self)
        self.state_label.setWordWrap(True)
        layout.addWidget(self.state_label)
        self.message_label = QLabel("运行日志", self)
        self.message_label.setWordWrap(True)
        self.message_label.setProperty("role", "muted")
        layout.addWidget(self.message_label, 1)
        row = QHBoxLayout()
        self.topmost = QCheckBox("置顶", self)
        self.topmost.setChecked(bool(preferences.get("topmost", True)))
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, self.topmost.isChecked())
        self.topmost.toggled.connect(self._set_topmost)
        row.addWidget(self.topmost)
        row.addStretch()
        self.stop_button = QPushButton("停止", self)
        T.set_role(self.stop_button, "danger")
        self.stop_button.clicked.connect(self.stop_requested.emit)
        row.addWidget(self.stop_button)
        self.expand_button = QPushButton("展开", self)
        self.expand_button.clicked.connect(self.restore_requested.emit)
        row.addWidget(self.expand_button)
        layout.addLayout(row)
        self._restore_position(preferences.get("geometry", ""))
        self.update_state("idle", "")

    def _restore_position(self, geometry):
        screen = QApplication.primaryScreen().availableGeometry()
        match = re.fullmatch(r"(\d+)x(\d+)([+-]\d+)([+-]\d+)", str(geometry))
        if match:
            width, height, x, y = map(int, match.groups())
            for candidate in QApplication.screens():
                if candidate.availableGeometry().contains(x, y):
                    screen = candidate.availableGeometry()
                    break
            self.resize(min(max(380, width), screen.width()), min(max(130, height), screen.height()))
        else:
            x, y = screen.right() - self.width() - 20, screen.top() + 20
        self.move(max(screen.left(), min(x, screen.right() - self.width() + 1)),
                  max(screen.top(), min(y, screen.bottom() - self.height() + 1)))

    def preferences(self):
        return {"geometry": f"{self.width()}x{self.height()}{self.x():+d}{self.y():+d}",
                "topmost": self.topmost.isChecked()}

    def _set_topmost(self, checked):
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, checked)
        self.show()

    def update_state(self, phase, label):
        self.state_label.setText(f"{label or '梦幻小助手'} · {self.STATUS_NAMES.get(phase, phase)}")
        self.stop_button.setEnabled(phase == "running")

    def append(self, message, level="info", source=""):
        self.message_label.setText(((f"[{source}] " if source else "") + str(message))[:240])
        T.set_tone(self.message_label, {"error": "error", "warn": "warning", "warning": "warning",
                                      "hit": "success", "success": "success"}.get(level, ""))

    def dispose(self):
        self._disposing = True
        self.close()
        self.deleteLater()

    def closeEvent(self, event):
        if self._disposing:
            event.accept()
        else:
            event.ignore()
            self.restore_requested.emit()
