# -*- coding: utf-8 -*-
"""解散结果保留旧退队/收尾判据；全部使用假窗口、时钟和输入。"""
import copy
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest import mock

from mhxy.core import rotation, teaming
from mhxy.core.runner import TaskRunner
from mhxy.core.runtime import RuntimeService
from mhxy.tasks.disband import DisbandTask
from tests.test_dungeon_outcome import _Context, _Window, _Clock


class DisbandEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.object(teaming.TeamFormation, "_load_templates", return_value={}))
        self.clock = _Clock()
        self.stack.enter_context(mock.patch.object(rotation.time, "time", self.clock.time))
        self.stack.enter_context(mock.patch.object(rotation, "_sleep", self.clock.sleep))
        self.stack.enter_context(mock.patch.object(teaming.win_mod, "grab", return_value=None))

    def make_team(self, count=2, dry_run=False):
        ctx = _Context([_Window() for _ in range(count)])
        ctx.cfg["tasks"]["teaming"]["loop"]["disband_timeout_sec"] = 5
        pairs = [(ctx.make_child(w, "窗口"), teaming.TeamFormation.ROLE_MEMBER) for w in ctx.windows]
        team = teaming.TeamFormation(ctx, pairs, ctx.task_cfg("teaming"), dry_run=dry_run)
        team._sleep = lambda seconds: None
        return ctx, team

    def test_all_records_ending_is_not_success(self):
        for good, expected in [(0, "failed"), (1, "partial"), (2, "completed")]:
            with self.subTest(good=good):
                ctx, team = self.make_team()
                seen = []
                def step(rec):
                    rec["ok"] = len(seen) < good
                    rec["done"] = True
                    seen.append(rec)
                team._disband_step = step
                self.assertEqual(team.run_disband(), (True, "disbanded") if good == 2 else (False, "partial"))
                self.assertEqual(team.disband_outcome, expected)

    def test_timeout_zero_or_partial_and_blocked_foreground(self):
        for good, expected in [(0, "timeout"), (1, "partial")]:
            ctx, team = self.make_team()
            ctx.windows[1].activates = False
            seen = []
            def step(rec):
                seen.append(rec)
                if good:
                    rec.update(ok=True, done=True)
            team._disband_step = step
            self.assertEqual(team.run_disband(), (False, "partial"))
            self.assertEqual(team.disband_outcome, expected)
            self.assertTrue(seen)
            self.assertTrue(all(r["ctx"].window is ctx.windows[0] for r in seen))
            self.assertGreater(ctx.windows[1].calls, 1)

    def test_lost_windows_fail_or_leave_other_window_partial(self):
        for all_lost, expected in [(True, "failed"), (False, "partial")]:
            ctx, team = self.make_team()
            ctx.windows[0].present = False
            ctx.windows[1].present = not all_lost
            team._disband_step = lambda rec: rec.update(ok=True, done=True)
            self.assertEqual(team.run_disband(), (False, "partial"))
            self.assertEqual(team.disband_outcome, expected)
            self.assertEqual(ctx.windows[0].calls, 0)

    def test_stopped_has_priority_and_does_not_start_cleanup(self):
        ctx, team = self.make_team()
        def step(rec):
            rec.update(ok=True, done=True)
            ctx.stop.set()
        team._disband_step = step
        team._close_panel = mock.Mock()
        self.assertEqual(team.run_disband(), (False, "stopped"))
        self.assertEqual(team.disband_outcome, "stopped")
        team._close_panel.assert_not_called()
        self.assertEqual(ctx.windows[1].calls, 0)

    def test_legacy_click_and_cleanup_rejection_still_mark_ok(self):
        for hit in [(10, 20, .99), None]:
            with self.subTest(hit=hit):
                ctx, team = self.make_team()
                rec = team._new_disband_record(team.records[0]["ctx"])
                rec["state"] = teaming.D_QUIT
                team._find_in_region = mock.Mock(return_value=hit)
                team._close_panel = mock.Mock(return_value=False)
                rec["ctx"].mouse.click.return_value = False
                team._db_quit(rec)
                self.assertTrue(rec["ok"])
                self.assertTrue(rec["done"])
                team._close_panel.assert_called_once()
                if hit:
                    rec["ctx"].mouse.click.assert_called_once_with(10, 20)
                else:
                    rec["ctx"].mouse.click.assert_not_called()

    def test_open_rejection_remains_failed_not_retried(self):
        ctx, team = self.make_team()
        for rec in team.records:
            rec["ctx"].send_hotkey.return_value = False
        self.assertEqual(team.run_disband(), (False, "partial"))
        self.assertEqual(team.disband_outcome, "failed")
        for rec in team.records:
            rec["ctx"].send_hotkey.assert_called_once_with("open_team")
            self.assertEqual(rec["ctx"].mouse.mock_calls, [])

    def test_dry_run_finishes_or_stops_without_input_and_resets_evidence(self):
        ctx, team = self.make_team(dry_run=True)
        self.assertEqual(team.run_disband(), (True, "dry_run"))
        self.assertEqual(team.disband_outcome, "completed")
        team._sleep = lambda seconds: ctx.stop.set()
        self.assertEqual(team.run_disband(), (True, "dry_run"))
        self.assertEqual(team.disband_outcome, "stopped")
        for rec in team.records:
            rec["ctx"].send_hotkey.assert_not_called()
            self.assertEqual(rec["ctx"].mouse.mock_calls, [])


class DisbandTaskTests(unittest.TestCase):
    def test_missing_windows_is_failed_or_stopped_and_preserves_config(self):
        ctx = _Context([])
        before = copy.deepcopy(ctx.cfg)
        self.assertEqual(DisbandTask().run(ctx), "failed")
        ctx.stop.set()
        self.assertEqual(DisbandTask().run(ctx), "stopped")
        self.assertEqual(ctx.cfg, before)

    def test_shared_evidence_reaches_runtime(self):
        for dry_run, outcome in [(False, s) for s in ("completed", "partial", "failed", "timeout", "stopped")] + [(True, "completed"), (True, "stopped")]:
            with self.subTest(dry_run=dry_run, outcome=outcome):
                ctx = _Context([_Window(), _Window()])
                ctx.cfg["tasks"]["teaming"]["dry_run"] = dry_run
                before = copy.deepcopy(ctx.cfg)
                runtime = RuntimeService()
                task = DisbandTask()
                team = SimpleNamespace(disband_outcome=outcome, run_disband=mock.Mock(return_value=(outcome == "completed", "partial")))
                with mock.patch("mhxy.tasks.disband.TeamFormation") as factory, \
                     mock.patch.object(task, "_is_admin", return_value=True), \
                     mock.patch.object(task, "preflight", return_value=(True, [])), \
                     mock.patch("mhxy.core.runner.TaskContext", return_value=ctx), \
                     mock.patch("mhxy.core.runner.get_runtime", return_value=runtime):
                    factory.ROLE_MEMBER = teaming.TeamFormation.ROLE_MEMBER
                    factory.return_value = team
                    runner = TaskRunner(task, ctx.cfg)
                    self.assertEqual(runner.start(), (True, []))
                    runner.thread.join(timeout=2)
                    self.assertFalse(runner.thread.is_alive())
                    self.assertEqual(runner.outcome, outcome)
                    self.assertEqual(runtime.state()[0], outcome)
                self.assertEqual(ctx.cfg, before)
                team.run_disband.assert_called_once_with()
                args, kwargs = factory.call_args
                self.assertEqual([role for _, role in args[1]], ["member", "member"])
                self.assertEqual(kwargs, {"dry_run": dry_run})


if __name__ == "__main__":
    unittest.main()
