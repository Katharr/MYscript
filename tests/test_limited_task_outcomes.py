# -*- coding: utf-8 -*-
"""第四阶段限定结果门禁：假窗口/输入，独立配置、模板与快照目录。"""
import copy
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from mhxy.core import config, scan
from mhxy.core.inventory import InventoryOrganizer, InventoryRecoveryError
from mhxy.tasks.sniper import SniperTask
from mhxy.tasks.freeclick import FreeClickTask
from mhxy.tasks.organize_bag import OrganizeBagTask


class Context:
    def __init__(self, cfg, stop_event=None):
        self.cfg = cfg
        self.stop_event = stop_event or threading.Event()
        self.input_session = None
        self.label = "测试窗口"
        self.window = Mock()
        self.window.rect.return_value = (0, 0, 800, 600)
        self.window.activate.return_value = True
        self.window.region_to_screen_rect.return_value = (0, 0, 800, 600)
        self.mouse = Mock()
        self.send_hotkey = Mock(return_value=True)
        self.log = Mock()
        self.hotkeys = {"open_shop": ["alt", "a"], "open_bag": ["alt", "e"]}
        self.windows = [self.window]
        self.children = {}

    def should_stop(self):
        return self.stop_event.is_set()

    def task_cfg(self, name):
        return self.cfg["tasks"][name]

    def select_windows(self):
        return self.windows

    def window_labels(self, wins):
        return ["测试窗口" for _ in wins]

    def make_child(self, window, label=None):
        if window not in self.children:
            child = Context(self.cfg, self.stop_event)
            child.window = window
            self.children[window] = child
        return self.children[window]

    def detection_rect(self, region):
        return self.window.rect()


