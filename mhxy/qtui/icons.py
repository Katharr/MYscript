"""工作台使用的统一线性图标；SVG 由 Qt 原生绘制。"""
from functools import lru_cache
from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

PATHS = {
    'daily': '<path d="M4 5h6v6H4zM14 13h6v6h-6zM10 8h7v5M7 11v5h7"/>',
    'tasks': '<path d="M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z"/>',
    'general': '<path d="m5 19 9-9M14 4a5 5 0 0 0 6 6l-4 4-3-3-8 9-3-3 9-8-1-3z"/>',
    'freeclick': '<path d="m6 3 13 11-7 1-3 6zM13 15l4 6"/>',
    'role_window': '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 8h18"/>',
    'calibration': '<circle cx="12" cy="12" r="6"/><path d="M12 2v6m0 8v6M2 12h6m8 0h6"/>',
    'settings': '<path d="M3 6h18M3 12h18M3 18h18M8 3v6m8 0v6M8 15v6"/>',
    'about': '<circle cx="12" cy="12" r="9"/><path d="M12 10v7m0-11v1"/>',
    'treasure_map': '<path d="m3 5 6-2 6 2 6-2v16l-6 2-6-2-6 2zM9 3v16M15 5v16"/>',
    'escort': '<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6zM8 12l3 3 5-6"/>',
    'secret_realm': '<path d="m3 20 6-12 4 6 3-9 5 15zM6 4h1m2-2v1"/>',
    'dungeon': '<path d="M4 21V8l8-5 8 5v13M2 21h20M9 21V11h6v10M7 8h10"/>',
    'sniper': '<circle cx="12" cy="12" r="7"/><path d="M12 2v6m0 8v6M2 12h6m8 0h6"/>',
}


@lru_cache(maxsize=128)
def line_icon(name, color='#68717D', size=18):
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><g fill="none" stroke="{color}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">{PATHS.get(name, PATHS["settings"])}</g></svg>'
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    QSvgRenderer(QByteArray(svg.encode())).render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)
