# -*- coding: utf-8 -*-
"""已开游戏的启动识别回归：模拟唤窗，不启动 GUI，不读真实游戏资料。"""
import copy
from pathlib import Path
import queue
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from mhxy.gui.app import _wake_existing_windows
from mhxy.core import accounts, window as win_mod
from mhxy.gui.app import App, GeneralPage


class QtStartupIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from mhxy.qtui.main import get_application
        cls.qt = get_application(["qt-startup-tests", "-platform", "offscreen"])

    def setUp(self):
        from PySide6.QtCore import QSettings
        from mhxy.core import config as cfg_mod
        from mhxy.qtui.window import MainWindow
        self.cfg = copy.deepcopy(cfg_mod.DEFAULT_CONFIG)
        self.cfg["targets"].update(multi=True, multi_indices=[0, 2], max_windows=0)
        self.cfg["tasks"]["teaming"]["captain_index"] = 1
        self.scan = patch("mhxy.qtui.window.scan_windows", return_value=[]).start()
        self.load = patch.object(cfg_mod, "load_config", side_effect=lambda: copy.deepcopy(self.cfg)).start()
        self.save = patch.object(cfg_mod, "save_config", side_effect=self.capture_config).start()
        self.addCleanup(patch.stopall)
        self.temp = tempfile.TemporaryDirectory(prefix="mhxy_qt_identity_")
        self.addCleanup(self.temp.cleanup)
        settings = QSettings(str(Path(self.temp.name) / "ui.ini"), QSettings.Format.IniFormat)
        self.window = MainWindow(settings=settings)
        self.window.scan_timer.stop()
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.window.close)

    def capture_config(self, cfg):
        self.cfg = copy.deepcopy(cfg)

    def states(self, labels=("号1", "号2", "号3")):
        from mhxy.qtui.role_strip import WindowState
        return [WindowState(101 + i, name, "pending" if name.startswith("号") else "ready")
                for i, name in enumerate(labels)]

    def wait_for(self, predicate):
        from PySide6.QtTest import QTest
        deadline = time.monotonic() + 3
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertTrue(predicate(), "Qt 后台结果未及时回到主线程")

    def test_empty_startup_keeps_full_window_and_launch_controls(self):
        self.window.show()
        self.window._apply_windows([], "")
        self.assertEqual(self.window.current_page, "role_window")
        self.assertTrue(self.window.isVisible())
        self.assertTrue(self.window.role_page.launch_panel.isVisible())
        self.assertEqual(self.window.role_strip.items, {})
        self.assertEqual(self.window.role_page.run_button.text(), "开始启动登录")
        self.assertFalse(self.window.role_page.live.isChecked())
        self.save.assert_not_called()

    def test_late_names_and_window_changes_reuse_unchanged_role_items(self):
        self.window._apply_windows(self.states(), "")
        items = dict(self.window.role_strip.items)
        boxes = dict(self.window.role_page.boxes)
        first_value = items[101]._value
        self.window._apply_windows(self.states(("号1", "示例乙（60）", "号3")), "")
        self.assertEqual(self.window.role_strip.items, items)
        self.assertIs(items[101]._value, first_value)
        self.assertEqual(items[102].name.text(), "示例乙（60）")
        self.assertEqual(items[102]._value[0].status, "ready")
        self.assertEqual(self.window.role_page.boxes, boxes)
        self.window._apply_windows([self.states()[2], self.states()[0]], "")
        self.assertEqual(list(self.window.role_strip._order), [103, 101])
        self.assertEqual(set(self.window.role_strip.items), {101, 103})
        self.assertIs(self.window.role_strip.items[101], items[101])

    def test_lights_and_selection_are_independent_and_captain_comes_from_config(self):
        self.window._apply_windows(self.states(), "")
        items = self.window.role_strip.items
        self.assertTrue(items[101].property("selected"))
        self.assertFalse(items[102].property("selected"))
        self.assertEqual(items[101].light.styleSheet(), items[102].light.styleSheet())
        self.assertFalse(items[101]._value[2])
        self.assertTrue(items[103]._value[2])
        self.window.role_page.captain_combo.setCurrentIndex(0)
        self.window._update_strip()
        self.assertTrue(items[103]._value[2])  # 未保存的 UI 标记不是配置真相。
        self.window._apply_windows([], "probe unavailable")
        self.assertEqual(len(items), 3)
        self.assertTrue(all(item._value[0].status == "failed" for item in items.values()))
        self.window._apply_windows(self.states(), "")
        self.assertTrue(all(item._value[0].status == "pending" for item in items.values()))
        self.window._apply_windows([], "")
        self.assertEqual(items, {})

    def test_save_subset_single_and_all_preserve_old_targets_and_relative_captain(self):
        self.window._apply_windows(self.states(), "")
        page = self.window.role_page
        page.save_selection()
        self.assertEqual(self.cfg["targets"]["multi_indices"], [0, 2])
        self.assertEqual(self.cfg["tasks"]["teaming"]["captain_index"], 1)
        page.boxes[101].setChecked(False)
        page.save_selection()
        self.assertFalse(self.cfg["targets"]["multi"])
        self.assertEqual(self.cfg["targets"]["single_index"], 2)
        self.assertEqual(self.cfg["targets"]["multi_indices"], [2])
        self.assertEqual(self.cfg["tasks"]["teaming"]["captain_index"], 0)
        for box in page.boxes.values():
            box.setChecked(True)
        page.captain_combo.setCurrentIndex(2)
        page.save_selection()
        self.assertTrue(self.cfg["targets"]["multi"])
        self.assertEqual(self.cfg["targets"]["multi_indices"], [])
        self.assertEqual(self.cfg["tasks"]["teaming"]["captain_index"], 2)
        self.assertEqual(self.cfg["targets"]["switch_delay_sec"], 0.15)
        self.assertTrue(self.cfg["tasks"]["launch_login"]["dry_run"])
        self.assertEqual(self.cfg["tasks"]["taohaiqu"]["captain_index"], 0)
        self.save.reset_mock()
        for box in page.boxes.values():
            box.setChecked(False)
        page.save_selection()
        self.save.assert_not_called()

    def test_profile_and_path_edits_preserve_existing_fields_and_safe_mode(self):
        page = self.window.role_page
        self.cfg["account_launch"].update(launcher_args=["example"], profiles=[
            {"id": "profile", "label": "示例角色", "enabled": True,
             "expected_role_id": "example", "expected_role_name": "示例角色"}])
        page.path.setText("launcher.exe")
        page.save_path()
        page.set_profile_enabled("profile", False)
        self.assertEqual(self.cfg["account_launch"]["launcher_args"], ["example"])
        self.assertEqual(self.cfg["account_launch"]["profiles"][0]["expected_role_id"], "example")
        self.assertFalse(self.cfg["account_launch"]["profiles"][0]["enabled"])
        self.assertEqual(self.cfg["account_launch"]["launcher_path"], "launcher.exe")
        self.assertTrue(self.cfg["tasks"]["launch_login"]["dry_run"])

    def test_slow_scan_does_not_block_navigation_and_returns_on_ui_thread(self):
        started, release = threading.Event(), threading.Event()
        worker_ids, ui_ids = [], []
        main_id = threading.get_ident()
        def scan(cfg):
            worker_ids.append(threading.get_ident())
            started.set()
            release.wait(2)
            return self.states()
        self.scan.side_effect = scan
        original = self.window.role_page.update_windows
        def apply(*args):
            ui_ids.append(threading.get_ident())
            original(*args)
        self.window.role_page.update_windows = apply
        try:
            self.window.refresh_windows()
            self.wait_for(started.is_set)
            self.window.refresh_windows()
            self.window.show_page("sniper")
            self.assertEqual(self.window.current_page, "sniper")
            self.assertEqual(self.scan.call_count, 1)
        finally:
            release.set()
        self.wait_for(lambda: len(self.window.role_strip.items) == 3)
        self.assertNotEqual(worker_ids, [main_id])
        self.assertEqual(ui_ids, [main_id])

    def test_scan_failure_then_recovery(self):
        self.scan.side_effect = RuntimeError("probe unavailable")
        self.window.refresh_windows()
        self.wait_for(lambda: not self.window._scanning)
        self.assertIn("检测失败", self.window.role_page.window_status.text())
        self.scan.side_effect = None
        self.scan.return_value = self.states()
        self.window.refresh_windows()
        self.wait_for(lambda: len(self.window.role_strip.items) == 3)

    def test_launch_preflight_runs_in_background_and_reports_failure_on_ui(self):
        runtime = Mock()
        runtime.drain_logs.return_value = []
        runtime.is_running.return_value = False
        runner = SimpleNamespace(log_queue=queue.Queue(), stop_event=threading.Event(), outcome="idle")
        runtime.create_runner.return_value = runner
        release = threading.Event()
        ids = []
        def start(_runner):
            ids.append(threading.get_ident())
            release.wait(2)
            return False, ["缺少流程标定"]
        runtime.start.side_effect = start
        with patch("mhxy.core.runtime.get_runtime", return_value=runtime):
            try:
                self.window.toggle_launch()
                self.assertTrue(self.window._launching)
                self.window.show_page("daily")
                self.assertEqual(self.window.current_page, "daily")
                self.assertEqual(runtime.create_runner.call_args.args[0].name, "launch_login")
                self.assertTrue(runtime.create_runner.call_args.args[1]["tasks"]["launch_login"]["dry_run"])
            finally:
                release.set()
            self.wait_for(lambda: not self.window._launching)
        self.assertNotEqual(ids, [threading.get_ident()])
        self.assertIn("缺少流程标定", self.window.role_page.status.text())
        self.assertIsNone(self.window.runner)

    def test_stop_before_launch_thread_starts_cancels_preflight(self):
        runtime = Mock()
        runtime.drain_logs.return_value = []
        runtime.is_running.return_value = False
        runner = SimpleNamespace(log_queue=queue.Queue(), stop_event=threading.Event(), outcome="idle")
        runtime.create_runner.return_value = runner
        with patch("mhxy.core.runtime.get_runtime", return_value=runtime), \
             patch("mhxy.qtui.window.threading.Thread") as thread:
            self.window.toggle_launch()
            self.window.stop_all_tasks()
            work = thread.call_args.kwargs["target"]
            work()
            self.wait_for(lambda: not self.window._launching)
        runtime.start.assert_not_called()
        self.assertIn("启动已取消", self.window.role_page.status.text())

    def test_button_hotkey_corner_and_close_keep_stop_path(self):
        for trigger in ("button", "hotkey", "corner", "close"):
            with self.subTest(trigger=trigger):
                runtime = Mock()
                runtime.drain_logs.return_value = []
                runtime.is_running.return_value = True
                runner = SimpleNamespace(log_queue=queue.Queue(), outcome="running")
                self.window._runtime = runtime
                self.window.runner = runner
                self.window._launch_cancel = threading.Event()
                self.window._hotkey_down = False
                with patch("mhxy.core.emergency.get_cursor", return_value=(400, 400)), \
                     patch("mhxy.core.emergency.in_failsafe_corner", return_value=trigger == "corner"), \
                     patch("mhxy.core.emergency.vk_down", return_value=trigger == "hotkey"):
                    if trigger == "button":
                        self.window.toggle_launch()
                    elif trigger == "close":
                        self.window.close()
                    else:
                        self.window._pump_launch()
                runtime.stop.assert_any_call(runner)
                runtime.stop_all.assert_called_once()
                self.assertTrue(self.window._launch_cancel.is_set())

    def test_scan_result_after_close_does_not_touch_widgets(self):
        release, done = threading.Event(), threading.Event()
        def scan(cfg):
            release.wait(2)
            done.set()
            return self.states()
        self.scan.side_effect = scan
        self.window.refresh_windows()
        self.window.close()
        try:
            release.set()
            self.wait_for(done.is_set)
            self.qt.processEvents()
        finally:
            release.set()
        self.assertEqual(self.window.role_strip.items, {})


