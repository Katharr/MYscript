# -*- coding: utf-8 -*-
"""副本迁移门禁：保留失败后自动退队和点击即证据的旧边界。"""
import copy
import unittest
from types import SimpleNamespace
from unittest import mock

from mhxy.core import teaming
from mhxy.tasks.taohaiqu import TaohaiquTask
from tests.test_dungeon_outcome import _Context, _Window


class TaohaiquOutcomeTests(unittest.TestCase):
    def make_task(self, skip_team=True, auto_disband=False, dry_run=False, count=2):
        ctx = _Context([_Window() for _ in range(count)])
        ctx.cfg["tasks"]["taohaiqu"] = {
            "loop": {"match_threshold": .85}, "regions": {}, "templates": {},
            "dry_run": dry_run, "skip_team": skip_team, "auto_disband": auto_disband}
        task = TaohaiquTask()
        task._is_admin = mock.Mock(return_value=True)
        task._load_flags = mock.Mock(return_value={})
        task._interruptible_sleep = mock.Mock()
        task._focus = mock.Mock()
        return ctx, task

    def test_missing_windows_or_insufficient_team_fail_without_config_changes(self):
        for count, skip in [(0, True), (1, False)]:
            ctx, task = self.make_task(count=count, skip_team=skip)
            before = copy.deepcopy(ctx.cfg)
            self.assertEqual(task.run(ctx), "failed")
            ctx.stop.set()
            self.assertEqual(task.run(ctx), "stopped")
            self.assertEqual(ctx.cfg, before)

    def test_failed_formation_does_not_run_dungeon_or_disband(self):
        for outcome in ("failed", "timeout", "partial", "stopped"):
            ctx, task = self.make_task(skip_team=False, auto_disband=True)
            team = SimpleNamespace(formation_outcome=outcome,
                                   run_until_formed=mock.Mock(return_value=(False, "not_enough")),
                                   run_disband=mock.Mock())
            task._run_dungeon = mock.Mock()
            with mock.patch("mhxy.tasks.taohaiqu.TeamFormation") as factory:
                factory.ROLE_CAPTAIN = teaming.TeamFormation.ROLE_CAPTAIN
                factory.ROLE_MEMBER = teaming.TeamFormation.ROLE_MEMBER
                factory.return_value = team
                self.assertEqual(task.run(ctx), outcome)
            task._run_dungeon.assert_not_called()
            team.run_disband.assert_not_called()

    def test_dungeon_failure_still_calls_auto_disband_and_preserves_result(self):
        for outcome in ("failed", "timeout", "partial", "completed"):
            ctx, task = self.make_task(auto_disband=True)
            before = copy.deepcopy(ctx.cfg)
            task._run_dungeon = mock.Mock(return_value=outcome)
            team = SimpleNamespace(disband_outcome="completed", run_disband=mock.Mock(return_value=(True, "disbanded")))
            with mock.patch("mhxy.tasks.taohaiqu.TeamFormation") as factory:
                factory.ROLE_CAPTAIN = teaming.TeamFormation.ROLE_CAPTAIN
                factory.ROLE_MEMBER = teaming.TeamFormation.ROLE_MEMBER
                factory.return_value = team
                self.assertEqual(task.run(ctx), outcome)
            team.run_disband.assert_called_once_with()
            self.assertEqual(ctx.cfg, before)

    def test_incomplete_optional_cleanup_makes_completed_dungeon_partial(self):
        for outcome in ("failed", "timeout", "partial"):
            ctx, task = self.make_task(auto_disband=True)
            task._run_dungeon = mock.Mock(return_value="completed")
            team = SimpleNamespace(disband_outcome=outcome, run_disband=mock.Mock(return_value=(False, "partial")))
            with mock.patch("mhxy.tasks.taohaiqu.TeamFormation") as factory:
                factory.ROLE_CAPTAIN = teaming.TeamFormation.ROLE_CAPTAIN
                factory.ROLE_MEMBER = teaming.TeamFormation.ROLE_MEMBER
                factory.return_value = team
                self.assertEqual(task.run(ctx), "partial")

    def test_stop_after_dungeon_prevents_cleanup(self):
        ctx, task = self.make_task(auto_disband=True)
        def dungeon(*args):
            ctx.stop.set()
            return "completed"
        task._run_dungeon = mock.Mock(side_effect=dungeon)
        with mock.patch("mhxy.tasks.taohaiqu.TeamFormation") as factory:
            self.assertEqual(task.run(ctx), "stopped")
            factory.assert_not_called()

    def test_stopping_during_cleanup_has_priority(self):
        ctx, task = self.make_task(auto_disband=True)
        task._run_dungeon = mock.Mock(return_value="completed")
        def cleanup():
            ctx.stop.set()
            return False, "stopped"
        team = SimpleNamespace(disband_outcome="stopped", run_disband=cleanup)
        with mock.patch("mhxy.tasks.taohaiqu.TeamFormation") as factory:
            factory.return_value = team
            self.assertEqual(task.run(ctx), "stopped")

    def test_dry_run_continues_until_stop_without_business_calls(self):
        ctx, task = self.make_task(dry_run=True)
        task._grab_scene = mock.Mock(return_value=None)
        task._interruptible_sleep.side_effect = lambda *args: ctx.stop.set()
        task._run_dungeon = mock.Mock()
        self.assertEqual(task.run(ctx), "stopped")
        task._run_dungeon.assert_not_called()
        ctx.send_hotkey.assert_not_called()
        self.assertEqual(ctx.mouse.mock_calls, [])

    def test_dry_run_skips_foreground_failure_without_capturing(self):
        ctx, task = self.make_task(dry_run=True)
        for w in ctx.windows:
            w.activates = False
        task._grab_scene = mock.Mock()
        task._interruptible_sleep.side_effect = lambda *args: ctx.stop.set()
        self.assertEqual(task.run(ctx), "stopped")
        task._grab_scene.assert_not_called()

    def test_dungeon_linear_branch_evidence_and_optional_daily_skip(self):
        for failed_key, expected in [(None, "completed"), ("thq_select", "timeout"),
                                     ("thq_skip", "partial"), ("thq_task", "partial"),
                                     ("thq_teleport", "partial"), ("thq_clock", "partial"),
                                     ("thq_daily", "completed")]:
            with self.subTest(failed_key=failed_key):
                ctx, task = self.make_task()
                task.flags = {"thq_enter": object()}
                task._open_and_join = mock.Mock(return_value=True)
                task._click_enter = mock.Mock(return_value=True)
                task._click_scene_center = mock.Mock()
                task._click_when = mock.Mock(side_effect=lambda ctx, key, *a: key != failed_key)
                self.assertEqual(task._run_dungeon(ctx, {}, {}, .85), expected)

    def test_dungeon_prefix_failures_remain_without_retry(self):
        ctx, task = self.make_task()
        task.flags = {"thq_enter": object()}
        task._open_and_join = mock.Mock(return_value=False)
        task._click_when = mock.Mock(return_value=True)
        task._click_enter = mock.Mock(return_value=False)
        self.assertEqual(task._run_dungeon(ctx, {}, {}, .85), "failed")
        task._click_when.assert_not_called()
        task._open_and_join.return_value = True
        self.assertEqual(task._run_dungeon(ctx, {}, {}, .85), "timeout")
        task.flags = {}
        self.assertEqual(task._run_dungeon(ctx, {}, {}, .85), "failed")

    def test_daily_collective_caller_consumes_task_outcome(self):
        from mhxy.tasks.daily import DailyTask
        for outcome in ("completed", "partial", "failed", "timeout"):
            ctx, _ = self.make_task()
            before = copy.deepcopy(ctx.cfg)
            daily = DailyTask()
            child_task = mock.Mock()
            child_task.preflight.return_value = (True, [])
            child_task.run.return_value = outcome
            daily._resolve = mock.Mock(return_value=("taohaiqu", mock.Mock(return_value=child_task)))
            daily._title_of = mock.Mock(return_value="副本")
            self.assertEqual(daily._run_collective_dungeon(ctx), outcome)
            child_task.run.assert_called_once_with(ctx)
            self.assertEqual(ctx.cfg, before)
            expected = {"completed": "完成", "partial": "部分完成",
                        "failed": "失败", "timeout": "超时"}[outcome]
            self.assertEqual(ctx.log.call_args.args[0], f"───── 「副本」{expected} ─────")

    def test_rejected_click_still_satisfies_legacy_match_predicate(self):
        ctx, task = self.make_task()
        task._scene_rect = mock.Mock(return_value=(0, 0, 800, 600))
        task._match_scene = mock.Mock(return_value=(10, 20, .99))
        ctx.mouse.click.return_value = False
        with mock.patch("mhxy.tasks.taohaiqu.win_mod.grab", return_value=object()):
            self.assertTrue(task._click_when(ctx, "thq_clock", "小闹钟", {}, .85, 1))
        ctx.mouse.click.assert_called_once_with(10, 20)


if __name__ == "__main__":
    unittest.main()
