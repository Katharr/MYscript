"""Qt 冻结屏幕框选。只读屏幕，坐标和裁图均来自同一张原始截图。"""

from PySide6.QtCore import QEventLoop, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPen
from PySide6.QtWidgets import QApplication, QDialog, QHBoxLayout, QPushButton, QWidget

from ..core import vision, window as win_mod


class RoiOverlay(QDialog):
    def __init__(self, monitor, size, pixels, title="拖动框选，Esc 取消", *,
                 min_size=6, action_choices=None):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.monitor, self.pixel_size = monitor, size
        self.image = QImage(pixels, size[0], size[1], size[0] * 3,
                            QImage.Format.Format_RGB888).copy()
        self.prompt, self.min_size = title, min_size
        self.origin = self.endpoint = None
        self.roi = self.action = None
        self.choices = action_choices
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setModal(True)
        self.toolbar = QWidget(self)
        tools = QHBoxLayout(self.toolbar)
        for action, label in list(action_choices or []) + [("retry", "重新框选"), ("cancel", "取消")]:
            button = QPushButton(label, self.toolbar)
            button.clicked.connect(lambda checked=False, a=action: self.choose(a))
            tools.addWidget(button)
        self.toolbar.hide()
        # Windows 的 Qt 屏幕原点与原生显示器原点一致，尺寸则可能按 DPI 缩放。
        # 用原点选屏，在该屏逻辑几何内显示完整原始像素，勿拿物理宽度当 Qt 宽度。
        screens = QGuiApplication.screens()
        screen = min(screens, key=lambda s: abs(s.geometry().x() - monitor["left"])
                     + abs(s.geometry().y() - monitor["top"]))
        self.setGeometry(screen.geometry())

    def physical_point(self, point):
        return (max(0, min(self.pixel_size[0], int(point.x() * self.pixel_size[0] / self.width()))),
                max(0, min(self.pixel_size[1], int(point.y() * self.pixel_size[1] / self.height()))))

    def selection(self):
        if self.origin is None or self.endpoint is None:
            return None
        x0, y0 = self.physical_point(self.origin)
        x1, y1 = self.physical_point(self.endpoint)
        return [min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0)]

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.drawImage(self.rect(), self.image)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 100))
        if self.origin is not None and self.endpoint is not None:
            box = QRectF(self.origin, self.endpoint).normalized().intersected(QRectF(self.rect()))
            painter.save()
            painter.setClipRect(box)
            painter.drawImage(self.rect(), self.image)
            painter.restore()
            painter.setPen(QPen(QColor("#45d6a2"), 2))
            painter.drawRect(box)
        painter.setPen(QColor("white"))
        painter.drawText(16, 30, self.prompt + "（Esc / 右键取消）")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self.reject()
        elif event.button() == Qt.MouseButton.LeftButton and self.roi is None:
            self.origin = self.endpoint = event.position()
            self.update()

    def mouseMoveEvent(self, event):
        if self.origin is not None and self.roi is None:
            self.endpoint = event.position()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self.origin is None or self.roi is not None:
            return
        self.endpoint = event.position()
        roi = self.selection()
        if roi[2] < self.min_size or roi[3] < self.min_size:
            self.choose("retry")
            return
        self.roi = roi
        if self.choices is None:
            self.accept()
        else:
            self.toolbar.adjustSize()
            self.toolbar.move(max(0, min(int(event.position().x()), self.width() - self.toolbar.width())),
                              max(0, min(int(event.position().y()), self.height() - self.toolbar.height())))
            self.toolbar.show()

    def choose(self, action):
        if action == "cancel":
            self.reject()
        elif action == "retry":
            self.roi = self.origin = self.endpoint = self.action = None
            self.toolbar.hide()
            self.update()
        elif self.roi is not None:
            self.action = action
            self.accept()

    def reject(self):
        self.roi = self.action = None
        super().reject()

    def result_data(self, with_crop=False):
        roi = self.roi
        absolute = None if roi is None else [self.monitor["left"] + roi[0],
                                            self.monitor["top"] + roi[1], roi[2], roi[3]]
        values = [absolute]
        if with_crop:
            import numpy as np

            # QImage 仅负责显示；裁图使用原始 RGB 字节，绝不包含遮罩、提示或按钮。
            crop = None
            if roi is not None:
                x, y, w, h = roi
                rgb = np.frombuffer(self.image.constBits(), dtype=np.uint8).reshape(
                    self.pixel_size[1], self.image.bytesPerLine())[:, :self.pixel_size[0] * 3]
                crop = rgb.reshape(self.pixel_size[1], self.pixel_size[0], 3)[y:y+h, x:x+w, ::-1].copy()
            values.append(crop)
        if self.choices is not None:
            values.append(self.action)
        return values[0] if len(values) == 1 else tuple(values)


def select_roi_on_screen(parent, title="拖动框选", around_point=None, min_size=6,
                         with_crop=False, action_choices=None):
    monitor, size, pixels = vision.capture_monitor_rgb(around_point)
    overlay = RoiOverlay(monitor, size, pixels, title, min_size=min_size, action_choices=action_choices)
    try:
        overlay.exec()
        return overlay.result_data(with_crop)
    finally:
        overlay.close()
        overlay.deleteLater()


def capture_roi(parent, cfg, prompt, *, allow_desktop_windows=False):
    """隐藏助手、按落点反查目标窗口，返回 (相对 ROI, BGR 裁图, 窗口尺寸)。取消返回 None。"""
    win_mod.set_game_process(cfg.get("window_process") or win_mod.DEFAULT_GAME_PROCESS_SPEC)
    title, offset = cfg.get("window_title", "梦幻西游"), cfg.get("window_offset", [0, 0])
    locate = win_mod.locate_desktop_windows if allow_desktop_windows else win_mod.locate_all
    at_point = win_mod.desktop_window_at_point if allow_desktop_windows else win_mod.window_at_point
    wins = locate(title, offset)
    if not wins:
        raise ValueError("没找到可标定窗口，请先打开启动器或游戏。")
    rect = wins[0].rect()
    center = (rect[0] + rect[2] // 2, rect[1] + rect[3] // 2) if rect else None
    visible = [(w, w.windowOpacity()) for w in QApplication.topLevelWidgets() if w.isVisible()]
    try:
        for widget, opacity in visible:
            # hide() 会提前结束正在 exec() 的 QDialog；透明化保留原模态循环。
            widget.setWindowOpacity(0)
        # 用 Qt 事件循环等待桌面完成重绘，避免截图包含助手。
        loop = QEventLoop()
        QTimer.singleShot(150, loop.quit)
        loop.exec()
        absolute, crop = select_roi_on_screen(parent, prompt, around_point=center, with_crop=True)
        if absolute is None:
            return None
        x, y, w, h = absolute
        ref = at_point(title, offset, x + w // 2, y + h // 2)
        wr = ref.rect() if ref else None
        if wr is None:
            raise ValueError("框选不在目标窗口内，请把目标窗口切到前面。")
        return [x - wr[0], y - wr[1], w, h], crop, [wr[2], wr[3]]
    finally:
        for widget, opacity in visible:
            widget.setWindowOpacity(opacity)
        if parent is not None:
            parent.window().raise_()
