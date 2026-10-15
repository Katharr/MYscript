"""按 HWND 复用角色项；灯表示检测状态，边框表示已保存的窗口选择。"""

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QScrollArea, QWidget

from . import theme as T


@dataclass(frozen=True)
class WindowState:
    hwnd: int
    label: str
    status: str = "pending"
    minimized: bool = False


STATUS_COLORS = {"ready": "#58c98b", "pending": "#e3b65a", "failed": "#ed7474"}
STATUS_NAMES = {"ready": "可用", "pending": "身份未确认", "failed": "检测失败"}


class RoleItem(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._value = None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(T.SPACE_SM)
        self.light = QLabel("●", self)
        self.name = QLabel(self)
        self.name.setTextFormat(Qt.TextFormat.PlainText)
        self.captain = QLabel("队长", self)
        self.captain.setProperty("role", "muted")
        layout.addWidget(self.light)
        layout.addWidget(self.name)
        layout.addWidget(self.captain)

    def update_state(self, state: WindowState, selected: bool, captain: bool):
        value = (state, selected, captain)
        if value == self._value:
            return
        self._value = value
        self.name.setText(state.label)
        self.light.setStyleSheet(f"color: {STATUS_COLORS[state.status]};")
        self.captain.setVisible(captain)
        self.setProperty("selected", selected)
        border = T.ACCENT if selected else T.BORDER
        self.setStyleSheet(f"RoleItem {{ background: {T.SURFACE}; border: 1px solid {border}; border-radius: 8px; }}")
        self.setToolTip(STATUS_NAMES[state.status] + (" · 已最小化" if state.minimized else ""))


class RoleStrip(QScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.items: dict[int, RoleItem] = {}
        self._order = []
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFixedHeight(70)
        body = QWidget(self)
        body.setObjectName("roleStripBody")
        self.setStyleSheet(f"QScrollArea {{ background: {T.BG}; }} QWidget#roleStripBody {{ background: {T.BG}; }}")
        self.row = QHBoxLayout(body)
        self.row.setContentsMargins(0, 0, 0, 0)
        self.row.setSpacing(T.SPACE_SM)
        self.empty = QLabel("检测中…", body)
        self.empty.setProperty("role", "muted")
        self.row.addWidget(self.empty)
        self.row.addStretch()
        self.setWidget(body)

    def update_windows(self, states, selected=(), captain=None, error=""):
        order = [state.hwnd for state in states]
        for hwnd in list(self.items):
            if hwnd not in order:
                item = self.items.pop(hwnd)
                self.row.removeWidget(item)
                item.deleteLater()
        for index, state in enumerate(states):
            item = self.items.get(state.hwnd)
            if item is None:
                item = self.items[state.hwnd] = RoleItem(self.widget())
            if order != self._order:
                self.row.insertWidget(index, item)
            item.update_state(state, index in selected, index == captain)
        self._order = order
        self.empty.setVisible(not states)
        self.empty.setText("检测失败" if error else "未检测到游戏窗口")
