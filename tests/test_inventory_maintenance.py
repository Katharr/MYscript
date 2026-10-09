# -*- coding: utf-8 -*-
"""第三阶段背包共享能力离线门禁：所有窗口、截图和输入均为假对象。"""
import copy
import threading
import unittest
from unittest import mock

from mhxy.core import scan, rotation
from mhxy.core.context import TaskContext
from mhxy.core.input_session import InputSession
from mhxy.core.inventory import InventoryOrganizer, InventoryRecoveryError
from mhxy.core.inventory_maintenance import AutoInventoryMaintenance
from mhxy.core.window import GameWindow
from mhxy.tasks.base import Task
from tests.test_architecture_baseline import _Context, _Window, _Daily


class _Ctx:
    def __init__(self, ob=None):
        self.cfg = {"tasks": {"organize_bag": ob or {}}}
        self.window = mock.Mock()
        self.window.rect.return_value = (0, 0, 800, 600)
        self.window.activate.return_value = True
        self.window.region_to_screen_rect.return_value = (0, 0, 800, 600)
        self.mouse = mock.Mock()
        for name in ("click", "double_click", "right_click", "scroll"):
            getattr(self.mouse, name).return_value = True
        self.send_hotkey = mock.Mock(return_value=True)
        self.log = mock.Mock()
        self.stop_event = threading.Event()
        self.input_session = None

    def should_stop(self):
        return self.stop_event.is_set()

    def stop(self):
        self.stop_event.set()

    def task_cfg(self, name):
        return self.cfg["tasks"][name]


def _ob(dry_run=False):
    return {"auto_organize": True, "dry_run": dry_run,
            "loop": {"auto_check_interval_sec": 20, "passes": 3},
            "templates": {"bag_full_icon": "full", "confirm_button": "confirm"},
            "items": [{"name": "测试物品", "template": "item", "action": "use"}]}


