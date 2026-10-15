"""主窗口壳：窄导航、按需创建的占位页和状态条，无游戏或任务依赖。"""

from PySide6.QtCore import QSettings
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import theme as T


class MainWindow(QMainWindow):
    NAV = (
        ("role_window", "角色与窗口"),
        ("general", "通用 / 工具"),
        ("daily", "日常一条龙"),
        ("sniper", "秒装备"),
        ("treasure_map", "宝图"),
        ("escort", "运镖"),
        ("secret_realm", "秘境降妖"),
        ("dungeon", "刷副本"),
        ("freeclick", "自由点击"),
        ("settings", "设置"),
        ("about", "关于"),
    )

    def __init__(self, settings: QSettings | None = None):
        super().__init__()
        # 此存储仅包含窗口几何信息，任务配置仍由 core.config 管理。
        self._settings = settings if settings is not None else QSettings("MHXY", "QtUI")
        self.pages: dict[str, QWidget] = {}
        self.nav_buttons: dict[str, QPushButton] = {}
        self.current_page: str | None = None
        self.setWindowTitle("梦幻小助手 · Qt")
        self.resize(*T.WINDOW_SIZE)
        self.setMinimumSize(*T.MIN_WINDOW_SIZE)

        shell = QWidget(self)
        shell.setObjectName("shell")
        layout = QHBoxLayout(shell)
        layout.setContentsMargins(0, 0, T.SPACE_LG, 0)
        layout.setSpacing(T.SPACE_LG)
        self.setCentralWidget(shell)

        navigation = QFrame(shell)
        navigation.setObjectName("navigation")
        navigation.setFixedWidth(T.NAV_WIDTH)
        nav_layout = QVBoxLayout(navigation)
        nav_layout.setContentsMargins(T.SPACE_SM, T.SPACE_LG, T.SPACE_SM, T.SPACE_MD)
        nav_layout.setSpacing(T.SPACE_SM)
        brand = QLabel("梦幻小助手", navigation)
        brand.setProperty("role", "brand")
        nav_layout.addWidget(brand)
        nav_layout.addSpacing(T.SPACE_MD)
        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        for key, title in self.NAV:
            button = QPushButton(title, navigation)
            button.setProperty("role", "navigation")
            button.setCheckable(True)
            button.clicked.connect(lambda checked=False, page_key=key: self.show_page(page_key))
            self._nav_group.addButton(button)
            self.nav_buttons[key] = button
            nav_layout.addWidget(button)
        nav_layout.addStretch()
        layout.addWidget(navigation)

        self.stack = QStackedWidget(shell)
        stack_layout = QVBoxLayout()
        stack_layout.setContentsMargins(0, T.SPACE_LG, 0, T.SPACE_LG)
        stack_layout.addWidget(self.stack)
        layout.addLayout(stack_layout, 1)

        status = self.statusBar()
        status.setSizeGripEnabled(True)
        status.showMessage("就绪")
        self._page_status = QLabel(self)
        self._page_status.setProperty("role", "muted")
        status.addPermanentWidget(self._page_status)
        geometry = self._settings.value("window/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        self.show_page(self.NAV[0][0])

    def _create_page(self, key: str, title: str) -> QWidget:
        page = QWidget(self.stack)
        page.setObjectName(f"page_{key}")
        page.setProperty("role", "page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(T.SPACE_LG, T.SPACE_LG, T.SPACE_LG, T.SPACE_LG)
        layout.setSpacing(T.SPACE_MD)
        heading = QLabel(title, page)
        heading.setProperty("role", "heading")
        heading.setWordWrap(True)
        layout.addWidget(heading)
        state = QLabel("待迁移", page)
        state.setProperty("role", "muted")
        layout.addWidget(state)
        layout.addStretch()
        return page

    def show_page(self, key: str) -> None:
        titles = dict(self.NAV)
        if key not in titles:
            raise KeyError(f"未知页面: {key}")
        if key not in self.pages:
            page = self._create_page(key, titles[key])
            self.stack.addWidget(page)
            self.pages[key] = page
        self.stack.setCurrentWidget(self.pages[key])
        self.current_page = key
        self.nav_buttons[key].setChecked(True)
        self._page_status.setText(titles[key])

    def closeEvent(self, event: QCloseEvent) -> None:
        self._settings.setValue("window/geometry", self.saveGeometry())
        super().closeEvent(event)
