# -*- coding: utf-8 -*-
"""架构迁移前的行为基线：假窗口和假业务，不截图、不向游戏输入。"""
import threading
import unittest
from unittest import mock

from mhxy.core import rotation
from mhxy.core.runner import TaskRunner
from mhxy.tasks.daily import DailyTask


class _Window:
    def __init__(self, label, events, activates=True, present=True):
        self.label = label
        self.events = events
        self.activates = activates
        self.present = present

    def rect(self):
        return (0, 0, 800, 600) if self.present else None

    def activate(self):
        self.events.append(("activate", self.label))
        return self.activates


class _Context:
    def __init__(self, window, stop_event=None):
        self.window = window
        self.label = window.label
        self.stop_event = stop_event or threading.Event()
        self.cfg = {"targets": {"multi": True, "switch_delay_sec": 0}}
        self.windows = [window]

    def should_stop(self):
        return self.stop_event.is_set()

    def log(self, *args, **kwargs):
        pass

    def select_windows(self):
        return self.windows

    def window_labels(self, windows):
        return [w.label for w in windows]

    def make_child(self, window, label):
        return _Context(window, self.stop_event)

    def task_cfg(self, name):
        return {"auto_organize": False}


class RotationBaselineTests(unittest.TestCase):
    def test_one_two_and_three_windows_progress_to_wait_before_switching(self):
        for count in (1, 2, 3):
            with self.subTest(count=count):
                events = []
                stop = threading.Event()
                records = [{"ctx": _Context(_Window(str(i), events), stop),
                            "state": "ready", "done": False} for i in range(count)]

                def step(rec):
                    events.append(("step", rec["ctx"].label, rec["state"]))
                    if rec["state"] == "ready":
                        rec["state"] = "wait"
                    elif all(r["state"] == "wait" for r in records):
                        rec["done"] = True

                c = rotation.RotationConfig(records, step, stop.is_set, lambda *a, **k: None,
                                            multi=count > 1, tick=0)
                with mock.patch.object(rotation, "_sleep"):
                    self.assertEqual(rotation.run_rotation(c), "ok")
                self.assertTrue(all(r["done"] for r in records))
                expected = []
                for i in range(count):
                    expected.extend([("activate", str(i)), ("step", str(i), "ready"),
                                     ("step", str(i), "wait")])
                self.assertEqual(events[:len(expected)], expected)

    def test_foreground_failure_and_missing_window_skip_all_actions(self):
        events = []
        stop = threading.Event()
        records = [{"ctx": _Context(w, stop), "state": "ready", "done": False}
                   for w in (_Window("blocked", events, activates=False),
                             _Window("gone", events, present=False),
                             _Window("healthy", events))]

        def step(rec):
            events.append(("step", rec["ctx"].label))
            stop.set()

        c = rotation.RotationConfig(records, step, stop.is_set, lambda *a, **k: None,
                                    between_steps=lambda r: events.append(("maintenance", r["ctx"].label)))
        with mock.patch.object(rotation, "_sleep"):
            self.assertEqual(rotation.run_rotation(c), "stopped")
        self.assertEqual(events, [("activate", "blocked"), ("activate", "healthy"),
                                  ("maintenance", "healthy"), ("step", "healthy")])

    def test_stop_after_activation_prevents_maintenance_and_step(self):
        events = []
        stop = threading.Event()
        w = _Window("one", events)
        rec = {"ctx": _Context(w, stop), "state": "ready", "done": False}
        step, maintenance = mock.Mock(), mock.Mock()
        c = rotation.RotationConfig([rec], step, stop.is_set, lambda *a, **k: None,
                                    between_steps=maintenance)
        with mock.patch.object(w, "activate", side_effect=lambda: (stop.set() or True)), \
             mock.patch.object(rotation, "_sleep"):
            self.assertEqual(rotation.run_rotation(c), "stopped")
        step.assert_not_called()
        maintenance.assert_not_called()

    def test_continuous_progress_has_a_fairness_cap(self):
        rec = {"state": 0, "done": False}
        c = rotation.RotationConfig([rec], lambda r: r.update(state=r["state"] + 1),
                                    lambda: False, lambda *a, **k: None, max_consec_steps=3)
        self.assertEqual(rotation._drive_until_yield(c, rec), rotation.YIELD_CAP)
        self.assertEqual(rec["state"], 3)


