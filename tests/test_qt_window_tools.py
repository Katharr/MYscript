"""窗口工具迁移回归；唤窗、OCR 和关闭进程始终使用替身。"""

import copy
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QElapsedTimer, QSettings
from PySide6.QtTest import QTest

from mhxy.core import accounts, config as C, window as win_mod
from mhxy.qtui.main import get_application
from mhxy.qtui.role_strip import WindowState
from mhxy.qtui.window import MainWindow, run_window_action


class WindowActionTests(unittest.TestCase):
    def test_wake_identifies_each_foreground_window_and_preserves_configured_scope(self):
        windows = [object(), object()]

        def wake(title, offset, on_woken):
            self.assertEqual((title, offset), ("指定游戏", [2, 3]))
            for win in windows:
                on_woken(win)
            return 2, 2

        cfg = dict(window_process="custom.exe", window_title="指定游戏", window_offset=[2, 3],
                   game_dir="game", account_launch={"launcher_path": "launcher"})
        with patch.object(win_mod, "set_game_process") as scope, \
             patch.object(win_mod, "wake_all_to_front", side_effect=wake), \
             patch.object(accounts, "set_game_dir") as directory, \
             patch.object(accounts, "set_launcher_path") as launcher, \
             patch.object(accounts, "labels_for") as identify, \
             patch.object(accounts, "ocr_status", return_value="ready"), \
             patch("mhxy.qtui.window.scan_windows", return_value=[
                 WindowState(1, "甲（50）", "ready"), WindowState(2, "乙（60）", "ready")]):
            self.assertEqual(run_window_action(cfg, "wake"), (2, 2, 2, "ready"))
        scope.assert_called_once_with("custom.exe")
        directory.assert_called_once_with("game")
        launcher.assert_called_once_with("launcher")
        self.assertEqual([call.args[0][0] for call in identify.call_args_list],
                         [windows[0], windows[0], windows[1], windows[1]])

    def test_close_uses_existing_process_close_function_without_ocr_or_waking(self):
        with patch.object(win_mod, "set_game_process"), \
             patch.object(win_mod, "close_all", return_value=(3, 2)) as close, \
             patch.object(win_mod, "wake_all_to_front") as wake, \
             patch.object(accounts, "labels_for") as identify:
            self.assertEqual(run_window_action({"window_title": "game"}, "close"), (3, 2))
        close.assert_called_once_with("game", [0, 0])
        wake.assert_not_called()
        identify.assert_not_called()


class QtWindowToolsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = get_application(["window-tools-tests", "-platform", "offscreen"])

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="mhxy_window_tools_")
        self.addCleanup(directory.cleanup)
        for target, kwargs in (
            ("mhxy.core.config.load_config", dict(side_effect=lambda: copy.deepcopy(C.DEFAULT_CONFIG))),
            ("mhxy.core.config.save_config", {}),
            ("mhxy.qtui.window.scan_windows", dict(return_value=[])),
            ("mhxy.qtui.window.run_window_action", dict(return_value=(2, 2, 2, "ready"))),
            ("mhxy.core.runtime.RuntimeService.any_running", dict(return_value=False)),
        ):
            mock = patch(target, **kwargs)
            value = mock.start()
            self.addCleanup(mock.stop)
            if target.endswith("run_window_action"):
                self.action = value
        self.window = MainWindow(QSettings(str(Path(directory.name) / "ui.ini"), QSettings.Format.IniFormat))
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.window.close)
        self.window.scan_timer.stop()
        self.window.runtime_timer.stop()
        self.window.resize(800, 640)
        self.window.show()
        self.app.processEvents()

    def wait_for(self, predicate):
        timer = QElapsedTimer()
        timer.start()
        while not predicate() and timer.elapsed() < 3000:
            QTest.qWait(10)
        self.assertTrue(predicate())

    def test_top_window_management_is_the_only_window_tool_entry(self):
        tools = self.window.window_tools
        self.assertTrue(tools.isVisible())
        self.assertTrue(tools.wake_button.isVisible())
        self.assertTrue(tools.close_button.isVisible())
        for key in ("role_window", "general"):
            self.window.show_page(key)
            self.app.processEvents()
            self.assertFalse(hasattr(self.window.pages[key], "window_tools"))
            self.assertTrue(self.window.window_tools.wake_button.isVisible())
            self.assertTrue(self.window.window_tools.close_button.isVisible())
            with patch.object(self.window, "_start_window_action") as start:
                tools.wake_button.click()
                start.assert_called_once_with("wake")
            self.assertGreaterEqual(tools.wake_button.height(), tools.fontMetrics().height() + 6)

    def test_cancel_does_not_close_or_stop_and_confirm_starts_close_action(self):
        for accepted in (False, True):
            with self.subTest(accepted=accepted):
                def respond(dialog):
                    text = "确认关闭" if accepted else "取消"
                    next(button for button in dialog.buttons() if button.text() == text).click()
                    return 0

                with patch("mhxy.qtui.window.QMessageBox.exec", respond), \
                     patch.object(self.window, "_start_window_action") as start, \
                     patch.object(self.window, "stop_all_tasks") as stop:
                    self.window.window_tools.close_button.click()
                    if accepted:
                        start.assert_called_once_with("close")
                    else:
                        start.assert_not_called()
                    stop.assert_not_called()
        self.action.assert_not_called()

    def test_wake_runs_off_ui_thread_and_blocks_duplicate_and_new_task_starts(self):
        release, entered = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        worker_threads = []

        def work(cfg, action):
            worker_threads.append(threading.get_ident())
            entered.set()
            release.wait(3)
            return 2, 2, 1, "unavailable: OCR missing"

        self.action.side_effect = work
        self.window.wake_game_windows()
        self.wait_for(entered.is_set)
        self.window.wake_game_windows()
        self.window.show_page("general")
        tools = self.window.window_tools
        self.assertFalse(tools.wake_button.isEnabled())
        self.assertFalse(tools.close_button.isEnabled())
        with patch.object(self.window.pages["general"], "_make_runner") as create:
            self.window.pages["general"].toggle_run()
            create.assert_not_called()
        self.assertTrue(self.window._calibration_running())
        self.assertNotEqual(worker_threads[0], threading.get_ident())
        release.set()
        self.wait_for(lambda: not self.window._window_action)
        self.assertEqual(self.action.call_count, 1)
        self.assertTrue(tools.wake_button.isEnabled())
        self.assertIn("OCR missing", tools.status.text())
        self.assertIn("角色名已确认 1 个", self.window.window_tools.status.text())

    def test_close_stops_tasks_before_worker_and_reports_partial_failure(self):
        stopped = threading.Event()

        def work(cfg, action):
            self.assertTrue(stopped.is_set())
            self.assertEqual(action, "close")
            return 3, 2

        self.action.side_effect = work
        with patch.object(self.window, "stop_all_tasks", side_effect=stopped.set):
            self.window._start_window_action("close")
            self.wait_for(lambda: not self.window._window_action)
        text = self.window.window_tools.status.text()
        self.assertIn("2/3", text)
        self.assertIn("运行权限", text)

    def test_wake_refuses_to_change_focus_while_task_is_running(self):
        with patch.object(self.window, "_calibration_running", return_value=True):
            self.window.wake_game_windows()
        self.action.assert_not_called()
        self.assertIn("先停止任务", self.window.window_tools.status.text())

    def test_worker_error_restores_buttons_and_displays_error(self):
        self.action.side_effect = RuntimeError("测试异常")
        self.window.wake_game_windows()
        self.wait_for(lambda: not self.window._window_action)
        tools = self.window.window_tools
        self.assertTrue(tools.wake_button.isEnabled())
        self.assertIn("测试异常", tools.status.text())


if __name__ == "__main__":
    unittest.main()
