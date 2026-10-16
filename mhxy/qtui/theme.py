"""Qt 的语义色板、控件尺寸与原生勾选框绘制。"""

from PySide6.QtCore import QObject, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPalette, QPen
from PySide6.QtWidgets import QApplication, QProxyStyle, QStyle, QStyleFactory, QStyleOption

FONT_FAMILY = "Microsoft YaHei UI"
FONT_SIZE = 11
SPACE_SM = 8
SPACE_MD = 16
SPACE_LG = 24
BUTTON_HEIGHT = 36
ROW_HEIGHT = 44
NAV_WIDTH = 160
WINDOW_SIZE = (1120, 720)
MIN_WINDOW_SIZE = (800, 640)

PALETTES = {
    "light": dict(
        BG="#f3f6fb", SIDEBAR="#e9eff7", SURFACE="#ffffff", INPUT="#ffffff",
        BORDER="#7d8ba0", DIVIDER="#d4dce8", TEXT="#1f2937", TEXT_DIM="#526176",
        ACCENT="#2563eb", ON_ACCENT="#ffffff", BUTTON_HOVER="#e5edfb",
        SELECTION="#dce9ff", DISABLED="#64748b", DISABLED_BG="#edf1f6",
        SUCCESS="#16794b", WARNING="#946200", ERROR="#b42318",
    ),
    "dark": dict(
        BG="#181f2a", SIDEBAR="#1c2532", SURFACE="#222d3d", INPUT="#1b2533",
        BORDER="#7d8ba0", DIVIDER="#435167", TEXT="#edf2f8", TEXT_DIM="#b8c4d6",
        ACCENT="#82aaff", ON_ACCENT="#172132", BUTTON_HOVER="#34465f",
        SELECTION="#354e73", DISABLED="#9cabc0", DISABLED_BG="#293445",
        SUCCESS="#80d8a6", WARNING="#f2cb75", ERROR="#ffaaa3",
    ),
}
globals().update(PALETTES["light"])


class _ThemeEvents(QObject):
    changed = Signal()


def events():
    app = QApplication.instance()
    if not hasattr(app, "_mhxy_theme_events"):
        app._mhxy_theme_events = _ThemeEvents(app)
    return app._mhxy_theme_events


def set_role(widget, role):
    widget.setProperty("role", role)
    if role == "page":
        widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


def set_tone(widget, tone):
    if widget.property("tone") == tone:
        return
    widget.setProperty("tone", tone)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


def log_color(level):
    return {
        "debug": TEXT_DIM, "hit": SUCCESS, "success": SUCCESS,
        "warn": WARNING, "warning": WARNING, "error": ERROR,
    }.get(str(level).lower(), TEXT)


