"""Qt 关于页。"""

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from . import theme as T


class AboutPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        T.set_role(self, "page")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*([T.SPACE_LG] * 4))
        heading = QLabel("关于", self)
        heading.setProperty("role", "heading")
        layout.addWidget(heading)
        self.description = QLabel(
            "梦幻小助手\n\n"
            "通过截屏、图像识别和模拟点击执行任务，不读内存、不注入进程。\n"
            "界面使用 PySide6 Qt Widgets，复用现有任务、识别、输入和配置。\n\n"
            "默认使用演练模式；急停快捷键与失控急停角可在设置页查看和调整。\n\n"
            "使用第三方脚本可能违反目标客户端用户协议并导致封号。"
            "请用小号测试，自负风险。本工具仅供学习交流。", self)
        self.description.setWordWrap(True)
        layout.addWidget(self.description)
        layout.addStretch()
