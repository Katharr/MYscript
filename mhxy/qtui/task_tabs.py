"""可滚动的原生任务标签，补足 Home / End 键盘导航。"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTabBar, QSizePolicy


class TaskTabs(QTabBar):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('taskTabs')
        self.setExpanding(False)
        self.setUsesScrollButtons(True)
        self.setElideMode(Qt.TextElideMode.ElideNone)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setAccessibleName('单项任务')

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Home, Qt.Key.Key_End):
            self.setCurrentIndex(0 if event.key() == Qt.Key.Key_Home else self.count() - 1)
            event.accept()
        else:
            super().keyPressEvent(event)
