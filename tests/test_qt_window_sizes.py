"""窗口尺寸迁移回归：固定句柄、后台恢复、尺寸组事务和页面边界。"""

import copy
from dataclasses import replace
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QElapsedTimer, QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QCheckBox, QDialog, QPushButton

from mhxy.core import calib_profiles as calib, config as C, window as win_mod
from mhxy.qtui.main import get_application
from mhxy.qtui.role_strip import WindowState
from mhxy.qtui.window import MainWindow, run_window_action, scan_windows


def fake_window(hwnd, size=(1600, 1200)):
    win = Mock()
    win._win = SimpleNamespace(_hWnd=hwnd, isMinimized=False)
    win.rect.return_value = [0, 0, *size]
    win.activate.return_value = True
    win.resize_to.return_value = True
    return win


class RestoreSizeTests(unittest.TestCase):
    def test_restore_uses_snapshot_order_and_never_touches_unselected_or_new_windows(self):
        windows = [fake_window(hwnd) for hwnd in (103, 104, 101, 102)]
        with patch.object(win_mod, "locate_all", return_value=windows):
            result = win_mod.restore_targets_size("game", [0, 0], {"window_handles": [101, 103]}, [1600, 1200])
        self.assertEqual(result, (2, 2, [[1600, 1200], [1600, 1200]]))
        for index in (1, 3):
            windows[index].activate.assert_not_called()
            windows[index].resize_to.assert_not_called()

    def test_missing_snapshot_member_cancels_without_substituting_another_window(self):
        win = fake_window(101)
        with patch.object(win_mod, "locate_all", return_value=[win, fake_window(104)]):
            self.assertEqual(win_mod.restore_targets_size("game", [], {"window_handles": [101, 103]}, [1600, 1200]),
                             (0, 0, []))
        win.activate.assert_not_called()

    def test_activation_failure_skips_resize_and_exception_does_not_abort_remaining_targets(self):
        windows = [fake_window(hwnd) for hwnd in (101, 102, 103)]
        windows[0].activate.return_value = False
        windows[1].resize_to.side_effect = OSError("closed")
        with patch.object(win_mod, "resolve_targets", return_value=windows):
            self.assertEqual(win_mod.restore_targets_size("game", [], {}, [1600, 1200]),
                             (1, 3, [[1600, 1200], None, [1600, 1200]]))
        windows[0].resize_to.assert_not_called()
        windows[2].resize_to.assert_called_once_with(1600, 1200)

    def test_action_uses_active_profile_and_requires_explicit_snapshot(self):
        cfg = copy.deepcopy(C.DEFAULT_CONFIG)
        calib.get_or_create(cfg, [1600, 1200])
        cfg["targets"].update(window_handles=[103], base_size=[900, 700])
        with patch.object(win_mod, "set_game_process"), \
                patch.object(win_mod, "restore_targets_size", return_value=(1, 1, [[1600, 1200]])) as restore:
            run_window_action(cfg, "restore_size")
            self.assertEqual(restore.call_args.args[2]["window_handles"], [103])
            self.assertEqual(restore.call_args.args[3], [1600, 1200])
            cfg["targets"]["window_handles"] = []
            with self.assertRaises(ValueError):
                run_window_action(cfg, "restore_size")
            self.assertEqual(restore.call_count, 1)

    def test_minimized_size_reads_normal_placement_without_waking_window(self):
        win = win_mod.GameWindow("game").bind(SimpleNamespace(_hWnd=123, isMinimized=True))
        def placement(hwnd, pointer):
            rect = pointer._obj.rcNormalPosition
            rect.left, rect.top, rect.right, rect.bottom = 20, 30, 1620, 1230
            return 1
        with patch.object(win_mod._user32, "GetWindowPlacement", side_effect=placement), \
                patch.object(win, "activate") as activate:
            self.assertEqual(win.normal_size(), (1600, 1200))
        activate.assert_not_called()

    def test_scan_includes_normal_and_minimized_sizes_without_input(self):
        windows = [fake_window(101), fake_window(103)]
        windows[1]._win.isMinimized = True
        windows[1].normal_size.return_value = (1200, 900)
        with patch.object(win_mod, "locate_all", return_value=windows), \
                patch("mhxy.core.accounts.sync_window_labels", return_value=["测试甲", "测试乙"]):
            states = scan_windows(copy.deepcopy(C.DEFAULT_CONFIG))
        self.assertEqual([s.size for s in states], [(1600, 1200), (1200, 900)])
        for win in windows:
            win.activate.assert_not_called()

    def test_unreadable_minimized_size_keeps_identity_and_presence(self):
        win = fake_window(101)
        win._win.isMinimized = True
        win.normal_size.side_effect = OSError("placement unavailable")
        with patch.object(win_mod, "locate_all", return_value=[win]), \
                patch("mhxy.core.accounts.sync_window_labels", return_value=["测试甲"]):
            state = scan_windows(copy.deepcopy(C.DEFAULT_CONFIG))[0]
        self.assertEqual((state.hwnd, state.status, state.minimized, state.size), (101, "ready", True, None))


class QtWindowSizeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = get_application(["window-size-tests", "-platform", "offscreen"])

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="mhxy_window_sizes_")
        self.addCleanup(directory.cleanup)
        self.cfg = copy.deepcopy(C.DEFAULT_CONFIG)
        self.first = calib.get_or_create(self.cfg, [1600, 1200])
        self.second = calib.get_or_create(self.cfg, [1200, 900])
        calib.set_active(self.cfg, self.first)
        self.cfg["targets"]["selected_hwnds"] = [101, 103]
        for target, kwargs in (
            ("mhxy.core.config.load_config", dict(side_effect=lambda: copy.deepcopy(self.cfg))),
            ("mhxy.core.config.save_config", dict(side_effect=self.capture)),
            ("mhxy.qtui.window.MainWindow.refresh_windows", {}),
            ("mhxy.core.runtime.RuntimeService.any_running", dict(return_value=False)),
        ):
            patcher = patch(target, **kwargs)
            value = patcher.start()
            self.addCleanup(patcher.stop)
            if target.endswith("save_config"):
                self.save = value
        self.window = MainWindow(QSettings(str(Path(directory.name) / "ui.ini"), QSettings.Format.IniFormat))
        self.window.scan_timer.stop()
        self.window.runtime_timer.stop()
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.window.close)
        self.states = [WindowState(101, "测试甲", "ready", size=(1600, 1200)),
                       WindowState(102, "测试乙", "ready", size=(1400, 1000)),
                       WindowState(103, "测试丙", "ready", size=(1200, 900))]
        self.window._apply_windows(self.states, "")
        self.window.resize(800, 640)
        self.window.show()
        self.app.processEvents()
        self.panel = self.window.role_page.size_panel

    def capture(self, cfg):
        self.cfg = copy.deepcopy(cfg)

    def wait_for(self, predicate):
        timer = QElapsedTimer()
        timer.start()
        while not predicate() and timer.elapsed() < 3000:
            QTest.qWait(10)
        self.assertTrue(predicate())

    def test_size_list_and_restore_follow_top_selection_with_no_second_selection_control(self):
        self.assertEqual(self.panel.restore_button.text(), "恢复选中尺寸（2）")
        self.assertTrue(self.panel.restore_button.isEnabled())
        self.assertEqual(self.panel.row_labels[101][2].text(), "一致")
        self.assertEqual(self.panel.row_labels[103][2].text(), "不一致")
        self.window._toggle_window(103)
        self.assertEqual(self.panel.restore_button.text(), "恢复选中尺寸（1）")
        self.assertFalse(self.panel.row_labels[103][0].text().startswith("●"))
        self.assertEqual(self.panel.findChildren(QCheckBox), [])

    def test_restore_runs_in_background_with_frozen_handles_and_immediate_ui_lock(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        snapshots, threads = [], []
        def restore(cfg, action):
            snapshots.append(copy.deepcopy(cfg))
            threads.append(threading.get_ident())
            entered.set()
            release.wait(3)
            return 2, 2, [[1600, 1200], [1600, 1200]]
        with patch("mhxy.qtui.window.run_window_action", side_effect=restore) as worker:
            self.panel.restore_button.click()
            self.wait_for(entered.is_set)
            self.assertFalse(self.panel.restore_button.isEnabled())
            self.assertFalse(self.panel.record_button.isEnabled())
            self.assertFalse(self.window.clear_selection_button.isEnabled())
            self.assertFalse(self.window.role_page.run_button.isEnabled())
            self.window.restore_selected_size()
            self.window._change_selection("clear")
            self.assertEqual(self.window.selection.selected, {101, 103})
            with self.assertRaises(ValueError):
                self.window.prepare_task_config(self.cfg, "escort")
            self.assertEqual(snapshots[0]["targets"]["window_handles"], [101, 103])
            self.assertNotEqual(threads[0], threading.get_ident())
            release.set()
            self.wait_for(lambda: not self.window._window_action)
            self.assertEqual(worker.call_count, 1)
        self.assertTrue(self.panel.restore_button.isEnabled())
        self.assertIn("2/2", self.panel.status.text())
        self.assertEqual(self.panel.row_labels[103][2].text(), "一致")

    def test_empty_selection_no_base_scan_error_running_and_editor_prevent_restore(self):
        with patch("mhxy.qtui.window.run_window_action") as worker:
            self.window._change_selection("clear")
            self.window.restore_selected_size()
            self.assertFalse(self.panel.restore_button.isEnabled())
            self.window._change_selection("all")
            self.window._apply_windows([], "scan error")
            self.window.restore_selected_size()
            self.assertFalse(self.panel.restore_button.isEnabled())
            self.window._apply_windows(self.states, "")
            with patch.object(self.window, "_calibration_running", return_value=True):
                self.window._update_strip()
                self.window.restore_selected_size()
                self.assertFalse(self.panel.record_button.isEnabled())
            self.panel._editing = True
            self.window.restore_selected_size()
            self.panel._editing = False
            cfg = copy.deepcopy(self.window.cfg)
            for profile in calib.items(cfg):
                calib.delete(cfg, profile["id"])
            self.window._config_changed(cfg)
            self.window.restore_selected_size()
            self.assertFalse(self.panel.restore_button.isEnabled())
        worker.assert_not_called()

    def test_restore_reports_actual_locked_ratio_partial_failure_missing_and_worker_error(self):
        for result, error, expected in (
            ((0, 2, [[1500, 1100], [1500, 1100]]), "", "实际尺寸 1500×1100"),
            ((1, 2, [[1600, 1200], None]), "", "部分窗口调整失败"),
            ((0, 0, []), "", "目标窗口已变化"),
            (None, "test resize error", "test resize error"),
        ):
            with self.subTest(expected=expected):
                self.window._window_action = "restore_size"
                self.window._window_action_size = [1600, 1200]
                self.window._window_action_handles = [101, 103]
                self.window._window_action_result("restore_size", result, error)
                self.assertIn(expected, self.panel.status.text())
                self.assertTrue(self.panel.restore_button.isEnabled())

    def test_switch_only_changes_baseline_and_save_failure_rolls_back_checked_profile(self):
        with patch("mhxy.qtui.window.run_window_action") as worker:
            self.save.side_effect = OSError("disk full")
            self.panel.profile_buttons[1].click()
            self.assertEqual(calib.active_id(self.panel.cfg), self.first)
            self.assertTrue(self.panel.profile_buttons[0].isChecked())
            self.assertFalse(self.panel.profile_buttons[1].isChecked())
            self.save.side_effect = self.capture
            self.panel.profile_buttons[1].click()
            self.assertEqual(calib.active_id(self.window.cfg), self.second)
            self.assertEqual(self.window.cfg["targets"]["base_size"], [1200, 900])
            self.assertEqual(self.window.selection.selected, {101, 103})
        worker.assert_not_called()

    def test_record_uses_single_selected_reference_and_cancel_or_failed_save_leaves_groups_unchanged(self):
        self.window._toggle_window(101)
        self.panel.record_size()
        self.assertEqual(calib.active_id(self.window.cfg), self.second)
        self.window._toggle_window(101)
        before = copy.deepcopy(self.window.cfg)
        with patch("mhxy.qtui.window_size_panel.QInputDialog.getItem", return_value=("", False)):
            self.panel.record_size()
        self.assertEqual(self.window.cfg, before)
        def choose(parent, title, prompt, labels, *args):
            self.assertEqual(len(labels), 2)
            self.assertTrue(self.window._selection_locked())
            return labels[0], True
        with patch("mhxy.qtui.window_size_panel.QInputDialog.getItem", side_effect=choose):
            self.panel.record_size()
        self.assertEqual(calib.active_id(self.window.cfg), self.first)
        self.assertFalse(self.window._selection_locked())

    def test_full_group_replacement_cancel_and_save_failure_preserve_original_groups(self):
        calib.get_or_create(self.cfg, [1800, 1300])
        self.window._config_changed(copy.deepcopy(self.cfg))
        states = [replace(s, size=(2000, 1400)) for s in self.states]
        self.window._apply_windows(states, "")
        self.window._toggle_window(103)
        before = copy.deepcopy(self.window.cfg)
        with patch("mhxy.qtui.calibration_dialog.QInputDialog.getItem", return_value=("", False)):
            self.panel.record_size()
        self.assertEqual(self.window.cfg, before)
        def choose(parent, title, prompt, labels, *args):
            return labels[0], True
        self.save.side_effect = OSError("disk full")
        with patch("mhxy.qtui.calibration_dialog.QInputDialog.getItem", side_effect=choose):
            self.panel.record_size()
        self.assertEqual(self.window.cfg, before)
        self.assertIn("保存失败", self.panel.status.text())

    def test_manage_delete_and_calibration_jump_keep_one_source_of_truth(self):
        self.window.show_page("calibration")
        page = self.window.pages["calibration"]
        self.assertFalse(hasattr(page, "profile_buttons"))
        self.assertIn("1600×1200", page.profile_note.text())
        page.manage_sizes_button.click()
        self.assertEqual(self.window.current_page, "role_window")
        def manage(dialog):
            self.assertTrue(self.window._selection_locked())
            next(b for b in dialog.findChildren(QPushButton) if b.text() == "删除（保留模板）").click()
            dialog.reject()
            return 0
        with patch.object(QDialog, "exec", manage):
            self.panel.manage_button.click()
        self.assertIsNone(calib.get(self.window.cfg, self.first))
        self.assertIn("1200×900", page.profile_note.text())
        self.assertFalse(self.window._selection_locked())

    def test_launch_remains_accessible_with_windows_and_manual_expansion_survives_refresh(self):
        page = self.window.role_page
        self.assertFalse(page.launch_panel.isVisible())
        page.launch_section.toggle.click()
        self.assertTrue(page.launch_panel.isVisible())
        self.window._apply_windows(self.states, "")
        self.assertTrue(page.launch_panel.isVisible())
        page.launch_section.toggle.click()
        self.assertFalse(page.launch_panel.isVisible())
        self.assertFalse(hasattr(page, "window_tools"))
        self.assertTrue(self.window.window_tools.close_button.isVisible())

    def test_no_window_prioritizes_launch_and_start_stays_before_long_profile_list(self):
        page = self.window.role_page
        self.window._apply_windows([], "")
        self.assertEqual(page._body_layout.indexOf(page.launch_section), 0)
        self.assertTrue(page.launch_panel.isVisible())
        self.assertLess(page.launch_panel.layout().indexOf(page.status),
                        page.launch_panel.layout().indexOf(page.profile_body))
        self.window._apply_windows(self.states, "")
        self.assertEqual(page._body_layout.indexOf(page.launch_section), page._body_layout.indexOf(page.size_panel) + 1)


if __name__ == "__main__":
    unittest.main()
