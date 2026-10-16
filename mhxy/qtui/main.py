"""默认 Qt 入口，也可用 python -m mhxy.qtui.main 独立启动。"""

import sys

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from .theme import apply_theme
from .window import MainWindow


def get_application(argv: list[str] | None = None) -> QApplication:
    """复用进程中的 QApplication，导入模块本身不创建应用或窗口。"""
    app = QCoreApplication.instance()
    if app is None:
        app = QApplication(sys.argv if argv is None else argv)
        app.setApplicationName("梦幻小助手")
    if not isinstance(app, QApplication):
        raise RuntimeError("Qt 界面需要 QApplication，当前进程已有非 GUI Qt 应用")
    apply_theme(app)
    return app


def main(argv: list[str] | None = None) -> int:
    app = get_application(argv)
    # 显式诊断开关用于在干净目录验证真实发布包；正常启动不加载诊断代码。
    args = sys.argv[1:] if argv is None else argv[1:]
    if "--smoke-report" in args:
        from .smoke import run_smoke

        return run_smoke(app, args[args.index("--smoke-report") + 1])
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
