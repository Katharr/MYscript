"""Qt 共用主题令牌；页面只设置语义属性，不各自复制样式。"""

from PySide6.QtGui import QColor, QFont, QPalette
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


LIGHT_COLORS = {
    BG: "#f2f4f8", SIDEBAR: "#e8ecf3", SURFACE: "#ffffff",
    BORDER: "#c4ccd8", TEXT: "#202837", TEXT_DIM: "#586579",
    ACCENT: "#2465ce", BUTTON_HOVER: "#dce6f5",
}
_DARK_TOKENS = {name: globals()[name] for name in
                ("BG", "SIDEBAR", "SURFACE", "BORDER", "TEXT", "TEXT_DIM", "ACCENT", "BUTTON_HOVER")}


def apply_theme(app: QApplication, appearance="dark") -> None:
    app.setStyle("Fusion")
    app.setFont(QFont(FONT_FAMILY, FONT_SIZE))
    light = appearance == "light"
    colors = LIGHT_COLORS if light else {}
    # 已创建页同步其局部样式；后创建的页面和对话框读取当前主题令牌。
    replacements = LIGHT_COLORS if light else {v: k for k, v in LIGHT_COLORS.items()}
    for widget in app.allWidgets():
        local_style = widget.styleSheet()
        if local_style:
            for before, after in replacements.items():
                local_style = local_style.replace(before, after)
            widget.setStyleSheet(local_style)
    for name, dark in _DARK_TOKENS.items():
        globals()[name] = colors.get(dark, dark)
    palette = QPalette()
    for role, color in ((QPalette.ColorRole.Window, BG), (QPalette.ColorRole.Base, SURFACE),
                        (QPalette.ColorRole.AlternateBase, SIDEBAR), (QPalette.ColorRole.Text, TEXT),
                        (QPalette.ColorRole.WindowText, TEXT), (QPalette.ColorRole.ButtonText, TEXT),
                        (QPalette.ColorRole.Button, SURFACE), (QPalette.ColorRole.Highlight, ACCENT),
                        (QPalette.ColorRole.HighlightedText, "#ffffff"),
                        (QPalette.ColorRole.ToolTipBase, SURFACE), (QPalette.ColorRole.ToolTipText, TEXT)):
        palette.setColor(role, QColor(colors.get(color, color)))
    app.setPalette(palette)
    style = STYLE_SHEET
    for dark, bright in colors.items():
        style = style.replace(dark, bright)
    app.setStyleSheet(style)
    app.setProperty("appearance", "light" if light else "dark")