def _organizer(ctx, dry_run=False):
    with mock.patch("mhxy.core.inventory.vision.load_template", return_value=object()):
        organizer = InventoryOrganizer(ctx, ctx.task_cfg("organize_bag"), dry_run)
    organizer._sleep = mock.Mock()
    return organizer


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.patches = [mock.patch("mhxy.core.inventory_maintenance.vision.load_template", return_value=object()),
                        mock.patch("mhxy.core.inventory_maintenance.win_mod.grab", return_value=object()),
                        mock.patch("mhxy.core.inventory_maintenance.vision.match", return_value=(1, 1, .99)),
                        mock.patch("mhxy.core.inventory_maintenance.InventoryOrganizer")]
        self.tpl, self.grab, self.match, self.organizer = [p.start() for p in self.patches]
        for p in self.patches:
            self.addCleanup(p.stop)
        self.organizer.return_value.organize.return_value = (1, "done")

    def test_context_has_no_inventory_policy_or_throttle(self):
        self.assertFalse(hasattr(TaskContext, "maybe_auto_organize"))

    def test_per_window_throttle_and_end_timestamp_without_config_mutation(self):
        ctx1, ctx2 = _Ctx(_ob()), _Ctx(_ob())
        before = copy.deepcopy(ctx1.cfg)
        maintenance = AutoInventoryMaintenance()
        with mock.patch("mhxy.core.inventory_maintenance.time.time", side_effect=[100, 120, 121, 121, 122]):
            self.assertTrue(maintenance.check(ctx1))
            self.assertTrue(maintenance.check(ctx1))
            self.assertTrue(maintenance.check(ctx2))
        self.assertEqual(self.organizer.call_count, 2)
        self.assertEqual(ctx1.cfg, before)
        self.assertEqual(maintenance._last_check[ctx1], 120)

    def test_disabled_missing_template_no_match_and_failed_capture_do_not_organize(self):
        for scenario in ("disabled", "no_template", "no_match", "no_frame"):
            with self.subTest(scenario=scenario):
                ctx = _Ctx(_ob())
                self.organizer.reset_mock()
                ctx.task_cfg("organize_bag")["auto_organize"] = scenario != "disabled"
                self.tpl.return_value = None if scenario == "no_template" else object()
                self.match.return_value = None if scenario == "no_match" else (1, 1, .99)
                self.grab.return_value = None if scenario == "no_frame" else object()
                self.assertTrue(AutoInventoryMaintenance().check(ctx))
                self.organizer.assert_not_called()

    def test_activation_failure_and_stop_prevent_organizer(self):
        ctx = _Ctx(_ob())
        ctx.window.activate.return_value = False
        self.assertFalse(AutoInventoryMaintenance().check(ctx))
        self.organizer.assert_not_called()
        ctx.stop()
        self.grab.reset_mock()
        self.assertFalse(AutoInventoryMaintenance().check(ctx))
        self.grab.assert_not_called()

    def test_policy_uses_organize_config_and_restores_parent_and_invalidates(self):
        ctx = _Ctx(_ob(True))
        ctx.cfg["tasks"]["escort"] = {"dry_run": False}
        session = ctx.input_session = InputSession(ctx.cfg, ctx.stop_event, "escort")
        session._observation = object()
        seen = []
        self.organizer.return_value.organize.side_effect = lambda: seen.append(session.dry_run())
        self.assertTrue(AutoInventoryMaintenance().check(ctx))
        self.assertEqual(seen, [True])
        self.assertFalse(session.dry_run())
        self.assertIsNone(session._observation)
        self.organizer.assert_called_once_with(ctx, ctx.task_cfg("organize_bag"), dry_run=True)

    def test_exception_stops_without_replay_or_parent_progress(self):
        ctx = _Ctx(_ob())
        self.organizer.return_value.organize.side_effect = RuntimeError("模拟异常")
        maintenance = AutoInventoryMaintenance()
        self.assertFalse(maintenance.check(ctx))
        self.assertTrue(ctx.should_stop())
        self.assertFalse(maintenance.check(ctx))
        self.assertEqual(self.organizer.return_value.organize.call_count, 1)

    def test_failed_reactivation_yields(self):
        ctx = _Ctx(_ob())
        ctx.window.activate.side_effect = [True, False]
        self.assertFalse(AutoInventoryMaintenance().check(ctx))

    def test_activity_hook_runs_once_before_continuous_steps(self):
        events = []
        ctx = _Context(_Window("one", events))
        rec = {"ctx": ctx, "state": "start", "done": False, "dead_logged": False}
        def step(r):
            events.append(("step", r["state"]))
            if r["state"] == "start":
                r["state"] = "wait"
            else:
                r["done"] = True
        with mock.patch.object(AutoInventoryMaintenance, "check", side_effect=lambda c: events.append(("maintenance", c.label))), \
             mock.patch.object(rotation, "_sleep"):
            rotation.run_rotation(Task()._make_rotation(ctx, [rec], step, False, 0, 0))
        self.assertEqual(events, [("activate", "one"), ("maintenance", "one"),
                                  ("step", "start"), ("step", "wait")])

    def test_unrecovered_hook_does_not_change_record(self):
        events = []
        ctx = _Context(_Window("one", events))
        rec = {"ctx": ctx, "state": "monitor", "done": False, "dead_logged": False}
        step = mock.Mock()
        def blocked(c):
            c.stop_event.set()
            return False
        with mock.patch.object(AutoInventoryMaintenance, "check", side_effect=blocked), \
             mock.patch.object(rotation, "_sleep"):
            rotation.run_rotation(Task()._make_rotation(ctx, [rec], step, False, 0, 0))
        step.assert_not_called()
        self.assertEqual(rec["state"], "monitor")
        self.assertFalse(rec["done"])

    def test_daily_keeps_independent_progress_and_barrier_without_maintenance_inside_it(self):
        events = []
        ctx = _Context(_Window("fast", events))
        ctx.windows = [ctx.window, _Window("slow", events)]
        with mock.patch.object(AutoInventoryMaintenance, "check", side_effect=lambda c: events.append(("maintenance", c.label))):
            _Daily(events).run(ctx)
        barrier = events.index(("dungeon",))
        self.assertEqual(events.count(("dungeon",)), 1)
        self.assertEqual(events[barrier - 1][0], "step")
        self.assertLess(events.index(("begin", "fast", "secret_realm")),
                        events.index(("begin", "slow", "escort")))
        for index, event in enumerate(events):
            if event[0] == "maintenance":
                self.assertEqual(events[index - 1], ("activate", event[1]))