class _ReadableStyle(QProxyStyle):
    """统一勾选框、箭头与滚动条绘制，命中测试沿用原生几何。"""

    def pixelMetric(self, metric, option=None, widget=None):
        if metric == QStyle.PixelMetric.PM_ScrollBarExtent:
            return 24
        if metric == QStyle.PixelMetric.PM_ScrollBarSliderMin:
            return 40
        if metric in (QStyle.PixelMetric.PM_IndicatorWidth, QStyle.PixelMetric.PM_IndicatorHeight):
            return 20
        if metric == QStyle.PixelMetric.PM_CheckBoxLabelSpacing:
            return 10
        if metric in (QStyle.PixelMetric.PM_LayoutHorizontalSpacing,
                      QStyle.PixelMetric.PM_LayoutVerticalSpacing):
            return 12
        return super().pixelMetric(metric, option, widget)

    def drawComplexControl(self, control, option, painter, widget=None):
        if control != QStyle.ComplexControl.CC_ScrollBar:
            return super().drawComplexControl(control, option, painter, widget)
        # 使用原生 subcontrol 几何和命中测试，仅绘制更清楚的轨道、滑块和箭头。
        vertical = option.orientation == Qt.Orientation.Vertical
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(option.rect, QColor(BG))
        for part, arrow in (
            (QStyle.SubControl.SC_ScrollBarSubLine, QStyle.PrimitiveElement.PE_IndicatorArrowUp if vertical else QStyle.PrimitiveElement.PE_IndicatorArrowLeft),
            (QStyle.SubControl.SC_ScrollBarAddLine, QStyle.PrimitiveElement.PE_IndicatorArrowDown if vertical else QStyle.PrimitiveElement.PE_IndicatorArrowRight),
        ):
            rect = self.subControlRect(control, option, part, widget)
            active = bool(option.activeSubControls & part)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(BUTTON_HOVER if active else SIDEBAR))
            painter.drawRoundedRect(QRectF(rect).adjusted(1, 1, -1, -1), 4, 4)
            arrow_option = QStyleOption(option)
            arrow_option.rect = rect
            self.drawPrimitive(arrow, arrow_option, painter, widget)
        slider = self.subControlRect(control, option, QStyle.SubControl.SC_ScrollBarSlider, widget)
        slider = QRectF(slider).adjusted(4, 1, -4, -1) if vertical else QRectF(slider).adjusted(1, 4, -1, -4)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(TEXT_DIM if option.activeSubControls & QStyle.SubControl.SC_ScrollBarSlider else BORDER))
        painter.drawRoundedRect(slider, 5, 5)
        painter.restore()

    def drawPrimitive(self, element, option, painter, widget=None):
        directions = {
            QStyle.PrimitiveElement.PE_IndicatorArrowUp: ((-4, 2), (0, -2), (4, 2)),
            QStyle.PrimitiveElement.PE_IndicatorArrowDown: ((-4, -2), (0, 2), (4, -2)),
            QStyle.PrimitiveElement.PE_IndicatorArrowLeft: ((2, -4), (-2, 0), (2, 4)),
            QStyle.PrimitiveElement.PE_IndicatorArrowRight: ((-2, -4), (2, 0), (-2, 4)),
        }
        if element in directions:
            group = QPalette.ColorGroup.Active if option.state & QStyle.StateFlag.State_Enabled else QPalette.ColorGroup.Disabled
            color = QApplication.instance().palette().color(group, QPalette.ColorRole.Text)
            center = QRectF(option.rect).center()
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(color, 1.8, Qt.PenStyle.SolidLine,
                                Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            path = QPainterPath()
            for index, (x, y) in enumerate(directions[element]):
                (path.moveTo if index == 0 else path.lineTo)(center.x() + x, center.y() + y)
            painter.drawPath(path)
            painter.restore()
            return
        if element not in (QStyle.PrimitiveElement.PE_IndicatorCheckBox,
                           QStyle.PrimitiveElement.PE_IndicatorItemViewItemCheck):
            return super().drawPrimitive(element, option, painter, widget)
        enabled = bool(option.state & QStyle.StateFlag.State_Enabled)
        group = QPalette.ColorGroup.Active if enabled else QPalette.ColorGroup.Disabled
        # Item-view 的局部 Highlight 是整行浅选中色，勾选方框仍用主强调色。
        palette = QApplication.instance().palette()
        checked = bool(option.state & (QStyle.StateFlag.State_On | QStyle.StateFlag.State_NoChange))
        border = palette.color(group, QPalette.ColorRole.Highlight if checked else QPalette.ColorRole.Mid)
        fill = palette.color(group, QPalette.ColorRole.Highlight if checked else QPalette.ColorRole.Base)
        if enabled and option.state & QStyle.StateFlag.State_MouseOver:
            border = palette.color(group, QPalette.ColorRole.Highlight)
        rect = QRectF(option.rect).adjusted(1, 1, -1, -1)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(border, 2))
        painter.setBrush(fill)
        painter.drawRoundedRect(rect, 3, 3)
        if checked:
            painter.setPen(QPen(palette.color(group, QPalette.ColorRole.HighlightedText), 2.4,
                                Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            x, y, width, height = rect.x(), rect.y(), rect.width(), rect.height()
            path = QPainterPath()
            if option.state & QStyle.StateFlag.State_NoChange:
                path.moveTo(x + width * .25, y + height * .5)
                path.lineTo(x + width * .75, y + height * .5)
            else:
                path.moveTo(x + width * .2, y + height * .5)
                path.lineTo(x + width * .42, y + height * .73)
                path.lineTo(x + width * .8, y + height * .28)
            painter.drawPath(path)
        painter.restore()


def _style_sheet(font_height):
    content_height = max(BUTTON_HEIGHT - 12, font_height)
    accent_hover = QColor(ACCENT).darker(110).name()
    return f"""
    QWidget {{ color: {TEXT}; }}
    QMainWindow, QWidget#shell {{ background: {BG}; }}
    QDialog, QWidget[role="page"] {{ background: {SURFACE}; }}
    QWidget[role="page"] {{ border-radius: 10px; }}
    QFrame#navigation, QWidget#navigationBody {{ background: {SIDEBAR}; }}
    QFrame#navigation {{ border-right: 1px solid {DIVIDER}; }}
    QLabel[role="brand"] {{ font-size: 17px; font-weight: 600; }}
    QLabel[role="heading"] {{ font-size: 24px; font-weight: 600; }}
    QLabel[role="section"] {{ font-weight: 600; }}
    QLabel[role="muted"] {{ color: {TEXT_DIM}; }}
    QLabel[tone="success"] {{ color: {SUCCESS}; }}
    QLabel[tone="warning"] {{ color: {WARNING}; }}
    QLabel[tone="error"] {{ color: {ERROR}; }}
    QPushButton, QToolButton {{
        min-height: {content_height}px; padding: 5px 12px;
        border: 1px solid {BORDER}; border-radius: 6px; background: {SURFACE};
    }}
    QPushButton:hover, QToolButton:hover {{ background: {BUTTON_HOVER}; }}
    QPushButton:focus, QToolButton:focus {{ border-color: {ACCENT}; }}
    QPushButton[role="primary"] {{ background: {ACCENT}; color: {ON_ACCENT}; border-color: {ACCENT}; }}
    QPushButton[role="primary"]:hover {{ background: {accent_hover}; }}
    QPushButton[role="danger"] {{ color: {ERROR}; border-color: {ERROR}; }}
    QPushButton[role="navigation"] {{ text-align: left; border-color: transparent; background: transparent; }}
    QPushButton[role="navigation"]:hover {{ background: {BUTTON_HOVER}; }}
    QPushButton[role="navigation"]:checked, QPushButton[role="profile"]:checked {{
        background: {SELECTION}; color: {TEXT}; border-color: {ACCENT}; font-weight: 600;
    }}
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
        min-height: {content_height}px; padding: 5px 10px;
        border: 1px solid {BORDER}; border-radius: 5px; background: {INPUT};
        selection-background-color: {ACCENT}; selection-color: {ON_ACCENT};
    }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {ACCENT}; }}
    QComboBox {{ padding-right: 30px; }}
    QComboBox::drop-down {{ width: 26px; border: none; }}
    QComboBox::down-arrow {{ image: none; }}
    QAbstractSpinBox QLineEdit {{ min-height: 0; padding: 0; border: none; background: transparent; }}
    QToolButton[role="stepButton"] {{ min-height: 0; padding: 0; border: none; border-radius: 4px; background: transparent; }}
    QToolButton[role="stepButton"]:hover {{ background: {BUTTON_HOVER}; }}
    QToolButton[role="stepButton"]:focus {{ border: 1px solid {ACCENT}; }}
    QToolButton[role="stepButton"]:disabled {{ color: {DISABLED}; background: transparent; }}
    QComboBox QAbstractItemView {{
        background: {SURFACE}; color: {TEXT}; border: 1px solid {BORDER};
        selection-background-color: {SELECTION}; selection-color: {TEXT}; padding: 4px;
    }}
    QComboBox QAbstractItemView::item {{ min-height: 28px; padding: 4px 8px; }}
    QCheckBox {{ min-height: 32px; spacing: 10px; padding: 0 2px; border: 1px solid transparent; }}
    QCheckBox:focus {{ border-color: {ACCENT}; border-radius: 4px; }}
    QCheckBox::indicator, QAbstractItemView::indicator {{ width: 20px; height: 20px; }}
    QTableView, QListWidget, QTextBrowser, QPlainTextEdit {{
        background: {INPUT}; color: {TEXT}; border: 1px solid {DIVIDER};
        selection-background-color: {SELECTION}; selection-color: {TEXT};
        alternate-background-color: {BG}; gridline-color: {DIVIDER};
    }}
    QTextBrowser, QPlainTextEdit {{ padding: 8px; border-radius: 5px; }}
    QTableView::item {{ padding: 6px; }}
    QHeaderView::section {{
        background: {SIDEBAR}; color: {TEXT_DIM}; padding: 8px;
        border: none; border-bottom: 1px solid {DIVIDER};
    }}
    QListWidget::item {{ min-height: 26px; padding: 8px 10px; border-bottom: 1px solid {DIVIDER}; }}
    QListWidget::item:hover {{ background: {BUTTON_HOVER}; }}
    QListWidget::item:selected {{ background: {SELECTION}; color: {TEXT}; }}
    QScrollArea {{ border: none; background: transparent; }}
    QWidget#taskBody, QWidget#activityBody, QWidget#sniperBody,
    QWidget#settingsBody, QWidget#windowBody, QWidget#profileBody {{ background: {SURFACE}; }}
    QFrame#activityCard {{ background: {SURFACE}; border: 1px solid {DIVIDER}; border-radius: 8px; }}
    QScrollArea#roleStrip, QWidget#roleStripBody {{ background: {BG}; }}
    QFrame#roleItem {{ background: {SURFACE}; border: 1px solid {DIVIDER}; border-radius: 6px; }}
    QFrame#roleItem[selected="true"] {{ border-color: {ACCENT}; background: {SELECTION}; }}
    QPushButton:disabled, QToolButton:disabled, QLineEdit:disabled, QComboBox:disabled,
    QSpinBox:disabled, QDoubleSpinBox:disabled {{
        color: {DISABLED}; background: {DISABLED_BG}; border-color: {DIVIDER};
    }}
    QCheckBox:disabled {{ color: {DISABLED}; }}
    QStatusBar {{ background: {SIDEBAR}; color: {TEXT_DIM}; border-top: 1px solid {DIVIDER}; }}
    QStatusBar::item {{ border: none; }}
    QToolTip {{ color: {TEXT}; background: {SURFACE}; border: 1px solid {BORDER}; padding: 6px; }}
    """


def apply_theme(app: QApplication, appearance="light") -> None:
    appearance = "light" if appearance == "light" else "dark"
    globals().update(PALETTES[appearance])
    if not hasattr(app, "_mhxy_readable_style"):
        app._mhxy_readable_style = _ReadableStyle(QStyleFactory.create("Fusion"))
        app.setStyle(app._mhxy_readable_style)
    app.setFont(QFont(FONT_FAMILY, FONT_SIZE))
    palette = QPalette()
    roles = {
        "Window": SURFACE, "Base": INPUT, "AlternateBase": BG,
        "Text": TEXT, "WindowText": TEXT, "ButtonText": TEXT, "Button": SURFACE,
        "Mid": BORDER, "Dark": BORDER, "Highlight": ACCENT, "HighlightedText": ON_ACCENT,
        "ToolTipBase": SURFACE, "ToolTipText": TEXT, "PlaceholderText": TEXT_DIM,
    }
    for role, color in roles.items():
        palette.setColor(getattr(QPalette.ColorRole, role), QColor(color))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(DISABLED))
    for role in (QPalette.ColorRole.Base, QPalette.ColorRole.Button):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(DISABLED_BG))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Highlight, QColor(DISABLED))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.HighlightedText, QColor(DISABLED_BG))
    app.setPalette(palette)
    app.setStyleSheet(_style_sheet(QFontMetrics(app.font()).height()))
    app.setProperty("appearance", appearance)
    events().changed.emit()
