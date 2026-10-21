"""按 HWND 复用角色项；灯表示检测状态，边框表示已保存的窗口选择。"""

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QCheckBox, QFrame, QHBoxLayout, QLabel, QScrollArea, QSizePolicy, QStyle, QVBoxLayout, QWidget

from . import theme as T


@dataclass(frozen=True)
class WindowState:
    hwnd: int
    label: str
    status: str = "pending"
    minimized: bool = False
    size: tuple[int, int] | None = None


STATUS_NAMES = {"ready": "可用", "pending": "身份未确认", "failed": "检测失败"}
CARD_WIDTH = 180
CARD_HEIGHT = 68
CARD_GAP = 6


class _ElidedLabel(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.full_text = ""
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def set_full_text(self, text):
        self.full_text = text
        self.setToolTip(text)
        self._elide()

    def _elide(self):
        self.setText(self.fontMetrics().elidedText(self.full_text, Qt.TextElideMode.ElideRight, self.width()))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()


class RoleItem(QFrame):
    selection_toggled = Signal()
    captain_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("roleItem")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFixedWidth(CARD_WIDTH)
        self._interactive = None
        self._value = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 5, 8, 5)
        layout.setSpacing(0)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.selected_box = QCheckBox(self)
        self.selected_box.clicked.connect(self.selection_toggled.emit)
        self.name = _ElidedLabel(self)
        row.addWidget(self.selected_box)
        row.addWidget(self.name, 1)
        layout.addLayout(row)
        status_row = QHBoxLayout()
        status_row.setSpacing(4)
        self.light = QLabel("●", self)
        self.activity = _ElidedLabel(self)
        self.activity.set_full_text("空闲")
        self.activity.setMinimumHeight(self.fontMetrics().height() + 4)
        self.captain = QCheckBox("队长", self)
        self.captain.clicked.connect(self.captain_requested.emit)
        status_row.addWidget(self.light)
        status_row.addWidget(self.activity, 1)
        status_row.addWidget(self.captain)
        layout.addLayout(status_row)
        for label in (self.light, self.name, self.activity):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def set_interactive(self, enabled):
        if self._interactive == enabled:
            return
        self._interactive = enabled
        self.selected_box.setEnabled(enabled)
        self.captain.setEnabled(enabled)
        self.setCursor(Qt.CursorShape.PointingHandCursor if enabled else Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, event):
        if self._interactive and event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.selection_toggled.emit()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if self._interactive and event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return):
            self.selection_toggled.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def update_state(self, state: WindowState, selected: bool, captain: bool, activity=("空闲", "")):
        self.selected_box.setChecked(selected)
        self.captain.setChecked(captain)
        value = (state, selected, captain, activity)
        if value == self._value:
            return
        self._value = value
        self.name.set_full_text(state.label)
        self.selected_box.setAccessibleName("选择执行窗口：" + state.label)
        self.captain.setAccessibleName("设为队长：" + state.label)
        self.setAccessibleName(state.label)
        self.light.setText({"ready": "●", "pending": "?", "failed": "!"}[state.status])
        self.light.setAccessibleName(STATUS_NAMES[state.status])
        T.set_tone(self.light, {"ready": "success", "pending": "warning", "failed": "error"}[state.status])
        self.activity.set_full_text(activity[0])
        self.activity.setAccessibleName(activity[0])
        T.set_tone(self.activity, activity[1])
        if self.property("selected") != selected:
            self.setProperty("selected", selected)
            self.style().unpolish(self)
            self.style().polish(self)
            self.update()
        self.setToolTip(state.label + " · " + STATUS_NAMES[state.status] + (" · 已最小化" if state.minimized else ""))


class RoleStrip(QScrollArea):
    selection_toggled = Signal(int)
    captain_requested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("roleStrip")
        self.items: dict[int, RoleItem] = {}
        self._order = []
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget(self)
        body.setObjectName("roleStripBody")
        self.row = QHBoxLayout(body)
        self.row.setContentsMargins(0, 0, 0, 0)
        self.row.setSpacing(CARD_GAP)
        self.empty = QLabel("检测中…", body)
        self.empty.setProperty("role", "muted")
        self.row.addWidget(self.empty)
        self.row.addStretch()
        self.setWidget(body)
        self._update_height()
        T.events().changed.connect(self._update_height)

    def _update_height(self):
        scroll_height = self.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent)
        for item in self.items.values():
            item.ensurePolished()
            height = max(CARD_HEIGHT, item.minimumSizeHint().height())
            if item.height() != height:
                item.setFixedHeight(height)
        content_height = max((item.height() for item in self.items.values()), default=CARD_HEIGHT)
        needed = len(self._order) * CARD_WIDTH + max(0, len(self._order) - 1) * CARD_GAP
        overflow = needed > self.viewport().width()
        height = content_height + 2 + (scroll_height if overflow else 0) if self._order else max(32, self.fontMetrics().height() + 12)
        if self.height() != height:
            self.setFixedHeight(height)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_height()

    def update_windows(self, states, selected=(), captain=None, error="", *, activities=None, locked=False):
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
                item.selection_toggled.connect(lambda h=state.hwnd: self.selection_toggled.emit(h))
                item.captain_requested.connect(lambda h=state.hwnd: self.captain_requested.emit(h))
            if order != self._order:
                self.row.insertWidget(index, item)
            item.update_state(state, index in selected, index == captain, (activities or {}).get(state.hwnd, ("空闲", "")))
            item.set_interactive(not locked)
        self._order = order
        self._update_height()
        self.empty.setVisible(not states)
        self.empty.setText("检测失败" if error else "未检测到游戏窗口")