class _Daily(DailyTask):
    def __init__(self, events):
        self.events = events
        self.waits = {"fast": 0, "slow": 2}

    def _enabled_steps(self, ctx):
        return ["escort", "secret_realm", "dungeon", "treasure_map"]

    def _time_limit(self, ctx):
        return 0

    def _title_of(self, name, ctx):
        return name

    def _interruptible_sleep(self, ctx, seconds):
        pass

    def _begin_step(self, ctx, c, name):
        label = c["wctx"].label
        self.events.append(("begin", label, name))
        rec = {"state": "wait", "done": False}

        def step():
            self.events.append(("step", label, name))
            if name == "escort" and self.waits[label]:
                self.waits[label] -= 1
            else:
                rec["done"] = True
                rec["outcome"] = "completed"

        c["sub_rec"], c["sub_step"], c["cur_title"] = rec, step, name
        c["sub_name"] = name
        return "ready"

    def _run_collective_dungeon(self, ctx):
        self.events.append(("dungeon",))
        return "completed"


class DailyBaselineTests(unittest.TestCase):
    def test_independent_windows_wait_at_barrier_then_continue_once(self):
        events = []
        windows = [_Window("fast", events), _Window("slow", events)]
        ctx = _Context(windows[0])
        ctx.windows = windows
        _Daily(events).run(ctx)
        barrier = events.index(("dungeon",))
        self.assertEqual(events.count(("dungeon",)), 1)
        self.assertLess(events.index(("begin", "fast", "escort")), barrier)
        self.assertEqual(events[:barrier].count(("step", "fast", "escort")), 1)
        self.assertEqual(events[:barrier].count(("step", "slow", "escort")), 3)
        self.assertLess(events.index(("begin", "fast", "secret_realm")),
                        events.index(("begin", "slow", "escort")))
        for label in ("fast", "slow"):
            self.assertGreater(events.index(("begin", label, "treasure_map")), barrier)
            self.assertEqual(events.count(("begin", label, "treasure_map")), 1)

    def test_single_window_uses_same_barrier_and_chain(self):
        events = []
        ctx = _Context(_Window("fast", events))
        ctx.cfg["targets"]["multi"] = False
        _Daily(events).run(ctx)
        business = [e for e in events if e[0] in ("begin", "dungeon")]
        self.assertEqual(business, [("begin", "fast", "escort"),
                                    ("begin", "fast", "secret_realm"), ("dungeon",),
                                    ("begin", "fast", "treasure_map")])

    def test_stop_with_one_window_parked_does_not_run_dungeon(self):
        events = []
        windows = [_Window("fast", events), _Window("slow", events)]
        ctx = _Context(windows[0])
        ctx.windows = windows
        task = _Daily(events)
        original = task._begin_step

        def begin(main, chain, name):
            if chain["wctx"].label == "slow":
                main.stop_event.set()
            return original(main, chain, name)

        with mock.patch.object(task, "_begin_step", side_effect=begin):
            task.run(ctx)
        self.assertNotIn(("dungeon",), events)
        self.assertFalse(any(e[0] == "begin" and e[2] == "treasure_map" for e in events))


class RunnerBaselineTests(unittest.TestCase):
    def test_stopped_runner_can_restart_with_cleared_stop_event(self):
        entered = threading.Event()

        class Task:
            def preflight(self, ctx):
                return True, []

            def run(self, ctx):
                entered.set()
                ctx.stop_event.wait(2)

        runner = TaskRunner(Task(), {})
        for _ in range(2):
            entered.clear()
            with mock.patch("mhxy.core.runner.TaskContext") as context:
                context.return_value.stop_event = runner.stop_event
                self.assertTrue(runner.start()[0])
                try:
                    self.assertTrue(entered.wait(1))
                    self.assertFalse(runner.stop_event.is_set())
                finally:
                    runner.stop()
                    runner.thread.join(2)
            self.assertFalse(runner.is_running())
            self.assertEqual(runner.outcome, "stopped")


if __name__ == "__main__":
    unittest.main()
