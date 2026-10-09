# -*- coding: utf-8 -*-
"""第四阶段一键组队结果门禁：假窗口/时钟/输入，不读取真实模板。"""
import copy
import threading
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest import mock

from mhxy.core import rotation, teaming
from mhxy.core.runner import TaskRunner
from mhxy.core.runtime import RuntimeService
from mhxy.tasks.dungeon import DungeonTask


class _Window:
    def __init__(self, present=True, activates=True):
        self.present = present
        self.activates = activates
        self.calls = 0

    def rect(self):
        return (0, 0, 800, 600) if self.present else None

    def activate(self):
        self.calls += 1
        return self.activates


class _Context:
    def __init__(self, windows, cfg=None, stop=None):
        self.windows = windows
        self.window = windows[0] if windows else None
        self.cfg = cfg if cfg is not None else {
            "targets": {"multi": True},
            "tasks": {"teaming": {"dry_run": False, "loop": {"form_timeout_sec": 2}},
                      "escort": {"regions": {"entry": [1, 2, 3, 4]}}}}
        self.stop = stop if stop is not None else threading.Event()
        self.log = mock.Mock()
        self.mouse = mock.Mock()
        self.send_hotkey = mock.Mock()

    def should_stop(self):
        return self.stop.is_set()

    def select_windows(self):
        return self.windows

    def window_labels(self, windows):
        return ["窗口%d" % i for i in range(len(windows))]

    def make_child(self, window, label):
        return _Context([window], self.cfg, self.stop)

    def task_cfg(self, name):
        return self.cfg["tasks"][name]


class _Clock:
    def __init__(self):
        self.now = 100.0

    def time(self):
        return self.now

    def sleep(self, *args):
        self.now += 1.0


class RotationTerminationTests(unittest.TestCase):
    def test_reuse_keeps_old_returns_and_resets_evidence(self):
        stop = threading.Event()
        ctx = _Context([_Window()], stop=stop)
        rec = {"ctx": ctx, "state": "wait", "done": False}
        clock = _Clock()
        rc = rotation.RotationConfig([rec], lambda r: None, stop.is_set,
                                     ctx.log, overall_timeout=1)
        with mock.patch.object(rotation.time, "time", clock.time), \
                mock.patch.object(rotation, "_sleep", clock.sleep):
            self.assertEqual(rotation.run_rotation(rc), "ok")
            self.assertEqual(rc.termination_reason, "timeout")
            rec["done"] = True
            self.assertEqual(rotation.run_rotation(rc), "ok")
            self.assertEqual(rc.termination_reason, "completed")
            stop.set()
            self.assertEqual(rotation.run_rotation(rc), "stopped")
            self.assertEqual(rc.termination_reason, "stopped")
            stop.clear()
            rec["done"] = False
            ctx.window.present = False
            rc.on_window_gone = lambda r: "abort"
            self.assertEqual(rotation.run_rotation(rc), "aborted")
            self.assertEqual(rc.termination_reason, "aborted")

    def test_stop_during_timeout_log_has_priority(self):
        stop = threading.Event()
        clock = _Clock()
        rc = rotation.RotationConfig([], lambda r: None, stop.is_set,
                                     lambda *a, **k: stop.set(), overall_timeout=1)
        with mock.patch.object(rotation.time, "time", side_effect=[0, 2]):
            self.assertEqual(rotation.run_rotation(rc), "stopped")
        self.assertEqual(rc.termination_reason, "stopped")


class FormationOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.object(teaming.TeamFormation, "_load_templates", return_value={}))
        self.clock = _Clock()
        self.stack.enter_context(mock.patch.object(rotation.time, "time", self.clock.time))
        self.stack.enter_context(mock.patch.object(rotation, "_sleep", self.clock.sleep))
        self.stack.enter_context(mock.patch.object(teaming.win_mod, "grab", return_value=None))

    def make_team(self, count=3, dry_run=False):
        ctx = _Context([_Window() for _ in range(count)])
        roles = [teaming.TeamFormation.ROLE_CAPTAIN] + [teaming.TeamFormation.ROLE_MEMBER] * (count - 1)
        assignments = [(ctx.make_child(w, "窗口"), role) for w, role in zip(ctx.windows, roles)]
        team = teaming.TeamFormation(ctx, assignments, ctx.task_cfg("teaming"), dry_run=dry_run)
        team._sleep = lambda seconds: None
        return ctx, team

    def test_all_done_is_not_business_success(self):
        for joined, expected in [(0, "failed"), (1, "partial"), (2, "completed")]:
            with self.subTest(joined=joined):
                ctx, team = self.make_team()
                team.joined = joined
                for rec in team.records:
                    rec["done"] = True
                self.assertEqual(team.run_until_formed(),
                                 (True, "formed") if joined == 2 else (False, "not_enough"))
                self.assertEqual(team.formation_outcome, expected)

    def test_timeout_distinguishes_zero_partial_and_legacy_success(self):
        for joined, expected in [(0, "timeout"), (1, "partial"), (2, "completed")]:
            with self.subTest(joined=joined):
                ctx, team = self.make_team()
                team.joined = joined
                team._step_once = mock.Mock()  # 等待不改 state；两个窗口均会让出
                result = team.run_until_formed()
                self.assertEqual(result, (True, "formed") if joined == 2 else (False, "not_enough"))
                self.assertEqual(team.formation_outcome, expected)
                self.assertTrue(all(w.calls > 0 for w in ctx.windows))

    def test_stop_overrides_existing_accept_count_without_cleanup(self):
        ctx, team = self.make_team()
        team.joined = team.member_count
        ctx.stop.set()
        team._close_panel = mock.Mock()
        self.assertEqual(team.run_until_formed(), (False, "stopped"))
        self.assertEqual(team.formation_outcome, "stopped")
        team._close_panel.assert_not_called()
        self.assertTrue(all(w.calls == 0 for w in ctx.windows))

    def test_captain_disappears_aborts_without_member_actions(self):
        ctx, team = self.make_team()
        ctx.windows[0].present = False
        team.joined = 1
        team._step_once = mock.Mock()
        self.assertEqual(team.run_until_formed(), (False, "captain_window_gone"))
        self.assertEqual(team.formation_outcome, "failed")
        team._step_once.assert_not_called()

    def test_missing_member_is_skipped_and_other_window_continues(self):
        ctx, team = self.make_team()
        ctx.windows[1].present = False
        def step(rec):
            rec["done"] = True
            if rec["role"] == team.ROLE_CAPTAIN:
                team.joined = 1
        team._step_once = mock.Mock(side_effect=step)
        self.assertEqual(team.run_until_formed(), (False, "not_enough"))
        self.assertEqual(team.formation_outcome, "partial")
        self.assertTrue(team.records[1]["done"])
        self.assertFalse(team.records[1]["ok"])
        self.assertEqual(ctx.windows[1].calls, 0)
        self.assertGreater(ctx.windows[2].calls, 0)

    def test_foreground_failure_retries_without_stepping_blocked_window(self):
        ctx, team = self.make_team(count=2)
        ctx.windows[0].activates = False
        stepped = []
        team._step_once = lambda rec: stepped.append(rec)
        self.assertEqual(team.run_until_formed(), (False, "not_enough"))
        self.assertEqual(team.formation_outcome, "timeout")
        self.assertGreater(ctx.windows[0].calls, 1)
        self.assertTrue(stepped)
        self.assertTrue(all(rec is team.records[1] for rec in stepped))

    def test_legacy_rejected_click_still_counts_without_changing_state_machine(self):
        ctx, team = self.make_team()
        rec = team.records[0]
        rec["state"] = teaming.C_ACCEPT
        rec["ctx"].mouse.click.return_value = False
        team._find_in_region = mock.Mock(return_value=(10, 20, 0.99))
        team._cap_accept(rec)
        self.assertEqual(team.joined, 1)
        self.assertEqual(rec["state"], teaming.C_ACCEPT)
        team._cap_accept(rec)
        self.assertEqual(team.joined, 2)
        self.assertEqual(rec["state"], teaming.C_CLOSE)
        # 这是旧判据基线，不能将其解释为真实成队核验。

    def test_dry_run_selfcheck_finishes_or_stops_without_input(self):
        for stopped in (False, True):
            with self.subTest(stopped=stopped):
                ctx, team = self.make_team(count=2, dry_run=True)
                if stopped:
                    team._sleep = lambda seconds: ctx.stop.set()
                self.assertEqual(team.run_until_formed(), (True, "dry_run"))
                self.assertEqual(team.formation_outcome, "stopped" if stopped else "completed")
                self.assertEqual(team.joined, 0)
                for rec in team.records:
                    rec["ctx"].send_hotkey.assert_not_called()
                    self.assertEqual(rec["ctx"].mouse.mock_calls, [])


class DungeonOutcomeTests(unittest.TestCase):
    def test_insufficient_windows_fail_and_preserve_config(self):
        for count in (0, 1):
            ctx = _Context([_Window() for _ in range(count)])
            before = copy.deepcopy(ctx.cfg)
            self.assertEqual(DungeonTask().run(ctx), "failed")
            self.assertEqual(ctx.cfg, before)

    def test_shared_evidence_reaches_runner_without_mutating_config(self):
        for dry_run, outcome in [(False, s) for s in ("completed", "partial", "failed", "timeout", "stopped")] + [(True, "completed"), (True, "stopped")]:
            with self.subTest(dry_run=dry_run, outcome=outcome):
                ctx = _Context([_Window(), _Window(), _Window()])
                ctx.cfg["tasks"]["teaming"].update(dry_run=dry_run, captain_index=1)
                before = copy.deepcopy(ctx.cfg)
                runtime = RuntimeService()
                task = DungeonTask()
                team = SimpleNamespace(formation_outcome=outcome,
                                       run_until_formed=mock.Mock(return_value=(outcome == "completed", "formed")))
                with mock.patch("mhxy.tasks.dungeon.TeamFormation") as factory, \
                        mock.patch.object(task, "_is_admin", return_value=True), \
                        mock.patch.object(task, "preflight", return_value=(True, [])), \
                        mock.patch("mhxy.core.runner.TaskContext", return_value=ctx), \
                        mock.patch("mhxy.core.runner.get_runtime", return_value=runtime):
                    factory.ROLE_CAPTAIN = teaming.TeamFormation.ROLE_CAPTAIN
                    factory.ROLE_MEMBER = teaming.TeamFormation.ROLE_MEMBER
                    factory.return_value = team
                    runner = TaskRunner(task, ctx.cfg)
                    self.assertEqual(runner.start(), (True, []))
                    runner.thread.join(timeout=2)
                    self.assertFalse(runner.thread.is_alive())
                    self.assertEqual(runner.outcome, outcome)
                    self.assertEqual(runtime.state()[0], outcome)
                self.assertEqual(ctx.cfg, before)
                team.run_until_formed.assert_called_once_with()
                args, kwargs = factory.call_args
                self.assertIs(args[1][0][0].window, ctx.windows[1])
                self.assertEqual([role for _, role in args[1]], ["captain", "member", "member"])
                self.assertEqual(kwargs, {"dry_run": dry_run})


if __name__ == "__main__":
    unittest.main()
