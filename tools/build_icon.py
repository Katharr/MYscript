"""从可编辑 SVG 重建窗口 PNG 和 Windows 多尺寸 ICO：python -m tools.build_icon。"""

from pathlib import Path

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer


def main():
    assets = Path(__file__).resolve().parents[1] / "mhxy" / "assets"
    renderer = QSvgRenderer(str(assets / "app.svg"))
    if not renderer.isValid():
        raise RuntimeError("图标 SVG 无法解析")
    # 超采样后缩小，保持圆角和星芒边缘平滑。
    canvas = QImage(2048, 2048, QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    try:
        renderer.render(painter)
    finally:
        painter.end()
    canvas = canvas.scaled(512, 512, Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
    if not canvas.save(str(assets / "app.png")):
        raise RuntimeError("无法保存图标 PNG")
    with Image.open(assets / "app.png") as image:
        image.save(assets / "app.ico", sizes=[(s, s) for s in (16, 20, 24, 32, 40, 48, 64, 128, 256)])
    print("已生成 app.png 和 app.ico")


if __name__ == "__main__":
    main()
