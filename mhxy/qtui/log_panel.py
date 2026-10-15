"""Qt 任务页共用的可折叠日志面板。

日志由主线程消费后交给这里渲染。这个控件不读取 runner 的队列，也不在
后台线程中更新 Qt 对象，因此可以和 ``RuntimeService`` 的统一事件流共用。
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import theme as T


class LogPanel(QWidget):
    """底部日志抽屉，默认收起并提供清空操作。

    ``append`` 接受旧日志流使用的 ``level`` 和 ``source``，来源标签会保留
    在每行文本中，级别只影响颜色。``append_event`` 可直接接收
    ``RuntimeService.drain_logs`` 返回的四元组。
    """

    LEVEL_COLORS = {
        "debug": T.TEXT_DIM,
        "info": T.TEXT,
        "hit": "#58c98b",
        "success": "#58c98b",
        "warn": "#e3b65a",
        "warning": "#e3b65a",
        "error": "#ed7474",
    }

    cleared = Signal()
    collapsed_changed = Signal(bool)

    def __init__(self, parent=None, *, collapsed=True, max_lines=5000):
        super().__init__(parent)
        self.max_lines = max(1, int(max_lines))
        self._collapsed = bool(collapsed)
        self.entries = []
        self.records = self.entries
        self.setObjectName("logPanel")
        self.setProperty("role", "log-panel")

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(T.SPACE_SM)
        self.toggle_button = QToolButton(self)
        self.toggle_button.setCheckable(True)
        self.toggle_button.setChecked(not self._collapsed)
        self.toggle_button.setText("日志")
        self.toggle_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle_button.toggled.connect(self.set_expanded)
        header.addWidget(self.toggle_button)
        self.title_label = QLabel("运行日志", self)
        self.title_label.setProperty("role", "muted")
        header.addWidget(self.title_label)
        header.addStretch(1)
        self.clear_button = QPushButton("清空", self)
        self.clear_button.clicked.connect(self.clear)
        header.addWidget(self.clear_button)

        self.text_edit = QPlainTextEdit(self)
        self.text_edit.setReadOnly(True)
        self.text_edit.setUndoRedoEnabled(False)
        self.text_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.text_edit.setObjectName("logText")
        self.text_edit.setMaximumBlockCount(self.max_lines)
        self.text_edit.setMinimumHeight(96)

        body = QVBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(T.SPACE_SM)
        body.addLayout(header)
        body.addWidget(self.text_edit)
        self._apply_collapsed()

    @property
    def is_collapsed(self):
        return self._collapsed

    def set_expanded(self, expanded):
        expanded = bool(expanded)
        if self._collapsed == (not expanded):
            self._apply_collapsed()
            return
        self._collapsed = not expanded
        self._apply_collapsed()
        self.collapsed_changed.emit(self._collapsed)

    def set_collapsed(self, collapsed=True):
        self.set_expanded(not collapsed)

    def _apply_collapsed(self):
        self.text_edit.setVisible(not self._collapsed)
        self.toggle_button.setArrowType(
            Qt.ArrowType.RightArrow if self._collapsed else Qt.ArrowType.DownArrow
        )
        self.toggle_button.setChecked(not self._collapsed)

    def append(self, message, level="info", source=""):
        """在主线程追加一行日志，并保留来源标签。"""
        text = str(message)
        prefix = f"[{source}] " if source else ""
        line = prefix + text
        self.entries.append((str(level), text, str(source) if source else ""))
        if len(self.entries) > self.max_lines:
            del self.entries[:-self.max_lines]
        cursor = self.text_edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if not self.text_edit.document().isEmpty():
            cursor.insertBlock()
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(self.LEVEL_COLORS.get(str(level).lower(), T.TEXT)))
        cursor.insertText(line, fmt)
        self.text_edit.setTextCursor(cursor)
        self.text_edit.ensureCursorVisible()
        # maximumBlockCount normally handles this; explicitly trim when a custom
        # document is supplied by a test or caller.
        while self.text_edit.blockCount() > self.max_lines:
            old = self.text_edit.textCursor()
            old.movePosition(QTextCursor.MoveOperation.Start)
            old.select(QTextCursor.SelectionType.BlockUnderCursor)
            old.removeSelectedText()
            old.deleteChar()

    append_log = append

    def append_event(self, event, *args):
        if args:
            event = (event, *args)
        if len(event) >= 4:
            _runner, level, message, source = event[:4]
        elif len(event) == 3:
            _runner, level, message = event
            source = ""
        else:
            raise ValueError("日志事件必须是三元组或四元组")
        self.append(message, level, source)

    append_line = append

    def clear(self):
        self.text_edit.clear()
        self.entries.clear()
        self.cleared.emit()

    clear_logs = clear

    def toPlainText(self):
        return self.text_edit.toPlainText()
