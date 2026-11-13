"""Shared full-height log splitter, with a temporary narrow-window overlay."""
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QSplitter, QWidget
from . import theme as T


class LogWorkspace(QWidget):
    def __init__(self, script, log_panel, settings, parent=None):
        super().__init__(parent)
        self.script, self.log_panel, self.settings = script, log_panel, settings
        try:
            self.preferred_width = int(settings.value('window/logWidth', 0))
        except (TypeError, ValueError):
            self.preferred_width = 0
        self.preferred_width = max(0, min(T.LOG_MAX_WIDTH, self.preferred_width))
        self.expanded = settings.value('window/logExpanded', True, type=bool)
        self.overlay_mode = self.overlay_open = False
        self._script_minimum = T.SCRIPT_COMFORT_WIDTH
        self._syncing = False
        self._mode_initialized = False
        self._alert_count = 0
        self._default_width = (T.WINDOW_SIZE[0] - T.NAV_WIDTH) // 3
        self.toolbar = self.toggle_button = None
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.splitter.setObjectName('logSplitter')
        self.splitter.setHandleWidth(T.LOG_HANDLE_WIDTH)
        self.splitter.setChildrenCollapsible(False)
        script.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.splitter.addWidget(script)
        self.splitter.addWidget(log_panel)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)
        self.splitter.splitterMoved.connect(self._dragged)
        row.addWidget(self.splitter)
        log_panel.collapsed_changed.connect(self._collapsed)
        log_panel.alerts_changed.connect(self._show_alerts)
        self.escape = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self.escape.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.escape.activated.connect(self.close_log)

    def bind_toolbar(self, toolbar, button):
        # The same toolbar stays above the drawer; its placeholder preserves page geometry.
        self.toolbar, self.toggle_button = toolbar, button
        self.toolbar_slot = QWidget(self.script)
        self.toolbar_slot.setFixedHeight(max(47, toolbar.sizeHint().height()))
        layout = self.script.layout()
        layout.replaceWidget(toolbar, self.toolbar_slot)
        toolbar.setParent(self)
        self.script.installEventFilter(self)
        toolbar.show()
        self._relayout()

    def eventFilter(self, watched, event):
        if watched is self.script and event.type() == QEvent.Type.Resize:
            self._position_toolbar()
        return super().eventFilter(watched, event)

    def _position_toolbar(self):
        if self.toolbar:
            self.toolbar.setGeometry(0, 0, self.script.width(), self.toolbar_slot.height())
            self.toolbar.raise_()

    def set_page(self, page):
        self._script_minimum = max(T.SCRIPT_COMFORT_WIDTH, page.minimumWidth(), page.minimumSizeHint().width())
        self._relayout()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout()

    def showEvent(self, event):
        super().showEvent(event)
        self._relayout()

    def _relayout(self):
        if self._syncing:
            return
        self._syncing = True
        try:
            minimum = max(T.LOG_MIN_WIDTH, self.log_panel.fontMetrics().horizontalAdvance('全部来源  跟随最新') + 2*T.LOG_PADDING + 64)
            preferred = self.preferred_width or max(minimum, self.width() // 3)
            overlay = self.width() < self._script_minimum + T.LOG_HANDLE_WIDTH + max(minimum, preferred)
            if not self._mode_initialized or overlay != self.overlay_mode:
                self._mode_initialized = True
                self.overlay_mode = overlay
                self.overlay_open = False
                if overlay:
                    self.log_panel.setParent(self)
                else:
                    self.splitter.addWidget(self.log_panel)
                    self.splitter.setStretchFactor(1, 0)
            if not overlay:
                self._default_width = preferred
            visible = self.overlay_open if overlay else self.expanded
            self.escape.setEnabled(visible)
            self.script.setMinimumWidth(0 if overlay or not visible else self._script_minimum)
            self.log_panel.set_expanded(visible)
            self.log_panel.setVisible(visible)
            self.layout().activate()
            if overlay:
                width = min(max(minimum, preferred), self.width())
                self.log_panel.setMinimumWidth(0)
                self.log_panel.setMaximumWidth(T.LOG_MAX_WIDTH)
                self.log_panel.setGeometry(self.width()-width, 0, width, self.height())
                if visible:
                    self.log_panel.raise_()
            elif visible:
                maximum = min(T.LOG_MAX_WIDTH, self.splitter.width()-T.LOG_HANDLE_WIDTH-self._script_minimum)
                width = max(minimum, min(preferred, maximum))
                self.log_panel.setMinimumWidth(minimum)
                self.log_panel.setMaximumWidth(max(minimum, maximum))
                self.splitter.setSizes([self.splitter.width()-T.LOG_HANDLE_WIDTH-width, width])
            top = self.toolbar_slot.height() if self.toolbar and overlay else T.LOG_PADDING
            self.log_panel.layout().setContentsMargins(T.LOG_PADDING, top + (T.LOG_PADDING if overlay else 0), T.LOG_PADDING, T.SPACE_SM)
            self._position_toolbar()
            self._show_alerts(self._alert_count)
        finally:
            self._syncing = False

    def _dragged(self, _position, _index):
        if not self._syncing and not self.overlay_mode and self.expanded:
            self.preferred_width = self.log_panel.width()

    def _collapsed(self, collapsed):
        if not self._syncing:
            (self.close_log if collapsed else self.open_log)()

    def open_log(self):
        if self.overlay_mode:
            self.overlay_open = True
        else:
            self.expanded = True
        self._relayout()
        self.log_panel.mark_read()
        self.log_panel.text_edit.setFocus()

    def close_log(self):
        if self.overlay_mode:
            self.overlay_open = False
        else:
            self.expanded = False
        self._relayout()
        if self.toggle_button:
            self.toggle_button.setFocus()

    def _show_alerts(self, count):
        self._alert_count = count
        if self.toggle_button:
            visible = self.overlay_open if self.overlay_mode else self.expanded
            self.toggle_button.setChecked(visible)
            self.toggle_button.setText(f'日志 ({count})' if count else '日志')
            self.toggle_button.setAccessibleName(('收起' if visible else '展开') + f'运行日志，{count} 条未读警告或错误')

    def save_settings(self):
        width = self.preferred_width or self._default_width
        self.settings.setValue('window/logWidth', width)
        self.settings.setValue('window/logExpanded', self.expanded)
