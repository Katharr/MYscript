# -*- coding: utf-8 -*-
"""一条龙单人入口：隔离窗口、模板和输入，验证退队能力及策略。"""
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

from mhxy.core.input_session import InputSession
from mhxy.tasks.daily import DailyTask


class DailySoloGuardTests(unittest.TestCase):
    def context(self, dry=False):
        cfg = {"tasks": {"teaming": {"dry_run": dry}, "daily": {}}}
        stop = threading.Event()
        return SimpleNamespace(cfg=cfg, stop=stop, should_stop=stop.is_set,
                               task_cfg=lambda key: cfg["tasks"][key], log=mock.Mock(),
                               input_session=InputSession(cfg, stop, "daily"))

    def test_guard_uses_current_window_and_teaming_input_policy(self):
        ctx = self.context()
        def run():
            self.assertEqual(ctx.input_session._policy, "teaming")
            self.assertFalse(ctx.input_session.dry_run())
            return True, "disbanded"
        team = SimpleNamespace(tpl={"team_quit": object(), "team_create": object()},
                               run_disband=mock.Mock(side_effect=run))
        with mock.patch("mhxy.core.teaming.TeamFormation", return_value=team) as factory:
            self.assertTrue(DailyTask._ensure_solo(ctx))
            self.assertEqual(factory.call_args.args[1], [(ctx, factory.ROLE_MEMBER)])
        self.assertEqual(ctx.input_session._policy, "daily")

    def test_dry_run_never_attempts_real_disband(self):
        with mock.patch("mhxy.core.teaming.TeamFormation") as factory:
            self.assertFalse(DailyTask._ensure_solo(self.context(dry=True)))
        factory.assert_not_called()

    def test_missing_templates_failed_disband_and_stop_are_blocked(self):
        for missing, success, stop in [(True, True, False), (False, False, False),
                                      (False, True, True)]:
            with self.subTest(missing=missing, success=success, stop=stop):
                ctx = self.context()
                if stop:
                    ctx.stop.set()
                team = SimpleNamespace(tpl={} if missing else {"team_quit": object(), "team_create": object()},
                                       run_disband=mock.Mock(return_value=(success, "result")))
                with mock.patch("mhxy.core.teaming.TeamFormation", return_value=team):
                    self.assertFalse(DailyTask._ensure_solo(ctx))
                if missing:
                    team.run_disband.assert_not_called()
                self.assertEqual(ctx.input_session._policy, "daily")
