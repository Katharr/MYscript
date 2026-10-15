"""Qt 共用主题令牌；页面只设置语义属性，不各自复制样式。"""

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication


BG = "#0e1014"
SIDEBAR = "#13161c"
SURFACE = "#181c24"
BORDER = "#2a313d"
TEXT = "#e7eaf0"
TEXT_DIM = "#8a93a3"
ACCENT = "#4f8cff"
BUTTON_HOVER = "#20252f"
FONT_FAMILY = "Microsoft YaHei UI"
FONT_SIZE = 10
SPACE_SM = 8
SPACE_MD = 16
SPACE_LG = 24
BUTTON_HEIGHT = 36
NAV_WIDTH = 152
WINDOW_SIZE = (1120, 720)
MIN_WINDOW_SIZE = (800, 640)

STYLE_SHEET = f"""
QWidget {{ color: {TEXT}; }}
QMainWindow, QWidget#shell {{ background: {BG}; }}
QFrame#navigation {{
    background: {SIDEBAR};
    border-right: 1px solid {BORDER};
}}
QLabel[role="brand"] {{ font-size: 16px; font-weight: 600; }}
QLabel[role="heading"] {{ font-size: 24px; font-weight: 600; }}
QLabel[role="muted"] {{ color: {TEXT_DIM}; }}
QWidget[role="page"] {{ background: {SURFACE}; border-radius: 12px; }}
QPushButton {{
    min-height: {BUTTON_HEIGHT}px;
    padding: 0 {SPACE_MD}px;
    border: 1px solid {BORDER};
    border-radius: 8px;
    background: {SURFACE};
}}
QPushButton:hover {{ background: {BUTTON_HOVER}; }}
QPushButton:focus {{ border: 1px solid {ACCENT}; }}
QPushButton[role="navigation"] {{ text-align: left; border-color: transparent; }}
QPushButton[role="navigation"]:checked {{
    background: {BUTTON_HOVER}; color: {ACCENT}; border-color: {ACCENT};
}}
QStatusBar {{
    background: {SIDEBAR}; color: {TEXT_DIM}; border-top: 1px solid {BORDER};
}}
QStatusBar::item {{ border: none; }}
"""


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    app.setFont(QFont(FONT_FAMILY, FONT_SIZE))
    app.setStyleSheet(STYLE_SHEET)
