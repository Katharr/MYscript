"""脚本优先的右侧日志布局；窄窗仅覆盖展开，不挤压任务页。"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QSizePolicy, QSplitter, QToolButton, QVBoxLayout, QWidget,
)

from . import theme as T


class LogWorkspace(QWidget):
    def __init__(self, script, log_panel, settings, parent=None):
        super().__init__(parent)
        self.script = script
        self.log_panel = log_panel
        self.settings = settings
        try:
            self.preferred_width = max(T.LOG_MIN_WIDTH, min(
                T.LOG_MAX_WIDTH, settings.value("window/logWidth", T.LOG_WIDTH, type=int)))
        except (TypeError, ValueError):
            self.preferred_width = T.LOG_WIDTH
        self.expanded = settings.value("window/logExpanded", True, type=bool)
        self.overlay_mode = False
        self.overlay_open = False
        self._script_minimum = T.SCRIPT_COMFORT_WIDTH
        self._syncing = False
        self._mode_initialized = False

        row = QHBoxLayout(self)
        row.setContentsMargins(0, T.SPACE_MD, 0, T.SPACE_MD)
        row.setSpacing(T.LOG_HANDLE_WIDTH)
        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.splitter.setObjectName("logSplitter")
        self.splitter.setHandleWidth(T.LOG_HANDLE_WIDTH)
        self.splitter.setChildrenCollapsible(False)
        script.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.splitter.addWidget(script)
        self.splitter.addWidget(log_panel)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)
        self.splitter.splitterMoved.connect(self._dragged)
        row.addWidget(self.splitter, 1)

        self.rail = QWidget(self)
        self.rail.setFixedWidth(T.LOG_RAIL_WIDTH)
        rail_layout = QVBoxLayout(self.rail)
        rail_layout.setContentsMargins(0, 0, 0, 0)
        rail_layout.setSpacing(T.SPACE_SM)
        self.open_button = QToolButton(self.rail)
        self.open_button.setObjectName("logRailButton")
        self.open_button.setText("日\n志")
        self.open_button.setAccessibleName("展开右侧运行日志")
        self.open_button.setToolTip("展开运行日志")
        self.open_button.clicked.connect(self.open_log)
        rail_layout.addWidget(self.open_button)
        self.alert_label = QLabel(self.rail)
        self.alert_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.alert_label.setProperty("tone", "warning")
        rail_layout.addWidget(self.alert_label)
        rail_layout.addStretch()
        row.addWidget(self.rail)
        self.rail.hide()
        log_panel.collapsed_changed.connect(self._collapsed)
        log_panel.alerts_changed.connect(self._show_alerts)
        self.escape = QShortcut(QKeySequence(Qt.Key.Key_Escape), log_panel)
        self.escape.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.escape.activated.connect(self.close_log)
        self._show_alerts(0)

    def set_page(self, page):
        # 使用当前页的最小布局需求，不能让隐藏页面扩大所有页面的预算。
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
            overlay = self.width() < self._script_minimum + T.LOG_HANDLE_WIDTH + T.LOG_MIN_WIDTH
            changed_mode = not self._mode_initialized or overlay != self.overlay_mode
            self._mode_initialized = True
            if changed_mode:
                self.overlay_mode = overlay
                self.overlay_open = False
                if overlay:
                    self.log_panel.setParent(self)
                else:
                    self.splitter.addWidget(self.log_panel)
                    self.splitter.setStretchFactor(1, 0)
            visible = self.overlay_open if overlay else self.expanded
            self.script.setMinimumWidth(0 if overlay or not visible else self._script_minimum)
            self.rail.setVisible(overlay or not visible)
            self.log_panel.set_expanded(visible)
            self.log_panel.setVisible(visible)
            self.layout().activate()
            if overlay:
                width = min(self.preferred_width, self.width())
                self.log_panel.setMinimumWidth(0)
                self.log_panel.setMaximumWidth(T.LOG_MAX_WIDTH)
                self.log_panel.setGeometry(self.width() - width, T.SPACE_MD,
                                           width, max(0, self.height() - 2 * T.SPACE_MD))
                if visible:
                    self.log_panel.raise_()
            elif visible:
                maximum = min(T.LOG_MAX_WIDTH, self.splitter.width() - T.LOG_HANDLE_WIDTH - self._script_minimum)
                width = max(T.LOG_MIN_WIDTH, min(self.preferred_width, maximum))
                self.log_panel.setMinimumWidth(T.LOG_MIN_WIDTH)
                self.log_panel.setMaximumWidth(max(T.LOG_MIN_WIDTH, maximum))
                self.splitter.setSizes([self.splitter.width() - T.LOG_HANDLE_WIDTH - width, width])
        finally:
            self._syncing = False

    def _dragged(self, _position, _index):
        if not self._syncing and not self.overlay_mode and self.expanded:
            self.preferred_width = max(T.LOG_MIN_WIDTH, min(T.LOG_MAX_WIDTH, self.log_panel.width()))

    def _collapsed(self, collapsed):
        if not self._syncing:
            if collapsed:
                self.close_log()
            else:
                self.open_log()

    def open_log(self):
        if self.overlay_mode:
            self.overlay_open = True
        else:
            self.expanded = True
        self._relayout()
        self.log_panel.mark_read()
        self.log_panel.toggle_button.setFocus()

    def close_log(self):
        if self.overlay_mode:
            self.overlay_open = False
        else:
            self.expanded = False
        self._relayout()
        self.open_button.setFocus()

    def _show_alerts(self, count):
        self.alert_label.setText(str(count))
        self.alert_label.setVisible(bool(count))
        self.open_button.setAccessibleName(f"展开右侧运行日志，{count} 条未读警告或错误")

    def save_settings(self):
        # 自动进入窄窗抽屉不覆盖宽窗的用户偏好。
        self.settings.setValue("window/logWidth", self.preferred_width)
        self.settings.setValue("window/logExpanded", self.expanded)