class OfflineResults(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mhxy_task_results_")
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        for module, name, value in (
                (config, "CONFIG_PATH", root / "config.json"),
                (config, "CONFIG_BACKUP_DIR", root / "snapshots"),
                (config, "TEMPLATES_DIR", root / "templates"),
                (config, "CAPTURES_DIR", root / "captures")):
            p = patch.object(module, name, value)
            p.start()
            self.addCleanup(p.stop)
        for target, value in (("mhxy.core.vision.PROJECT_ROOT", root),
                              ("mhxy.tasks.base.CAPTURES_DIR", root / "captures")):
            p = patch(target, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch("mhxy.core.vision.load_template", return_value=object())
        self.load = p.start()
        self.addCleanup(p.stop)
        # 所有截图入口封住，未设置的场景用无效帧而不触碰真实游戏。
        for target in ("mhxy.core.window.grab", "mhxy.core.window.grab_scene"):
            p = patch(target, return_value=None)
            p.start()
            self.addCleanup(p.stop)
        self.cfg = copy.deepcopy(config.DEFAULT_CONFIG)
        self.cfg["targets"]["multi"] = False
        self.ctx = Context(self.cfg)

    def sniper(self):
        task = SniperTask()
        self.cfg["tasks"]["sniper"]["templates"] = {key: key for key, _, _ in task.CALIBRATION["templates"]}
        self.cfg["tasks"]["sniper"]["dry_run"] = False
        return task

    def test_sniper_no_windows_and_stop_priority(self):
        self.ctx.windows = []
        task = self.sniper()
        self.assertEqual(task.run(self.ctx), "failed")
        self.ctx.stop_event.set()
        self.assertEqual(task.run(self.ctx), "stopped")

    def test_sniper_real_rotation_counts_success_to_completed(self):
        task = self.sniper()
        task._match = Mock(side_effect=[(hit, (0, 0, 800, 600)) for hit in
                                       (None, (1, 2, .99), (1, 2, .99), (1, 2, .99), (1, 2, .99))])
        with patch.object(task, "_interruptible_sleep"), patch.object(task, "_close_interference"), \
                patch("mhxy.core.rotation._sleep"):
            self.assertEqual(task.run(self.ctx), "completed")
        self.assertEqual(task.progress[0][1], 1)
        self.assertEqual(self.ctx.mouse.click.call_count, 3)
        self.ctx.send_hotkey.assert_not_called()

    def test_sniper_real_two_windows_one_completed_then_capture_fails(self):
        task = self.sniper()
        self.cfg["targets"]["multi"] = True
        second = Mock()
        second.rect.return_value = (0, 0, 800, 600)
        second.activate.return_value = True
        self.ctx.windows = [self.ctx.window, second]
        task._match = Mock(side_effect=[(hit, (0, 0, 800, 600)) for hit in
                                       (None, (1, 2, .99), (1, 2, .99), (1, 2, .99), (1, 2, .99))]
                          + [RuntimeError("模拟次窗截图失败")])
        with patch.object(task, "_interruptible_sleep"), patch.object(task, "_close_interference"), \
                patch("mhxy.core.rotation._sleep"):
            self.assertEqual(task.run(self.ctx), "partial")
        self.assertEqual([row[1] for row in task.progress], [1, 0])
        self.assertEqual(self.ctx.children[self.ctx.window].mouse.click.call_count, 3)
        self.ctx.children[second].mouse.click.assert_not_called()

    def test_organize_task_valid_current_frame_dry_run_completed_with_zero_input(self):
        task = OrganizeBagTask()
        task._is_admin = lambda: True
        task._interruptible_sleep = Mock()
        self.cfg["tasks"]["organize_bag"].update({"dry_run": True, "items": [{"template": "item"}]})
        with patch("mhxy.core.window.grab", return_value=object()), \
                patch("mhxy.core.vision.match", return_value=None), \
                patch("mhxy.core.inventory.scan.scroll_search") as search:
            self.assertEqual(task.run(self.ctx), "completed")
        child = self.ctx.children[self.ctx.window]
        child.send_hotkey.assert_not_called()
        self.assertEqual(child.mouse.mock_calls, [])
        search.assert_not_called()

    def test_sniper_existing_count_survives_rotation_failure(self):
        for count, expected in ((0, "failed"), (1, "partial")):
            with self.subTest(count=count):
                task = self.sniper()
                def fail(rc):
                    rc.records[0]["count"] = count
                    raise RuntimeError("模拟截图失败")
                with patch("mhxy.tasks.sniper.rotation.run_rotation", side_effect=fail):
                    self.assertEqual(task.run(self.ctx), expected)
                self.ctx.mouse.click.assert_not_called()
                self.ctx.send_hotkey.assert_not_called()

    def test_sniper_termination_evidence_and_stop_priority(self):
        for reason, count, expected in (("timeout", 0, "timeout"), ("timeout", 1, "partial"),
                                        ("aborted", 0, "failed"), ("completed", 0, "failed"),
                                        ("stopped", 1, "stopped")):
            with self.subTest(reason=reason, count=count):
                self.ctx.stop_event.clear()
                task = self.sniper()
                def end(rc):
                    rc.termination_reason = reason
                    rc.records[0]["count"] = count
                    if reason == "stopped":
                        self.ctx.stop_event.set()
                with patch("mhxy.tasks.sniper.rotation.run_rotation", side_effect=end):
                    self.assertEqual(task.run(self.ctx), expected)

    def test_sniper_dry_run_continues_until_stop_without_count_or_input(self):
        task = self.sniper()
        self.cfg["tasks"]["sniper"]["dry_run"] = True
        task._match = Mock(side_effect=[(None, (0, 0, 800, 600)), ((1, 2, .99), (0, 0, 800, 600))])
        def stop_after_round(rc, seconds):
            self.ctx.stop_event.set()
        with patch("mhxy.core.rotation._sleep", side_effect=stop_after_round):
            self.assertEqual(task.run(self.ctx), "stopped")
        self.assertEqual(task.progress[0][1], 0)
        self.assertEqual(self.ctx.mouse.mock_calls, [])
        self.ctx.send_hotkey.assert_not_called()

    def freeclick(self, dry):
        task = FreeClickTask()
        self.cfg["tasks"]["freeclick"] = {"items": [{"name": "a", "template": "a"},
                                                       {"name": "b", "template": "b"}], "dry_run": dry}
        task._is_admin = lambda: True
        return task

    def test_freeclick_no_assets_or_no_windows_failed(self):
        task = self.freeclick(True)
        with patch("mhxy.core.vision.load_template", return_value=None):
            self.assertEqual(task.run(self.ctx), "failed")
        self.ctx.windows = []
        self.assertEqual(task.run(self.ctx), "failed")
        self.ctx.stop_event.set()
        self.assertEqual(task.run(self.ctx), "stopped")

    def test_freeclick_hits_do_not_become_completed_or_partial(self):
        for dry in (True, False):
            with self.subTest(dry=dry):
                self.ctx.stop_event.clear()
                self.ctx.mouse.reset_mock()
                task = self.freeclick(dry)
                scene = Mock()
                scene.match.return_value = (1, 2, .99)
                scene.to_screen.return_value = (1, 2)
                def sleep(ctx, seconds):
                    # after_click 默认 .35；整轮结束默认 .8。
                    if seconds == .8:
                        ctx.stop_event.set()
                with patch.object(task, "_grab_scene", return_value=scene), \
                        patch.object(task, "_jitter", side_effect=lambda s, c: s), \
                        patch.object(task, "_interruptible_sleep", side_effect=sleep):
                    self.assertEqual(task.run(self.ctx), "stopped")
                self.assertEqual(scene.match.call_count, 2)
                self.assertEqual(self.ctx.mouse.click.call_count, 0 if dry else 2)
                self.ctx.send_hotkey.assert_not_called()

    def test_freeclick_exception_is_failed_even_after_hits_and_stop_wins(self):
        for stopped, expected in ((False, "failed"), (True, "stopped")):
            with self.subTest(stopped=stopped):
                self.ctx.stop_event.clear()
                task = self.freeclick(False)
                def fail(*args):
                    if stopped:
                        self.ctx.stop_event.set()
                    raise RuntimeError("模拟识别异常")
                with patch.object(task, "_click_one_round", side_effect=fail):
                    self.assertEqual(task.run(self.ctx), expected)
                self.ctx.mouse.click.assert_not_called()

    def test_freeclick_lost_windows_waits_until_stop_without_input(self):
        task = self.freeclick(False)
        with patch.object(task, "_ensure_contexts", return_value=[]), \
                patch.object(task, "_interruptible_sleep", side_effect=lambda c, s: c.stop_event.set()):
            self.assertEqual(task.run(self.ctx), "stopped")
        self.assertEqual(self.ctx.mouse.mock_calls, [])

    def test_organize_task_unresolved_evidence_is_not_completed(self):
        task = OrganizeBagTask()
        task._is_admin = lambda: True
        task._interruptible_sleep = Mock()
        for key in ("action_failures", "scan_limited", "observation_failed", "stall_full"):
            for handled, expected in ((0, "failed"), (1, "partial")):
                with self.subTest(key=key, handled=handled):
                    evidence = {"handled": handled, "action_failures": 0,
                                "scan_limited": False, "observation_failed": False}
                    evidence[key] = True
                    organizer = Mock(evidence=evidence)
                    organizer.organize.return_value = (handled, "done")
                    with patch("mhxy.tasks.organize_bag.InventoryOrganizer", return_value=organizer):
                        self.assertEqual(task.run(self.ctx), expected)
                    organizer.organize.assert_called_once()

    def test_organize_task_no_windows_or_preexisting_stop(self):
        task = OrganizeBagTask()
        self.ctx.windows = []
        self.assertEqual(task.run(self.ctx), "failed")
        self.ctx.stop_event.set()
        self.assertEqual(task.run(self.ctx), "stopped")

    def organizer(self, dry=False):
        ob = {"dry_run": dry, "items": [{"template": "item", "action": "use"}],
              "loop": {"passes": 1}, "templates": {}}
        organizer = InventoryOrganizer(self.ctx, ob, dry)
        organizer._sleep = Mock()
        return organizer

    def test_inventory_dry_frame_failure_preserves_old_return_and_adds_evidence(self):
        organizer = self.organizer(True)
        self.assertEqual(organizer.organize(), (0, "done"))
        self.assertTrue(organizer.evidence["observation_failed"])
        self.ctx.send_hotkey.assert_not_called()
        self.assertEqual(self.ctx.mouse.mock_calls, [])

    def test_inventory_scan_cap_keeps_old_done_and_action_order(self):
        organizer = self.organizer()
        actions = []
        def search(**kw):
            with patch("mhxy.core.inventory.vision.match", return_value=(1, 2, .99)):
                actions.append(kw["probe"](object(), (0, 0, 800, 600))[0])
            return scan.ScanResult("exhausted", reached_end=False)
        with patch("mhxy.core.inventory.scan.scroll_search", side_effect=search), \
                patch.object(organizer, "_do_action", return_value=True):
            self.assertEqual(organizer.organize(), (1, "done"))
        self.assertEqual(actions, [scan.HANDLED])
        self.assertEqual(organizer.evidence["handled"], 1)
        self.assertTrue(organizer.evidence["scan_limited"])
        self.assertEqual([call.args[0] for call in self.ctx.send_hotkey.call_args_list],
                         ["open_bag", "close_panel"])

    def test_inventory_failed_action_remains_scroll_and_evidence_resets(self):
        organizer = self.organizer()
        def search(**kw):
            with patch("mhxy.core.inventory.vision.match", return_value=(1, 2, .99)):
                self.assertEqual(kw["probe"](object(), (0, 0, 800, 600))[0], scan.SCROLL)
            return scan.ScanResult("exhausted", reached_end=True)
        with patch("mhxy.core.inventory.scan.scroll_search", side_effect=search), \
                patch.object(organizer, "_do_action", return_value=False):
            self.assertEqual(organizer.organize(), (0, "done"))
        self.assertEqual(organizer.evidence["action_failures"], 1)
        with patch("mhxy.core.inventory.scan.scroll_search", return_value=scan.ScanResult("exhausted", reached_end=True)):
            organizer.organize()
        self.assertEqual(organizer.evidence["action_failures"], 0)

    def test_inventory_exception_preserves_count_evidence_and_cleanup(self):
        organizer = self.organizer()
        def search(**kw):
            with patch("mhxy.core.inventory.vision.match", return_value=(1, 2, .99)):
                kw["probe"](object(), (0, 0, 800, 600))
            raise InventoryRecoveryError("模拟收尾失败")
        with patch("mhxy.core.inventory.scan.scroll_search", side_effect=search), \
                patch.object(organizer, "_do_action", return_value=True):
            with self.assertRaises(InventoryRecoveryError):
                organizer.organize()
        self.assertEqual(organizer.evidence["handled"], 1)
        self.assertEqual(self.ctx.send_hotkey.call_count, 2)

    def test_organize_task_aggregates_windows_without_retries(self):
        task = OrganizeBagTask()
        task._is_admin = lambda: True
        task._interruptible_sleep = Mock()
        self.cfg["targets"]["multi"] = True
        second = Mock()
        second.activate.return_value = True
        self.ctx.windows = [self.ctx.window, second]
        clean = {"handled": 0, "action_failures": 0, "scan_limited": False, "observation_failed": False}
        for reasons, expected in ((("done", "done"), "completed"),
                                  (("done", "no_hotkey"), "partial"),
                                  (("no_items", "no_hotkey"), "failed")):
            with self.subTest(reasons=reasons):
                instances = [Mock(evidence=clean.copy()), Mock(evidence=clean.copy())]
                for instance, reason in zip(instances, reasons):
                    instance.organize.return_value = (0, reason)
                with patch("mhxy.tasks.organize_bag.InventoryOrganizer", side_effect=instances):
                    self.assertEqual(task.run(self.ctx), expected)
                for instance in instances:
                    instance.organize.assert_called_once()

    def test_organize_task_progress_failure_and_stop_priority(self):
        task = OrganizeBagTask()
        task._is_admin = lambda: True
        task._interruptible_sleep = Mock()
        for handled, stopped, expected in ((0, False, "failed"), (1, False, "partial"), (1, True, "stopped")):
            with self.subTest(handled=handled, stopped=stopped):
                self.ctx.stop_event.clear()
                organizer = Mock(evidence={"handled": handled})
                def fail():
                    if stopped:
                        self.ctx.stop_event.set()
                    raise InventoryRecoveryError("模拟收尾失败")
                organizer.organize.side_effect = fail
                with patch("mhxy.tasks.organize_bag.InventoryOrganizer", return_value=organizer):
                    self.assertEqual(task.run(self.ctx), expected)
                organizer.organize.assert_called_once()

    def test_organize_task_dry_invalid_frame_failed_and_activation_skip(self):
        task = OrganizeBagTask()
        task._is_admin = lambda: True
        task._interruptible_sleep = Mock()
        self.cfg["tasks"]["organize_bag"].update({"dry_run": True, "items": [{"template": "item"}]})
        self.assertEqual(task.run(self.ctx), "failed")
        self.ctx.send_hotkey.assert_not_called()
        self.assertEqual(self.ctx.mouse.mock_calls, [])
        self.ctx.window.activate.return_value = False
        with patch("mhxy.tasks.organize_bag.InventoryOrganizer") as factory:
            self.assertEqual(task.run(self.ctx), "failed")
            factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
