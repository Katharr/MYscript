"""统一选窗回归：全程使用虚拟句柄与临时配置，不接触游戏或真实配置。"""

import copy
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest
from unittest.mock import patch

from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtTest import QTest

from mhxy.core import config as cfg_mod, window as win_mod
from mhxy.core.input_session import InputSession
from mhxy.core.runner import TaskRunner
from mhxy.core.runtime import RuntimeService
from mhxy.core.task_activity import report
from mhxy.qtui.main import get_application
from mhxy.qtui.role_strip import WindowState
from mhxy.qtui.window import MainWindow


class SharedSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = get_application(["shared-selection-tests", "-platform", "offscreen"])

    def setUp(self):
        self.cfg = copy.deepcopy(cfg_mod.DEFAULT_CONFIG)
        self.cfg["targets"].update(multi=True, multi_indices=[0, 1], max_windows=0)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.load = patch.object(cfg_mod, "load_config", side_effect=lambda: copy.deepcopy(self.cfg))
        self.save = patch.object(cfg_mod, "save_config", side_effect=self.capture)
        self.scan = patch.object(MainWindow, "refresh_windows")
        self.load.start()
        self.save_mock = self.save.start()
        self.scan.start()
        self.addCleanup(patch.stopall)
        self.window = MainWindow(QSettings(str(Path(self.temp.name) / "ui.ini"), QSettings.Format.IniFormat))
        self.window.scan_timer.stop()
        self.window.runtime_timer.stop()
        self.window.show()
        self.app.processEvents()
        self.states = [WindowState(101 + i, f"测试角色{i + 1}", "ready") for i in range(3)]
        self.window._apply_windows(self.states, "")
        self.app.processEvents()
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.window.close)

    def capture(self, cfg):
        self.cfg = copy.deepcopy(cfg)

    def test_card_keyboard_and_captain_click_are_independent_and_exclusive(self):
        first, second = self.window.role_strip.items[101], self.window.role_strip.items[102]
        QTest.mouseClick(first, Qt.MouseButton.LeftButton, pos=QPoint(60, first.height() - 10))
        self.assertEqual(self.window.selection.selected, {102})
        QTest.keyClick(first, Qt.Key.Key_Space)
        self.assertEqual(self.window.selection.selected, {101, 102})
        QTest.mouseClick(second.captain, Qt.MouseButton.LeftButton, pos=QPoint(8, second.captain.height() // 2))
        self.assertEqual(self.window.selection.captain, 102)
        self.assertEqual(self.window.selection.selected, {101, 102})
        self.assertEqual(sum(i.captain.isChecked() for i in self.window.role_strip.items.values()), 1)
        second.captain.click()
        self.assertTrue(second.captain.isChecked())

    def test_all_is_explicit_new_windows_remain_unchecked_and_names_preserve_selection(self):
        self.window._change_selection("all")
        self.window._apply_windows([self.states[2], self.states[0], self.states[1], WindowState(104, "新角色")], "")
        self.assertEqual(self.window.selection.selected, {101, 102, 103})
        self.assertFalse(self.window.role_strip.items[104].selected_box.isChecked())
        self.assertEqual(self.window.selection.captain, 101)
        renamed = [WindowState(s.hwnd, "更新角色名称", "ready", True) for s in self.states]
        self.window._apply_windows(renamed, "")
        self.assertEqual(self.window.selection.selected, {101, 102, 103})
        self.assertIn("已最小化", self.window.role_strip.items[101].toolTip())

    def test_long_name_elides_at_actual_width_and_remains_accessible(self):
        name = "这是一个需要省略但悬停可以查看的很长角色名称"
        self.window._apply_windows([WindowState(101, name, "ready"), *self.states[1:]], "")
        self.app.processEvents()
        item = self.window.role_strip.items[101]
        self.assertEqual(item.name.toolTip(), name)
        self.assertIn("…", item.name.text())
        self.assertLessEqual(item.name.fontMetrics().horizontalAdvance(item.name.text()), item.name.width())
        self.assertTrue(item.rect().contains(item.activity.geometry()))
        self.assertTrue(item.rect().contains(item.captain.geometry()))
        self.assertGreaterEqual(item.activity.height(), item.activity.fontMetrics().height() + 4)

    def test_business_runner_receives_isolated_snapshot_from_shared_selection(self):
        self.window.show_page("escort")
        page = self.window.pages["escort"]
        page.runner_factory = lambda cfg: SimpleNamespace(cfg=copy.deepcopy(cfg))
        self.window._toggle_window(101)
        runner = page._make_runner()
        self.assertEqual(runner.cfg["targets"]["window_handles"], [102])
        self.window._change_selection("all")
        self.assertEqual(runner.cfg["targets"]["window_handles"], [102])

    def test_compact_cards_leave_space_and_scroll_only_when_needed_after_resize(self):
        strip = self.window.role_strip
        states = [WindowState(101 + i, f"示例角色{i + 1}", "ready") for i in range(5)]

        def layout(width, count):
            self.window.resize(width, 720)
            self.window._apply_windows(states[:count], "")
            for _ in range(4):
                self.app.processEvents()

        layout(800, 2)
        first, second = strip.items[101], strip.items[102]
        widths = [first.width(), second.width()]
        self.assertEqual(widths[0], widths[1])
        self.assertLess(second.geometry().right(), strip.viewport().width() * .75)
        self.assertEqual(strip.horizontalScrollBar().maximum(), 0)
        compact_height = strip.height()
        layout(800, 3)
        self.assertEqual(strip.horizontalScrollBar().maximum(), 0)
        self.assertEqual(strip.height(), compact_height)
        layout(800, 5)
        self.assertGreater(strip.horizontalScrollBar().maximum(), 0)
        self.assertGreater(strip.height(), compact_height)
        strip.horizontalScrollBar().setValue(strip.horizontalScrollBar().maximum())
        self.assertLessEqual(strip.widget().mapTo(strip.viewport(), strip.items[105].geometry().bottomRight()).x(), strip.viewport().width())
        layout(1120, 5)
        self.assertEqual(strip.horizontalScrollBar().maximum(), 0)
        self.assertEqual(strip.height(), compact_height)
        self.assertEqual([strip.items[h].width() for h in (101, 102)], widths)
        for item in strip.items.values():
            self.assertTrue(item.rect().contains(item.activity.geometry()))
            self.assertFalse(item.activity.geometry().intersects(item.captain.geometry()))

    def test_long_activity_is_elided_without_losing_full_status(self):
        status = "等待其它角色完成当前运镖任务后集合…"
        self.window._window_activities = lambda: {101: (status, "warning")}
        self.window._update_strip()
        self.app.processEvents()
        label = self.window.role_strip.items[101].activity
        self.assertEqual(label.toolTip(), status)
        self.assertEqual(label.accessibleName(), status)
        self.assertIn("…", label.text())
        self.assertLessEqual(label.fontMetrics().horizontalAdvance(label.text()), label.width())

    def test_snapshot_preserves_order_and_rejects_missing_targets_without_substitution(self):
        snapshot = self.window.prepare_task_config(self.cfg, "escort")
        self.window._apply_windows(list(reversed(self.states)), "")
        wins = [SimpleNamespace(_win=SimpleNamespace(_hWnd=s.hwnd)) for s in reversed(self.states)]
        with patch.object(win_mod, "locate_all", return_value=wins):
            selected = win_mod.resolve_targets("test", [0, 0], snapshot["targets"])
            self.assertEqual([w._win._hWnd for w in selected], [101, 102])
        with patch.object(win_mod, "locate_all", return_value=[wins[0], wins[2]]):
            self.assertEqual(win_mod.resolve_targets("test", [0, 0], snapshot["targets"]), [])

    def test_empty_selection_disables_business_start_but_not_login_and_never_means_all(self):
        from mhxy.qtui.window_selection import WindowSelection
        initial = WindowSelection()
        initial.reconcile([], self.cfg, observed=True)
        initial.reconcile(self.states, self.cfg, observed=True)
        self.assertEqual(initial.selected, set())
        self.assertEqual(initial.captain, 101)
        self.window.show_page("escort")
        self.window._change_selection("clear")
        self.assertFalse(self.window.pages["escort"].start_button.isEnabled())
        self.assertTrue(self.window.role_page.run_button.isEnabled())
        with patch.object(win_mod, "locate_all", return_value=[SimpleNamespace(_win=SimpleNamespace(_hWnd=101))]):
            self.assertEqual(win_mod.resolve_targets("test", [0, 0], self.cfg["targets"]), [])
        with self.assertRaises(ValueError):
            self.window.prepare_task_config(self.cfg, "escort")

    def test_global_captain_updates_every_team_namespace_and_requires_participation(self):
        self.window._select_captain(102)
        for name in ("teaming", "taohaiqu"):
            self.assertEqual(cfg_mod.task_config(self.cfg, name)["captain_index"], 1)
        self.window._toggle_window(102)
        with self.assertRaisesRegex(ValueError, "队长角色"):
            self.window.prepare_task_config(self.cfg, "taohaiqu")
        self.assertEqual(self.window.prepare_task_config(self.cfg, "escort")["targets"]["window_handles"], [101])

    def test_saving_failure_restores_checkbox_and_captain_without_losing_selection(self):
        self.save_mock.side_effect = OSError("test failure")
        self.window.role_strip.items[102].captain.click()
        self.assertEqual(self.window.selection.captain, 101)
        self.assertTrue(self.window.role_strip.items[101].captain.isChecked())
        self.window.role_strip.items[101].selected_box.click()
        self.assertTrue(self.window.role_strip.items[101].selected_box.isChecked())
        self.assertEqual(self.window.selection.selected, {101, 102})

    def test_pending_and_stopping_lock_selection_until_actual_worker_exit(self):
        self.window.show_page("escort")
        page = self.window.pages["escort"]
        page._start_pending = True
        self.window._update_strip()
        self.assertFalse(self.window.role_strip.items[101].captain.isEnabled())
        self.window._change_selection("clear")
        self.assertEqual(self.window.selection.selected, {101, 102})
        page._start_pending = False
        running = [True]
        page.runner = SimpleNamespace(is_running=lambda: running[0], stop_event=threading.Event(), outcome="running")
        page.runner.stop_event.set()
        self.window._update_strip()
        self.assertFalse(self.window.clear_selection_button.isEnabled())
        running[0] = False
        self.window._update_strip()
        self.assertTrue(self.window.clear_selection_button.isEnabled())

    def test_status_reporting_is_per_window_and_preflight_never_claims_running(self):
        cfg = self.window.prepare_task_config(self.cfg, "daily")
        task = SimpleNamespace(name="daily", title="日常")
        runner = TaskRunner(task, cfg)
        runner.runtime = RuntimeService()
        session = InputSession(cfg, runner.stop_event, "daily", activity_fn=runner.report_activity)
        contexts = [SimpleNamespace(window=SimpleNamespace(_win=SimpleNamespace(_hWnd=h)), input_session=session)
                    for h in (101, 102)]
        with session.activate(preflight=True):
            report(contexts[0], "挖宝中…")
        self.assertEqual(runner.window_activities(), {})
        with session.activate():
            with session.policy("treasure_map"):
                report(contexts[0])
            with session.policy("escort"):
                report(contexts[1])
        runner.outcome = "running"
        self.window._role_runs.append(runner)
        self.window._update_strip()
        self.assertEqual(self.window.role_strip.items[101].activity.text(), "挖宝中…")
        self.assertEqual(self.window.role_strip.items[102].activity.text(), "运镖中…")
        self.assertEqual(self.window.role_strip.items[103].activity.text(), "空闲")
        runner.report_activity(102, "已完成", "success")
        runner.stop_event.set()
        self.window._update_strip()
        self.assertEqual(self.window.role_strip.items[101].activity.text(), "停止中…")
        self.assertEqual(self.window.role_strip.items[102].activity.text(), "已完成")
        runner.outcome = "stopped"
        self.window._update_strip()
        self.assertEqual(self.window.role_strip.items[101].activity.text(), "已停止")

    def test_disappeared_selected_window_is_removed_and_captain_reassigned_when_idle(self):
        self.window._apply_windows(self.states[1:], "")
        self.assertEqual(self.window.selection.selected, {102})
        self.assertEqual(self.window.selection.captain, 102)
        self.window._apply_windows([self.states[0], *self.states[1:]], "")
        self.assertEqual(self.window.selection.selected, {102})

    def test_team_captain_loss_requests_stop_before_reassignment(self):
        self.window.show_page("general")
        page = self.window.pages["general"]
        page.task_name = "dungeon"
        runner = TaskRunner(SimpleNamespace(name="dungeon", title="组队"),
                            self.window.prepare_task_config(self.cfg, "dungeon"))
        runner.outcome = "running"
        page.runner = runner
        self.window._role_runs.append(runner)
        self.window._apply_windows(self.states[1:], "")
        self.assertTrue(runner.stop_event.is_set())
        self.assertEqual(self.window.selection.captain, 101)
        self.assertFalse(self.window.role_strip.items[102].captain.isEnabled())
        runner.outcome = "stopped"
        self.window._update_strip()
        self.assertEqual(self.window.selection.captain, 102)


if __name__ == "__main__":
    unittest.main()