class OrganizerTests(unittest.TestCase):
    def test_dry_run_is_current_frame_only_with_zero_input_even_without_session(self):
        ctx = _Ctx(_ob(True))
        organizer = _organizer(ctx, True)
        with mock.patch("mhxy.core.inventory.win_mod.grab", return_value=object()), \
             mock.patch("mhxy.core.inventory.vision.match", return_value=(10, 20, .99)), \
             mock.patch("mhxy.core.inventory.scan.scroll_search") as search:
            self.assertEqual(organizer.organize(), (1, "done"))
        search.assert_not_called()
        ctx.send_hotkey.assert_not_called()
        self.assertEqual(ctx.mouse.mock_calls, [])

    def test_default_is_dry_run(self):
        ctx = _Ctx(_ob())
        with mock.patch("mhxy.core.inventory.vision.load_template", return_value=None):
            self.assertTrue(InventoryOrganizer(ctx, ctx.task_cfg("organize_bag")).dry_run)

    def test_exception_closes_opened_bag_and_propagates_without_retry(self):
        ctx = _Ctx(_ob())
        organizer = _organizer(ctx)
        organizer._sweep_once = mock.Mock(side_effect=RuntimeError("模拟扫描异常"))
        with self.assertRaisesRegex(RuntimeError, "模拟扫描异常"):
            organizer.organize()
        self.assertEqual(ctx.send_hotkey.call_args_list, [mock.call("open_bag"), mock.call("close_panel")])
        organizer._sweep_once.assert_called_once()

    def test_stop_during_scan_does_not_sort_close_or_report_done(self):
        ctx = _Ctx(_ob())
        organizer = _organizer(ctx)
        organizer._sweep_once = mock.Mock(side_effect=lambda: (ctx.stop() or 0))
        organizer._click_sort_button = mock.Mock()
        self.assertEqual(organizer.organize(), (0, "stopped"))
        ctx.send_hotkey.assert_called_once_with("open_bag")
        organizer._click_sort_button.assert_not_called()

    def test_stop_at_final_close_is_stopped_not_recovery_error(self):
        ctx = _Ctx(_ob())
        organizer = _organizer(ctx)
        organizer._sweep_once = mock.Mock(return_value=0)
        def hotkey(action):
            if action == "close_panel":
                ctx.stop()
                return False
            return True
        ctx.send_hotkey.side_effect = hotkey
        self.assertEqual(organizer.organize(), (0, "stopped"))
        self.assertFalse(any("整理背包完成" in c.args[0] for c in ctx.log.call_args_list))

    def test_open_denied_does_not_scan_or_close(self):
        ctx = _Ctx(_ob())
        ctx.send_hotkey.return_value = False
        organizer = _organizer(ctx)
        organizer._sweep_once = mock.Mock()
        self.assertEqual(organizer.organize(), (0, "no_hotkey"))
        organizer._sweep_once.assert_not_called()
        ctx.send_hotkey.assert_called_once_with("open_bag")

    def test_confirm_missing_timeout_denied_and_stop_do_not_count_success(self):
        for failure in ("missing", "timeout", "denied", "stop", "success"):
            with self.subTest(failure=failure):
                ctx = _Ctx(_ob())
                organizer = _organizer(ctx)
                if failure == "missing":
                    organizer._btn["confirm_button"] = None
                def find(key, timeout):
                    if key == "confirm_button":
                        if failure == "stop":
                            ctx.stop()
                        if failure == "timeout":
                            return None
                    return (10, 20, .99)
                organizer._find_full_wait = mock.Mock(side_effect=find)
                ctx.mouse.click.side_effect = [True, failure != "denied"]
                self.assertEqual(organizer._do_action({"name": "测试"}, "discard", 10, 20, .99), failure == "success")
                confirm_logs = [c for c in ctx.log.call_args_list if "点「确定」确认" in c.args[0]]
                self.assertEqual(bool(confirm_logs), failure == "success")

    def test_confirm_denied_probe_is_not_handled_or_counted(self):
        ctx = _Ctx(_ob())
        ctx.task_cfg("organize_bag")["items"][0]["action"] = "discard"
        organizer = _organizer(ctx)
        organizer._find_full_wait = mock.Mock(return_value=(10, 20, .99))
        ctx.mouse.click.side_effect = [True, False]
        decisions = []
        def search(**kwargs):
            decisions.append(kwargs["probe"](object(), (0, 0, 800, 600))[0])
            return scan.ScanResult("exhausted", reached_end=True)
        with mock.patch("mhxy.core.inventory.vision.match", return_value=(10, 20, .99)), \
             mock.patch("mhxy.core.inventory.scan.scroll_search", side_effect=search):
            self.assertEqual(organizer._sweep_once(), 0)
        self.assertEqual(decisions, [scan.SCROLL])

    def test_action_exception_cleans_once(self):
        ctx = _Ctx(_ob())
        organizer = _organizer(ctx)
        organizer._find_full_wait = mock.Mock(side_effect=RuntimeError("模拟匹配异常"))
        with self.assertRaises(RuntimeError):
            organizer._do_action({"name": "测试"}, "discard", 10, 20, .99)
        ctx.send_hotkey.assert_called_once_with("close_panel")

    def test_cleanup_denial_blocks_action_and_bag_completion(self):
        ctx = _Ctx(_ob())
        organizer = _organizer(ctx)
        organizer._find_full_wait = mock.Mock(return_value=(10, 20, .99))
        ctx.send_hotkey.return_value = False
        with self.assertRaises(InventoryRecoveryError):
            organizer._do_action({"name": "测试"}, "discard", 10, 20, .99)
        ctx.send_hotkey.side_effect = [True, False]
        organizer._sweep_once = mock.Mock(return_value=0)
        with self.assertRaises(InventoryRecoveryError):
            organizer.organize()

    def test_scan_failure_is_not_organizer_done(self):
        ctx = _Ctx(_ob())
        organizer = _organizer(ctx)
        with mock.patch("mhxy.core.inventory.scan.scroll_search", return_value=scan.ScanResult("stopped")):
            with self.assertRaises(InventoryRecoveryError):
                organizer.organize()

    def test_denied_sort_click_has_no_success_log(self):
        ctx = _Ctx(_ob())
        organizer = _organizer(ctx)
        organizer._btn["sort_button"] = object()
        organizer._find_full_wait = mock.Mock(return_value=(10, 20, .99))
        ctx.mouse.click.return_value = False
        organizer._click_sort_button()
        ctx.log.assert_not_called()


