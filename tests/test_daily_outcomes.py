# -*- coding: utf-8 -*-
"""daily 结果门禁：真实编排、假窗口/业务/时钟，资产出口隔离。"""
import copy
import tempfile
import threading
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

from mhxy.core import config, vision, window
from mhxy.tasks import base, daily


class Window:
    title_substr = "fake"

    def __init__(self, label, events, activates=None, present=None):
        self.label, self.events = label, events
        self.activates = list(activates or [True])
        self.present = list(present or [True])

    @staticmethod
    def next_value(values):
        return values.pop(0) if len(values) > 1 else values[0]

    def rect(self):
        return (0, 0, 800, 600) if self.next_value(self.present) else None

    def activate(self):
        ok = self.next_value(self.activates)
        self.events.append(("activate", self.label, ok))
        return ok


class Context:
    def __init__(self, wins, cfg, events, stop=None, log=None):
        self.windows, self.cfg, self.events = wins, cfg, events
        self.window = wins[0] if wins else Window("missing", events)
        self.label = self.window.label
        self.stop = stop if stop is not None else threading.Event()
        self.log = log if log is not None else mock.Mock()
        self.mouse = mock.Mock()

    def select_windows(self):
        return self.windows

    def window_labels(self, wins):
        return [w.label for w in wins]

    def make_child(self, win, label):
        return Context([win], self.cfg, self.events, self.stop, self.log)

    def task_cfg(self, name):
        return self.cfg["tasks"].get(name, {})

    def should_stop(self):
        return self.stop.is_set()


class Clock:
    def __init__(self):
        self.now = 100.0

    def time(self):
        return self.now

    def sleep(self, ctx, seconds):
        self.now += 1


class DailyOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix="daily_outcomes_")))
        for name, path in (("CONFIG_PATH", root / "config.json"),
                           ("CONFIG_BACKUP_DIR", root / "backups"),
                           ("TEMPLATES_DIR", root / "templates"),
                           ("CAPTURES_DIR", root / "captures")):
            self.stack.enter_context(mock.patch.object(config, name, path))
        self.stack.enter_context(mock.patch.object(base, "CAPTURES_DIR", root / "captures"))
        self.grab = self.stack.enter_context(mock.patch.object(window, "grab", side_effect=AssertionError("unexpected screenshot")))
        self.load = self.stack.enter_context(mock.patch.object(vision, "load_template", side_effect=AssertionError("unexpected template")))
        self.save = self.stack.enter_context(mock.patch.object(vision, "save_image", side_effect=AssertionError("unexpected asset write")))
        self.maintenance = self.stack.enter_context(mock.patch("mhxy.core.inventory_maintenance.AutoInventoryMaintenance.check", return_value=True))
        self.solo = self.stack.enter_context(mock.patch.object(daily.DailyTask, "_ensure_solo", return_value=True))
        self.clock = Clock()
        self.stack.enter_context(mock.patch.object(daily.time, "time", self.clock.time))
        self.events = []
        self.tasks = {}
        self.plans = {}
        self.stack.enter_context(mock.patch.object(daily, "get_task", side_effect=lambda name: self.tasks.get(name)))
        self.stack.enter_context(mock.patch.object(daily, "dungeon_tasks", side_effect=lambda: [self.tasks["fake_dungeon"]] if "fake_dungeon" in self.tasks else []))
        self.add_activity("escort")
        self.add_activity("treasure_map")
        self.add_activity("secret_realm")
        self.add_dungeon()

    def add_activity(self, name, preflight=True, prepare_error=False, construct_error=False):
        owner = self
        counter = {"escort": "escorts", "treasure_map": "dug", "secret_realm": "runs"}[name]

        class Activity:
            CHAINS_PER_WINDOW = True
            title = name

            def __init__(self):
                if construct_error:
                    raise RuntimeError("activity construction failed")

            def preflight(self, ctx):
                owner.events.append(("preflight", name))
                if preflight == "error":
                    raise RuntimeError("preflight failed")
                return preflight, [] if preflight else ["missing calibration"]

            def make_chain_driver(self, wctx):
                owner.events.append(("begin", wctx.label, name))
                if prepare_error:
                    raise RuntimeError("driver failed")
                rec = {"state": "wait", "done": False, counter: 0}
                plan = owner.plans.get((wctx.label, name), {"outcome": "completed"})
                waits = plan.get("waits", 0)

                def step():
                    nonlocal waits
                    owner.events.append(("step", wctx.label, name))
                    rec[counter] = plan.get("count", 0)
                    if plan.get("stop"):
                        wctx.stop.set()
                    if plan.get("error"):
                        raise RuntimeError("step failed")
                    if waits or plan.get("forever"):
                        waits -= 1
                        return
                    rec["done"] = True
                    if plan.get("outcome") is not None:
                        rec["outcome"] = plan["outcome"]
                return rec, step

        self.tasks[name] = Activity

    def add_dungeon(self, outcome="completed", preflight=True, error=False, stop=False, construct_error=False):
        owner = self

        class Dungeon:
            name = "fake_dungeon"
            title = "fake dungeon"
            is_dungeon = True

            def __init__(self):
                if construct_error:
                    raise RuntimeError("dungeon construction failed")

            def preflight(self, ctx):
                owner.events.append(("preflight", "dungeon"))
                if preflight == "error":
                    raise RuntimeError("dungeon preflight failed")
                return preflight, [] if preflight else ["missing calibration"]

            def run(self, ctx):
                owner.events.append(("dungeon",))
                if stop:
                    ctx.stop.set()
                if error:
                    raise RuntimeError("dungeon failed")
                return outcome

        self.tasks["fake_dungeon"] = Dungeon

    def context(self, steps, labels=("one",), limit=0):
        cfg = {"targets": {"multi": len(labels) > 1, "switch_delay_sec": 0},
               "humanize": {"interval_jitter": 0}, "calib_profiles": {"sentinel": [1, 2]},
               "tasks": {name: {"dry_run": False, "regions": {"sentinel": [1, 2, 3, 4]}}
                         for name in self.tasks}}
        cfg["tasks"]["daily"] = {"steps": [{"task": n, "enabled": True} for n in steps],
                                    "loop": {"time_limit_min": limit}}
        cfg["tasks"]["dungeon"] = {"selected": "fake_dungeon"}
        return Context([Window(label, self.events) for label in labels], cfg, self.events)

    def run_daily(self, ctx):
        task = daily.DailyTask()
        task._interruptible_sleep = self.clock.sleep
        before = copy.deepcopy(ctx.cfg)
        result = task.run(ctx)
        self.assertEqual(ctx.cfg, before)
        self.grab.assert_not_called()
        self.load.assert_not_called()
        self.save.assert_not_called()
        ctx.mouse.assert_not_called()
        return result

    def messages(self, ctx):
        return [call.args[0] for call in ctx.log.call_args_list]

    def test_different_speed_windows_keep_independent_chain_and_one_barrier(self):
        ctx = self.context(["escort", "secret_realm", "dungeon", "treasure_map"], ("fast", "slow"))
        self.plans[("slow", "escort")] = {"waits": 2, "outcome": "completed"}
        self.assertEqual(self.run_daily(ctx), "completed")
        barrier = self.events.index(("dungeon",))
        self.assertEqual(self.events.count(("dungeon",)), 1)
        self.assertLess(self.events.index(("begin", "fast", "secret_realm")),
                        self.events.index(("begin", "slow", "escort")))
        self.assertEqual(self.events[:barrier].count(("step", "slow", "escort")), 3)
        for label in ("fast", "slow"):
            self.assertGreater(self.events.index(("begin", label, "treasure_map")), barrier)
            self.assertEqual(self.events.count(("begin", label, "treasure_map")), 1)

    def test_solo_check_precedes_first_activity_and_repeats_after_dungeon(self):
        ctx = self.context(["secret_realm", "dungeon", "treasure_map", "escort"], ("a", "b"))
        self.solo.side_effect = lambda wctx: self.events.append(("solo", wctx.label)) or True
        self.assertEqual(self.run_daily(ctx), "completed")
        barrier = self.events.index(("dungeon",))
        for label in ("a", "b"):
            checks = [i for i, event in enumerate(self.events) if event == ("solo", label)]
            self.assertEqual(len(checks), 2)
            self.assertLess(checks[0], self.events.index(("begin", label, "secret_realm")))
            self.assertGreater(checks[1], barrier)
            self.assertLess(checks[1], self.events.index(("begin", label, "treasure_map")))

    def test_failed_quit_blocks_affected_window_but_other_window_continues(self):
        ctx = self.context(["dungeon", "treasure_map", "escort"], ("a", "b"))
        self.solo.side_effect = lambda wctx: wctx.label == "b"
        self.assertEqual(self.run_daily(ctx), "partial")
        for name in ("treasure_map", "escort"):
            self.assertNotIn(("begin", "a", name), self.events)
            self.assertIn(("begin", "b", name), self.events)

    def test_done_without_success_evidence_is_failed(self):
        for evidence in (None, "failed", "timeout"):
            with self.subTest(evidence=evidence):
                ctx = self.context(["escort"])
                self.plans[("one", "escort")] = {"outcome": evidence}
                self.assertEqual(self.run_daily(ctx), "timeout" if evidence == "timeout" else "failed")
                self.assertNotIn("───── 「escort」完成 ─────", self.messages(ctx))
                self.assertTrue(any("0/1 个号全部任务完成" in m for m in self.messages(ctx)))

    def test_activity_legacy_progress_and_mixed_windows_report_partial(self):
        for name in ("escort", "treasure_map", "secret_realm"):
            with self.subTest(name=name):
                ctx = self.context([name])
                self.plans[("one", name)] = {"outcome": "timeout", "count": 1}
                self.assertEqual(self.run_daily(ctx), "partial")
        ctx = self.context(["escort"], ("fast", "slow"))
        self.plans[("slow", "escort")] = {"outcome": "failed"}
        self.assertEqual(self.run_daily(ctx), "partial")
        self.assertTrue(any("1/2 个号全部任务完成" in m for m in self.messages(ctx)))

    def test_dungeon_five_states_and_unknown_are_consumed_without_repeat(self):
        for outcome in ("completed", "partial", "failed", "timeout", "stopped", None, "unknown"):
            with self.subTest(outcome=outcome):
                self.events.clear()
                self.add_dungeon(outcome)
                ctx = self.context(["dungeon"], ("a", "b"))
                expected = outcome if outcome in {"completed", "partial", "failed", "timeout", "stopped"} else "failed"
                self.assertEqual(self.run_daily(ctx), expected)
                self.assertEqual(self.events.count(("dungeon",)), 1)
                if expected != "completed":
                    self.assertFalse(any("「刷副本 · fake dungeon」完成 ─────" in m for m in self.messages(ctx)))

    def test_failed_dungeon_releases_barrier_and_runs_later_steps_once(self):
        self.add_dungeon("failed")
        ctx = self.context(["dungeon", "escort", "dungeon"], ("a", "b"))
        self.assertEqual(self.run_daily(ctx), "partial")
        self.assertEqual(self.events.count(("dungeon",)), 1)  # 重复配置仍由原 enabled_steps 去重
        for label in ("a", "b"):
            self.assertEqual(self.events.count(("begin", label, "escort")), 1)

    def test_dry_run_and_preflight_skips_are_not_completed_and_continue(self):
        for name in ("escort", "dungeon"):
            for skip in ("dry", "preflight"):
                with self.subTest(name=name, skip=skip):
                    self.events.clear()
                    self.add_activity("escort", preflight=skip != "preflight")
                    self.add_dungeon(preflight=skip != "preflight")
                    ctx = self.context([name, "treasure_map"])
                    if skip == "dry":
                        ctx.cfg["tasks"]["fake_dungeon" if name == "dungeon" else name]["dry_run"] = True
                    self.assertEqual(self.run_daily(ctx), "partial")
                    self.assertNotIn(("begin", "one", "escort"), self.events)
                    self.assertNotIn(("dungeon",), self.events)
                    self.assertEqual(self.events.count(("begin", "one", "treasure_map")), 1)

    def test_all_skipped_and_unavailable_tasks_are_failed(self):
        for name in ("escort", "dungeon"):
            with self.subTest(name=name):
                ctx = self.context([name])
                ctx.cfg["tasks"]["fake_dungeon" if name == "dungeon" else name]["dry_run"] = True
                self.assertEqual(self.run_daily(ctx), "failed")
        self.tasks.pop("escort")
        self.assertEqual(self.run_daily(self.context(["escort"])), "failed")
        self.tasks.pop("fake_dungeon")
        self.assertEqual(self.run_daily(self.context(["dungeon"])), "failed")

    def test_activity_exceptions_preserve_progress_continue_and_do_not_retry(self):
        for count in (0, 1):
            with self.subTest(count=count):
                self.events.clear()
                self.plans[("one", "escort")] = {"error": True, "count": count}
                self.assertEqual(self.run_daily(self.context(["escort"])), "partial" if count else "failed")
                self.assertEqual(self.events.count(("begin", "one", "escort")), 1)
                self.assertEqual(self.events.count(("step", "one", "escort")), 1)
        ctx = self.context(["escort", "treasure_map"])
        self.assertEqual(self.run_daily(ctx), "partial")
        self.assertEqual(self.events.count(("begin", "one", "treasure_map")), 1)

    def test_prepare_exceptions_propagate_and_runner_fails_without_later_steps(self):
        from mhxy.core.runner import TaskRunner
        for prepare in ("construct", "preflight", "driver", "dungeon_construct", "dungeon_preflight"):
            with self.subTest(prepare=prepare):
                self.events.clear()
                self.add_activity("escort", preflight="error" if prepare == "preflight" else True,
                                  prepare_error=prepare == "driver", construct_error=prepare == "construct")
                self.add_dungeon(preflight="error" if prepare == "dungeon_preflight" else True,
                                 construct_error=prepare == "dungeon_construct")
                name = "dungeon" if prepare.startswith("dungeon") else "escort"
                ctx = self.context([name, "treasure_map"])
                before = copy.deepcopy(ctx.cfg)
                with self.assertRaises(RuntimeError):
                    self.run_daily(ctx)
                self.assertEqual(ctx.cfg, before)
                task = daily.DailyTask()
                task._interruptible_sleep = self.clock.sleep
                runner = TaskRunner(task, ctx.cfg)
                with mock.patch("mhxy.core.runner.TaskContext", return_value=ctx):
                    self.assertTrue(runner._start_registered()[0])
                    runner.thread.join(2)
                self.assertFalse(runner.thread.is_alive())
                self.assertEqual(runner.outcome, "failed")
                self.assertNotIn(("dungeon",), self.events)
                self.assertNotIn(("begin", "one", "treasure_map"), self.events)
                self.grab.assert_not_called()
                self.load.assert_not_called()
                self.save.assert_not_called()

    def test_dungeon_run_exception_keeps_original_continue_and_no_retry(self):
        self.add_dungeon(error=True)
        ctx = self.context(["dungeon", "treasure_map"], ("a", "b"))
        self.assertEqual(self.run_daily(ctx), "partial")
        self.assertEqual(self.events.count(("dungeon",)), 1)
        for label in ("a", "b"):
            self.assertEqual(self.events.count(("begin", label, "treasure_map")), 1)

    def test_overall_timeout_without_progress_and_with_active_progress(self):
        for count in (0, 1):
            with self.subTest(count=count):
                ctx = self.context(["escort"], limit=.02)
                self.plans[("one", "escort")] = {"forever": True, "count": count}
                self.assertEqual(self.run_daily(ctx), "partial" if count else "timeout")

    def test_stop_before_run_and_during_activity_has_priority(self):
        ctx = self.context(["escort"])
        ctx.stop.set()
        self.assertEqual(self.run_daily(ctx), "stopped")
        self.assertNotIn(("begin", "one", "escort"), self.events)
        ctx = self.context(["escort", "treasure_map"])
        self.plans[("one", "escort")] = {"outcome": "completed", "stop": True}
        self.assertEqual(self.run_daily(ctx), "stopped")
        self.assertNotIn(("begin", "one", "treasure_map"), self.events)

    def test_stop_with_one_window_parked_does_not_execute_dungeon(self):
        ctx = self.context(["escort", "dungeon"], ("fast", "slow"))
        self.plans[("slow", "escort")] = {"stop": True, "outcome": "completed"}
        self.assertEqual(self.run_daily(ctx), "stopped")
        self.assertNotIn(("dungeon",), self.events)

    def test_stop_during_dungeon_prevents_later_steps(self):
        self.add_dungeon("completed", stop=True)
        ctx = self.context(["dungeon", "escort"])
        self.assertEqual(self.run_daily(ctx), "stopped")
        self.assertEqual(self.events.count(("dungeon",)), 1)
        self.assertNotIn(("begin", "one", "escort"), self.events)

    def test_missing_window_and_foreground_failure_retry_next_round_without_actions(self):
        for missing in (False, True):
            with self.subTest(missing=missing):
                self.events.clear()
                ctx = self.context(["escort"])
                win = ctx.window
                win.present = [False, True] if missing else [True]
                win.activates = [True] if missing else [False, True]
                self.assertEqual(self.run_daily(ctx), "completed")
                self.assertEqual(self.events.count(("begin", "one", "escort")), 1)
                self.assertEqual(self.events.count(("step", "one", "escort")), 1)
                if not missing:
                    self.assertLess(self.events.index(("activate", "one", False)),
                                    self.events.index(("activate", "one", True)))

    def test_persistent_foreground_failure_times_out_without_driver_or_dungeon(self):
        ctx = self.context(["escort", "dungeon"], limit=.02)
        ctx.window.activates = [False]
        self.assertEqual(self.run_daily(ctx), "timeout")
        self.assertFalse(any(e[0] in ("begin", "step", "dungeon") for e in self.events))

    def test_stop_after_activation_prevents_maintenance_and_driver(self):
        ctx = self.context(["escort"])
        ctx.window.activate = lambda: (ctx.stop.set() or True)
        self.assertEqual(self.run_daily(ctx), "stopped")
        self.maintenance.assert_not_called()
        self.assertNotIn(("begin", "one", "escort"), self.events)

    def test_no_steps_or_windows_fail_with_stop_priority(self):
        self.assertEqual(self.run_daily(self.context([])), "failed")
        ctx = self.context(["escort"], ())
        self.assertEqual(self.run_daily(ctx), "failed")
        ctx.stop.set()
        self.assertEqual(self.run_daily(ctx), "stopped")


if __name__ == "__main__":
    unittest.main()