class StartupIdentityTests(unittest.TestCase):
    def setUp(self):
        self.pending = []
        self.app = SimpleNamespace(
            cfg={'window_title': 'game', 'window_offset': [1, 2]},
            ui_post=self.pending.append, log_line=Mock(), _kick_locate=Mock(),
            _game_connected=(True, '号1 · 单开'))

    def drain(self):
        for callback in self.pending:
            callback()

    def test_each_woken_window_identified_before_ui_refresh(self):
        wins = [object(), object()]
        events = []

        def wake(title, offset, on_woken):
            self.assertEqual((title, offset), ('game', [1, 2]))
            for win in wins:
                events.append(('wake', win))
                on_woken(win)
            return 2, 2

        with patch.object(win_mod, 'wake_all_to_front', side_effect=wake), \
             patch.object(accounts, 'labels_for', side_effect=lambda wins: events.append(('identify', wins[0]))):
            _wake_existing_windows(self.app)
        self.assertEqual(events, [('wake', wins[0]), ('identify', wins[0]),
                                  ('wake', wins[1]), ('identify', wins[1])])
        self.app.log_line.assert_not_called()
        self.app._kick_locate.assert_not_called()
        self.assertEqual(len(self.pending), 1)
        self.drain()
        self.assertIsNone(self.app._game_connected)
        self.app._kick_locate.assert_called_once_with()

    def test_visible_windows_use_same_scan_without_minimized_gate(self):
        win = Mock()
        win._win.isMinimized = False
        def wake(title, offset, on_woken):
            on_woken(win)
            return 1, 1
        with patch.object(win_mod, 'wake_all_to_front', side_effect=wake), \
             patch.object(accounts, 'labels_for', return_value=['示例角色（50）']) as labels:
            _wake_existing_windows(self.app)
        labels.assert_called_once_with([win])
        self.drain()
        self.app._kick_locate.assert_called_once_with()

    def test_identity_exception_isolates_one_window_and_refreshes_ui(self):
        wins = [object(), object()]
        def wake(title, offset, on_woken):
            for win in wins:
                on_woken(win)
            return 2, 2
        with patch.object(win_mod, 'wake_all_to_front', side_effect=wake), \
             patch.object(accounts, 'labels_for', side_effect=[RuntimeError('OCR unavailable'), ['示例角色']]) as labels:
            _wake_existing_windows(self.app)
        self.assertEqual(labels.call_count, 2)
        self.drain()
        self.app._kick_locate.assert_called_once_with()
        self.assertTrue(any(call.args[1] == 'warn' for call in self.app.log_line.call_args_list))

    def test_no_windows_does_not_trigger_ocr_or_ui_refresh(self):
        with patch.object(win_mod, 'wake_all_to_front', return_value=(0, 0)), \
             patch.object(accounts, 'labels_for') as labels:
            _wake_existing_windows(self.app)
        self.drain()
        labels.assert_not_called()
        self.app._kick_locate.assert_not_called()
        self.app.log_line.assert_not_called()

    def test_wake_exception_reported_on_ui_queue(self):
        with patch.object(win_mod, 'wake_all_to_front', side_effect=RuntimeError('test error')):
            _wake_existing_windows(self.app)
        self.app.log_line.assert_not_called()
        self.drain()
        self.app.log_line.assert_called_once()
        self.assertEqual(self.app.log_line.call_args.args[1], 'warn')

    def test_full_role_order_drives_target_summary_and_general_page(self):
        wins = [SimpleNamespace(_win=SimpleNamespace(_hWnd=101)),
                SimpleNamespace(_win=SimpleNamespace(_hWnd=102))]
        names = {101: {'label': '示例甲（50）'}, 102: {'label': '示例乙（60）'}}
        targets = {'multi': True, 'multi_indices': [0, 1]}
        with patch.object(accounts, '_identity', copy.deepcopy(names)), \
             patch.object(accounts, '_cache', {'order': ['示例甲（50）']}), \
             patch.object(accounts, '_compute') as compute:
            connected, summary = App._compute_target_state(wins, targets)
            self.assertTrue(connected)
            self.assertEqual(summary, '示例甲（50）、示例乙（60） · 多开')
            self.assertEqual(GeneralPage._targets_summary({'targets': targets}),
                             '多开 · 示例甲（50）、示例乙（60）')
            self.assertEqual(accounts.cached_labels_for([wins[1]]), ['示例乙（60）'])
            self.assertEqual(accounts.cached_labels(), ['示例甲（50）', '示例乙（60）'])
            compute.assert_not_called()


if __name__ == '__main__':
    unittest.main()