class ScanAndObservationTests(unittest.TestCase):
    def test_denied_scroll_and_failed_frame_are_stopped_without_false_end(self):
        for reset in (False, True):
            for frame in (None, object()):
                with self.subTest(reset=reset, frame=frame):
                    mouse = mock.Mock()
                    mouse.scroll.return_value = False
                    probe = mock.Mock(return_value=(scan.SCROLL, None))
                    with mock.patch("mhxy.core.scan.win_mod.grab", return_value=frame):
                        result = scan.scroll_search(grab_rect=lambda: (0, 0, 100, 100),
                            probe=probe, mouse=mouse,
                            should_stop=lambda: False, sleep=lambda s: None, reset_to_top=reset)
                    self.assertTrue(result.stopped)
                    self.assertFalse(result.reached_end)
                    if reset or frame is None:
                        probe.assert_not_called()

    def test_external_focus_change_requires_new_observation_but_noop_activation_keeps_it(self):
        session = InputSession({"tasks": {"demo": {"dry_run": False}}}, threading.Event(), "demo")
        window = GameWindow("假窗口")
        window._win = mock.Mock(_hWnd=10)
        window.rect = mock.Mock(return_value=(0, 0, 100, 100))
        window._activate = mock.Mock(return_value=True)
        session._foreground = mock.Mock(return_value=True)
        owner = mock.Mock(window=window)
        with session.activate():
            session.activated(window, True)
            session.observed(session.capture_token(), (0, 0, 100, 100), True)
            window.activate()
            self.assertTrue(session.allowed(owner, "click", (10, 10), {}))
            session._foreground.side_effect = [False, True]
            window.activate()
            session._foreground.side_effect = None
            self.assertFalse(session.allowed(owner, "click", (10, 10), {}))
            session.observed(session.capture_token(), (0, 0, 100, 100), True)
            self.assertTrue(session.allowed(owner, "click", (10, 10), {}))


if __name__ == "__main__":
    unittest.main()
