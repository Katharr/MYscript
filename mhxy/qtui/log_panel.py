"""Qt 任务页共用的可折叠日志面板。

日志由主线程消费后交给这里渲染。这个控件不读取 runner 的队列，也不在
后台线程中更新 Qt 对象，因此可以和 ``RuntimeService`` 的统一事件流共用。
"""

from collections import Counter
from datetime import datetime
import re

from PySide6.QtCore import QEvent, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox, QGridLayout, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
    QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)
from .widgets import ComboBox as QComboBox, set_field_width

from . import theme as T


class LogPanel(QWidget):
    """全局右侧日志与独立任务页共用，旧嵌入模式仍默认收起。

    ``append`` 接受旧日志流使用的 ``level`` 和 ``source``，来源标签会保留
    在每行文本中，侧栏增加接收时间和警告/错误标识。``append_event`` 可直接接收
    ``RuntimeService.drain_logs`` 返回的四元组。
    """

    cleared = Signal()
    collapsed_changed = Signal(bool)
    message_added = Signal(str, str, str)
    alerts_changed = Signal(int)
    MAX_MESSAGE_CHARS = 4096
    MAX_SOURCES = 64

    def __init__(self, parent=None, *, collapsed=True, max_lines=5000, sidebar=False):
        super().__init__(parent)
        self.sidebar = sidebar
        self.max_lines = max(1, int(max_lines))
        self._collapsed = bool(collapsed)
        self.entries = []
        self.records = self.entries
        self._timestamps = []
        self._unread_flags = []
        self._unread_alerts = 0
        self._following = True
        self._new_count = 0
        self._updating = False
        self._sources = Counter()
        self._source_blocks = Counter()
        self._dirty = False
        self.setObjectName("logSidebar" if sidebar else "logPanel")
        if sidebar:
            self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
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
        self.source_filter = QComboBox(self)
        set_field_width(self.source_filter, "short")
        self.source_filter.addItem("全部来源", None)
        self.source_filter.setMinimumWidth(110)
        header.addWidget(self.source_filter)
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
        self.text_edit.setMaximumHeight(180)

        body = QVBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(T.SPACE_SM)
        body.addLayout(header)
        body.addWidget(self.text_edit)
        self.follow_checkbox = QCheckBox("跟随最新", self)
        self.follow_checkbox.setChecked(True)
        self.follow_checkbox.toggled.connect(self.set_following)
        self.count_label = QLabel(self)
        self.count_label.setProperty("role", "muted")
        self.new_button = QPushButton(self)
        self.new_button.clicked.connect(lambda: self.set_following(True))
        self.new_button.hide()
        if sidebar:
            body.setContentsMargins(T.LOG_PADDING, T.LOG_PADDING, T.LOG_PADDING, T.SPACE_SM)
            header.removeWidget(self.source_filter)
            header.removeWidget(self.title_label)
            header.removeWidget(self.toggle_button)
            header.insertWidget(0, self.title_label)
            self.title_label.setText("日志")
            self.title_label.setProperty("role", "section")
            header.addWidget(self.toggle_button)
            self.toggle_button.setText("收起")
            self.toggle_button.setAccessibleName("收起右侧运行日志")
            self.source_filter.setMinimumWidth(100)
            self.source_filter.setMaximumWidth(16777215)
            self.source_filter.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            self.source_filter.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            self.source_filter.setMinimumContentsLength(4)
            self.source_filter.currentTextChanged.connect(self.source_filter.setToolTip)
            self.source_filter.setToolTip(self.source_filter.currentText())
            self.tools = QGridLayout()
            self.tools.setContentsMargins(0, 0, 0, 0)
            self.tools.setSpacing(T.SPACE_SM)
            self.tools.addWidget(self.source_filter, 0, 0)
            self.tools.addWidget(self.follow_checkbox, 0, 1)
            self.tools.setColumnStretch(0, 1)
            body.insertLayout(1, self.tools)
            self.text_edit.setMaximumHeight(16777215)
            self.text_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            body.setStretchFactor(self.text_edit, 1)
            body.addWidget(self.count_label)
            body.addWidget(self.new_button)
        else:
            self.follow_checkbox.hide()
            self.count_label.hide()
        self.source_filter.currentIndexChanged.connect(self._filter_changed)
        scrollbar = self.text_edit.verticalScrollBar()
        # valueChanged 也会由换行、窗口缩放和文档布局触发；只有用户操作暂停跟随。
        scrollbar.sliderMoved.connect(self._scrolled)
        scrollbar.actionTriggered.connect(lambda _action: self._scrolled(scrollbar.sliderPosition()))
        scrollbar.rangeChanged.connect(lambda *_: QTimer.singleShot(0, self._settle_follow))
        self.text_edit.installEventFilter(self)
        self.text_edit.viewport().installEventFilter(self)
        self.text_edit.selectionChanged.connect(self._selection_changed)
        self._update_footer()
        self._apply_collapsed()
        T.events().changed.connect(self._theme_changed)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.sidebar:
            available = self.width() - 2 * T.LOG_PADDING
            wrap = available < 100 + T.SPACE_SM + self.follow_checkbox.sizeHint().width()
            self.tools.removeWidget(self.follow_checkbox)
            self.tools.addWidget(self.follow_checkbox, 1 if wrap else 0, 0 if wrap else 1)
            self.tools.setColumnStretch(1, 0)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._collapsed:
            self.mark_read()

    def mark_read(self):
        if self._unread_alerts:
            self._unread_flags[:] = [False] * len(self.entries)
            self._unread_alerts = 0
            self.alerts_changed.emit(0)

    def set_following(self, following):
        self._following = bool(following)
        self.follow_checkbox.blockSignals(True)
        self.follow_checkbox.setChecked(self._following)
        self.follow_checkbox.blockSignals(False)
        if self._following:
            self._new_count = 0
            self._updating = True
            try:
                scrollbar = self.text_edit.verticalScrollBar()
                scrollbar.setValue(scrollbar.maximum())
            finally:
                self._updating = False
        self._update_footer()

    def _scrolled(self, value):
        if not self._updating and not self._collapsed:
            at_end = value == self.text_edit.verticalScrollBar().maximum()
            self.set_following(at_end and not self.text_edit.textCursor().hasSelection())

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Wheel or (
            event.type() == QEvent.Type.KeyPress and event.key() in {
                Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown,
                Qt.Key.Key_Home, Qt.Key.Key_End,
            }
        ):
            self.set_following(False)
            QTimer.singleShot(0, self._user_scrolled)
        return super().eventFilter(watched, event)

    def _user_scrolled(self):
        self._scrolled(self.text_edit.verticalScrollBar().value())

    def _settle_follow(self):
        if self._following and not self._collapsed:
            scrollbar = self.text_edit.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())

    def _selection_changed(self):
        if not self._updating and self.text_edit.textCursor().hasSelection():
            self.set_following(False)

    def _filter_changed(self, *_):
        self.set_following(True)
        self._render()

    def _update_footer(self):
        selected = self.source_filter.currentData()
        count = len(self.entries) if selected is None else self._sources[selected]
        state = "跟随最新" if self._following else "已暂停"
        self.count_label.setText(f"{count} 条记录 · {state}")
        self.new_button.setText(f"回到最新（{self._new_count}）")
        self.new_button.setVisible(self.sidebar and bool(self._new_count) and not self._collapsed)

    def _theme_changed(self):
        if self._collapsed:
            self._dirty = True
        else:
            self._render()

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
            Qt.ArrowType.RightArrow if self.sidebar or self._collapsed else Qt.ArrowType.DownArrow
        )
        self.toggle_button.setChecked(not self._collapsed)
        if not self._collapsed and self._dirty:
            self._render()
        self._update_footer()

    def append(self, message, level="info", source=""):
        """在主线程追加一行日志，并保留来源标签。"""
        text = str(message)[:self.MAX_MESSAGE_CHARS]
        source = str(source or "")[:120]
        level = str(level)[:24]
        previous_alerts = self._unread_alerts
        self.entries.append((level, text, source))
        self._timestamps.append(datetime.now().strftime("%H:%M:%S"))
        unread = level.lower() in {"warn", "warning", "error"} and (self._collapsed or not self.isVisible())
        self._unread_flags.append(unread)
        self._unread_alerts += unread
        self._sources[source] += 1
        self._source_blocks[source] += text.count("\n") + 1
        if len(self.entries) > self.max_lines:
            removed = self.entries.pop(0)
            self._timestamps.pop(0)
            self._unread_alerts -= self._unread_flags.pop(0)
            self._sources[removed[2]] -= 1
            self._source_blocks[removed[2]] -= removed[1].count("\n") + 1
            if not self._sources[removed[2]]:
                del self._sources[removed[2]]
                del self._source_blocks[removed[2]]
        filter_changed = self._sync_sources()
        self._dirty = True
        selected = self.source_filter.currentData()
        matches = selected is None or selected == source
        if matches and not self._following:
            self._new_count = min(self.max_lines, self._new_count + 1)
        if not self._collapsed:
            if filter_changed:
                self._render()
            else:
                self._updating = True
                scrollbar = self.text_edit.verticalScrollBar()
                position = scrollbar.value()
                try:
                    self._trim_visible(selected)
                    if matches:
                        self._insert(level, text, source, timestamp=self._timestamps[-1])
                    scrollbar.setValue(scrollbar.maximum() if self._following else position)
                finally:
                    self._updating = False
            self._dirty = False
        self._update_footer()
        if previous_alerts != self._unread_alerts:
            self.alerts_changed.emit(self._unread_alerts)
        self.message_added.emit(text, level, source)

    def _sync_sources(self):
        selected = self.source_filter.currentData()
        sources = sorted(s for s in self._sources if s)[:self.MAX_SOURCES]
        if sources == [self.source_filter.itemData(i) for i in range(1, self.source_filter.count())]:
            return False
        self.source_filter.blockSignals(True)
        self.source_filter.clear()
        self.source_filter.addItem("全部来源", None)
        for source in sources:
            self.source_filter.addItem(source, source)
        self.source_filter.setCurrentIndex(max(0, self.source_filter.findData(selected)))
        self.source_filter.blockSignals(False)
        self.source_filter.setToolTip(self.source_filter.currentText())
        # 当前来源被淘汰时，重建为全部来源。
        return selected != self.source_filter.currentData()

    def _insert(self, level, text, source, cursor=None, timestamp=""):
        prefix = ""
        source_label = f"[{source}] " if source else ""
        line = source_label + text
        if self.sidebar:
            severity = {"warn": "警告", "warning": "警告", "error": "错误"}.get(level.lower())
            line = (f"{severity} · " if severity else "") + text
            prefix = source_label
            if timestamp and not re.match(r"\s*\[?\d{2}:\d{2}:\d{2}", text):
                prefix = timestamp + " " + prefix
        if cursor is None:
            # 独立文档光标追加，不改变用户正在选择复制的范围。
            cursor = QTextCursor(self.text_edit.document())
            cursor.movePosition(QTextCursor.MoveOperation.End)
        if not self.text_edit.document().isEmpty():
            cursor.insertBlock()
        fmt = QTextCharFormat()
        if prefix:
            fmt.setForeground(QColor(T.TEXT_DIM))
            cursor.insertText(prefix, fmt)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(T.log_color(level)))
        cursor.insertText(line, fmt)
        # maximumBlockCount normally handles this; explicitly trim when a custom
        # document is supplied by a test or caller.
        while self.text_edit.blockCount() > self.max_lines:
            old = self.text_edit.textCursor()
            old.movePosition(QTextCursor.MoveOperation.Start)
            old.select(QTextCursor.SelectionType.BlockUnderCursor)
            old.removeSelectedText()
            old.deleteChar()

    def _render(self, *_):
        self._updating = True
        scrollbar = self.text_edit.verticalScrollBar()
        position = scrollbar.value()
        selection = self.text_edit.textCursor()
        anchor, endpoint = selection.anchor(), selection.position()
        self.text_edit.clear()
        self.text_edit.setMaximumBlockCount(self.max_lines)
        selected = self.source_filter.currentData()
        self.text_edit.setUpdatesEnabled(False)
        cursor = self.text_edit.textCursor()
        cursor.beginEditBlock()
        try:
            for (level, text, source), timestamp in zip(self.entries, self._timestamps):
                if selected is None or selected == source:
                    self._insert(level, text, source, cursor, timestamp)
        finally:
            cursor.endEditBlock()
            if not self._following:
                limit = self.text_edit.document().characterCount() - 1
                selection = QTextCursor(self.text_edit.document())
                selection.setPosition(min(anchor, limit))
                selection.setPosition(min(endpoint, limit), QTextCursor.MoveMode.KeepAnchor)
                self.text_edit.setTextCursor(selection)
            self._trim_visible(selected)
            scrollbar.setValue(scrollbar.maximum() if self._following else position)
            self.text_edit.setUpdatesEnabled(True)
            self._updating = False
        self._dirty = False
        self._update_footer()

    def _trim_visible(self, selected):
        # 筛选来源后的保留行数也随缓存淘汰而减少，避免每条新日志重建全文。
        blocks = (sum(self._source_blocks.values()) if selected is None
                  else self._source_blocks[selected])
        if not blocks:
            self.text_edit.clear()
        self.text_edit.setMaximumBlockCount(max(1, min(self.max_lines, blocks)))

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
        self._updating = True
        self.text_edit.clear()
        self.text_edit.setMaximumBlockCount(self.max_lines)
        self.entries.clear()
        self._timestamps.clear()
        self._unread_flags.clear()
        self._unread_alerts = 0
        self._new_count = 0
        self._sources.clear()
        self._source_blocks.clear()
        self._sync_sources()
        self._dirty = False
        self._updating = False
        self.set_following(True)
        self.alerts_changed.emit(0)
        self.cleared.emit()

    clear_logs = clear

    def toPlainText(self):
        if self._dirty:
            self._render()
        return self.text_edit.toPlainText()
