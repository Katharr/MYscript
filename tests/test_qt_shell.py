"""仅测试无游戏依赖的 Qt 壳；QSettings 始终指向临时文件。"""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest

from mhxy.qtui.main import get_application
from mhxy.qtui.window import MainWindow


class QtShellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = get_application(["qt-shell-tests", "-platform", "offscreen"])

    def setUp(self):
        self.scan = patch("mhxy.qtui.window.scan_windows", return_value=[])
        self.scan.start()
        self.addCleanup(self.scan.stop)
        self.temp_dir = tempfile.TemporaryDirectory(prefix="mhxy_qt_tests_")
        self.addCleanup(self.temp_dir.cleanup)
        self.settings = QSettings(str(Path(self.temp_dir.name) / "ui.ini"), QSettings.Format.IniFormat)
        self.window = MainWindow(settings=self.settings)
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.window.close)

    def test_application_is_reused(self):
        self.assertIs(get_application([]), self.app)
        self.assertIs(get_application(["another"]), self.app)

    def test_navigation_creates_pages_lazily_and_reuses_them(self):
        self.assertEqual(list(self.window.pages), ["role_window"])
        self.assertEqual(self.window.stack.count(), 1)
        for key, _ in self.window.NAV:
            QTest.mouseClick(self.window.nav_buttons[key], Qt.MouseButton.LeftButton)
            self.assertEqual(self.window.current_page, key)
            self.assertIs(self.window.stack.currentWidget(), self.window.pages[key])
            self.assertEqual([k for k, b in self.window.nav_buttons.items() if b.isChecked()], [key])
        original_pages = dict(self.window.pages)
        for key, _ in reversed(self.window.NAV):
            self.window.show_page(key)
            self.assertIs(self.window.pages[key], original_pages[key])
        self.assertEqual(self.window.stack.count(), len(self.window.NAV))

    def test_unknown_page_does_not_change_selection(self):
        with self.assertRaises(KeyError):
            self.window.show_page("unknown")
        self.assertEqual(self.window.current_page, "role_window")
        self.assertEqual(self.window.stack.count(), 1)

    def test_only_ui_geometry_and_log_preferences_are_saved_and_restored(self):
        # offscreen 平台的屏幕宽度为 800；Qt 会把超出屏幕的几何自动调整。
        self.window.resize(800, 680)
        self.window.move(60, 70)
        self.window.close()
        self.settings.sync()
        self.assertEqual(self.settings.allKeys(), ["window/geometry", "window/logExpanded", "window/logWidth"])
        self.assertEqual(self.settings.value("window/geometry"), self.window.saveGeometry())
        restored = MainWindow(settings=self.settings)
        try:
            self.assertEqual(restored.size(), self.window.size())
        finally:
            restored.close()
            restored.deleteLater()

    def test_module_entry_runs_full_window_without_tk_for_any_window_count(self):
        # 独立进程排除其它旧 GUI 测试已导入 Tk 的影响，实际运行 -m 同一入口。
        script = """
import runpy
import sys
from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QApplication, QMainWindow
from mhxy.qtui import window as window_module
from mhxy.qtui.role_strip import WindowState
from mhxy.core import config as cfg_mod
from pathlib import Path
cfg_mod.CONFIG_PATH = Path(sys.argv[1]).parent / 'config.json'
settings = QSettings(sys.argv[1], QSettings.Format.IniFormat)
window_module.QSettings = lambda *args: settings
count = int(sys.argv[2])
window_module.scan_windows = lambda cfg: [WindowState(i + 1, f'号{i + 1}') for i in range(count)]
app = QApplication([])
def navigate_and_close():
    windows = [w for w in app.topLevelWidgets() if isinstance(w, QMainWindow)]
    assert len(windows) == 1
    window = windows[0]
    assert window.isVisible()
    assert window.current_page == 'role_window'
    assert len(window.role_strip.items) == count
    assert window.role_page.launch_panel.isVisible() == (count == 0)
    assert window.width() >= 800 and window.height() >= 640
    window.nav_buttons['sniper'].click()
    assert window.current_page == 'sniper'
    window.pages['sniper']._open_calibration()
    window.pages['sniper']._calibration_dialog.reject()
    window.nav_buttons['calibration'].click()
    assert window.current_page == 'calibration'
    assert QApplication.instance() is app
    assert 'tkinter' not in sys.modules
    assert 'customtkinter' not in sys.modules
    assert 'mhxy.gui.app' not in sys.modules
    window.close()
QTimer.singleShot(100, navigate_and_close)
runpy.run_module('mhxy.qtui.main', run_name='__main__')
"""
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
        for count in (0, 2):
            with self.subTest(count=count), tempfile.TemporaryDirectory(prefix="mhxy_qt_entry_") as directory:
                env["APPDATA"] = directory
                env["XDG_CONFIG_HOME"] = directory
                result = subprocess.run(
                    [sys.executable, "-c", script, str(Path(directory) / "ui.ini"), str(count)],
                    cwd=Path(__file__).resolve().parents[1],
                    env=env, capture_output=True, text=True, timeout=20,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
