# -*- coding: utf-8 -*-
import copy
import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

from mhxy.core import accounts
from mhxy.core import launcher
from mhxy.core.config import DEFAULT_CONFIG
from mhxy.tasks.launch_login import LaunchLoginTask


class _Ctx:
    def __init__(self, cfg):
        self.cfg = cfg

    def task_cfg(self, name):
        return self.cfg["tasks"][name]


class _Window:
    class _Native:
        _hWnd = 778899

    _win = _Native()


class LaunchLoginTests(unittest.TestCase):
    def test_normalize_existing_launcher_path(self):
        expected = os.path.abspath(sys.executable)
        self.assertEqual(launcher.normalize_path(sys.executable), expected)
        self.assertEqual(launcher.normalize_path("start.py"), "")
        self.assertEqual(launcher.normalize_path("missing-launcher.exe"), "")

    def test_auto_detect_accepts_only_official_launcher_name(self):
        official = Path("C:/game") / launcher.LAUNCHER_EXE
        wrong = Path("C:/game/MyGame_x64r.exe")
        found, seen = [], set()
        with mock.patch.object(Path, "is_file", return_value=True):
            self.assertFalse(launcher._append_launcher(found, seen, wrong, 8))
            self.assertEqual(found, [])
            self.assertFalse(launcher._append_launcher(found, seen, official, 8))
        self.assertEqual(found, [str(official.resolve())])

    def test_preflight_accepts_complete_live_profile(self):
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        cfg["account_launch"]["launcher_path"] = os.path.abspath(sys.executable)
        cfg["account_launch"]["profiles"] = [{
            "id": "main", "label": "主号", "enabled": True,
            "expected_role_id": "r1", "expected_role_name": "角色",
        }]
        cfg["tasks"]["launch_login"]["dry_run"] = False
        for key in cfg["tasks"]["launch_login"]["templates"]:
            cfg["tasks"]["launch_login"]["templates"][key] = key + ".png"
        ctx = _Ctx(cfg)
        task = LaunchLoginTask()
        with mock.patch.object(task, "_is_admin", return_value=True), \
             mock.patch("mhxy.tasks.launch_login.vision.load_template", return_value=object()), \
             mock.patch("mhxy.tasks.launch_login.win_mod.locate_all", return_value=[]), \
             mock.patch("mhxy.tasks.launch_login.accounts.roster", return_value={"r1": {"name": "角色"}}):
            ok, problems = task.preflight(ctx)
        self.assertTrue(ok, problems)

    def test_preflight_blocks_non_admin(self):
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        ctx = _Ctx(cfg)
        task = LaunchLoginTask()
        with mock.patch.object(task, "_is_admin", return_value=False), \
             mock.patch("mhxy.tasks.launch_login.win_mod.locate_all", return_value=[]), \
             mock.patch("mhxy.tasks.launch_login.accounts.roster", return_value={}):
            ok, problems = task.preflight(ctx)
        self.assertFalse(ok)
        self.assertTrue(any("不是管理员" in item for item in problems))

    def test_state_template_uses_next_expected_screen(self):
        task = LaunchLoginTask()
        templates = {"start_game": "start", "enter_game": "enter", "existing_role": "existing"}
        self.assertEqual(task._template_for_state("WAIT_START", templates), "start")
        self.assertEqual(task._template_for_state("WAIT_ENTER", templates), "enter")
        self.assertIsNone(task._template_for_state("WAIT_ROLE", templates))
        self.assertEqual(task._previous_template_for_state("WAIT_ENTER", templates),
                         ("start", "开始游戏"))
        self.assertEqual(task._previous_template_for_state("WAIT_ROLE", templates),
                         (templates.get("existing_role"), "已有角色"))
        self.assertIsNone(task._previous_template_for_state("WAIT_START", templates))
        # 通用规则：每一步没成功都回查上一步，故 WAIT_EXISTING 也要回查到“切换”。
        self.assertEqual(task._previous_template_for_state("WAIT_EXISTING", {"switch_role": "sw"}),
                         ("sw", "切换"))

    def test_recheck_and_switch_defaults_match_user_request(self):
        loop = DEFAULT_CONFIG["tasks"]["launch_login"]["loop"]
        self.assertEqual(loop["switch_wait_min_sec"], 2.0)
        self.assertEqual(loop["switch_wait_max_sec"], 3.0)
        self.assertGreaterEqual(loop["recheck_max"], 2)          # 反复回查，不是只补点一次
        self.assertGreaterEqual(loop["recheck_interval_sec"], 1.0)

    def test_switch_waits_then_retries_until_existing_appears(self):
        """顺序验证：进入游戏页后先等待再点“切换”；“已有角色”没出现时反复回查“切换”。"""
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        cfg["account_launch"]["launcher_path"] = os.path.abspath(sys.executable)
        cfg["account_launch"]["profiles"] = [{
            "id": "main", "label": "主号", "enabled": True,
            "expected_role_id": "r1", "expected_role_name": "角色",
        }]
        tc = cfg["tasks"]["launch_login"]
        tc["dry_run"] = False
        tc["templates"] = {key: key + ".png" for key in tc["templates"]}
        tc["loop"].update({"switch_wait_min_sec": 0.2, "switch_wait_max_sec": 0.2,
                           "state_timeout_sec": 5.0})

        task = LaunchLoginTask()
        events = []
        seen = {"switch_retries": 0}

        class _Win:
            def rect(self): return [0, 0, 800, 600]
            def activate(self): return True

        class _RunCtx(_Ctx):
            def should_stop(self): return False
            def log(self, msg, level="info"): events.append((time.time(), "log:" + msg))

        def fake_click(ctx, win, tpl, threshold, action):
            events.append((time.time(), action))
            if action == "重新点击切换":
                seen["switch_retries"] += 1
            if action == "点击已有角色":
                return seen["switch_retries"] >= 3      # 回查 3 次后才出现“已有角色”
            return True

        with mock.patch.object(task, "_desktop_hwnds", return_value=set()), \
             mock.patch.object(task, "_wait_new_window", return_value=_Win()), \
             mock.patch.object(task, "_interruptible_sleep", lambda *a: None), \
             mock.patch.object(task, "_click_template", fake_click), \
             mock.patch.object(task, "_click_roster_role", return_value=True), \
             mock.patch("mhxy.tasks.launch_login.launcher.launch", return_value=(True, "")):
            result = task._run_profile(_RunCtx(cfg), cfg["account_launch"]["profiles"][0],
                                      {k: object() for k in tc["templates"]}, 0.85, 5.0, 1.0,
                                      {"after": 0.05, "interval": 0.05, "max": 3},
                                      {"wait_min": 0.2, "wait_max": 0.2}, False)

        actions = [a for _t, a in events]
        self.assertEqual(result, "ok")
        enter_at = next(t for t, a in events if a == "点击进入游戏")
        switch_at = next(t for t, a in events if a == "点击切换角色")
        self.assertGreaterEqual(switch_at - enter_at, 0.2)          # 先等 2~3 秒再点切换
        self.assertEqual(actions.count("重新点击切换"), 3)          # 反复回查切换，不限一次
        self.assertEqual(actions[-1], "点击已有角色")

    def test_any_step_repeatedly_rechecks_previous(self):
        """通用规则验证：不是只有“切换”步反复回查，任何步没成功都反复回查上一步。"""
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        cfg["account_launch"]["profiles"] = [{
            "id": "main", "label": "主号", "enabled": True,
            "expected_role_id": "r1", "expected_role_name": "角色",
        }]
        tc = cfg["tasks"]["launch_login"]
        tc["dry_run"] = False
        task = LaunchLoginTask()
        actions = []
        seen = {"start_retries": 0}

        class _Win:
            def rect(self): return [0, 0, 800, 600]
            def activate(self): return True

        class _RunCtx(_Ctx):
            def should_stop(self): return False
            def log(self, msg, level="info"): pass

        def fake_click(ctx, win, tpl, threshold, action):
            actions.append(action)
            if action == "重新点击开始游戏":
                seen["start_retries"] += 1
            if action == "点击进入游戏":
                return seen["start_retries"] >= 3      # 回查“开始游戏”3 次后才出现“进入游戏”
            return True

        with mock.patch.object(task, "_desktop_hwnds", return_value=set()), \
             mock.patch.object(task, "_wait_new_window", return_value=_Win()), \
             mock.patch.object(task, "_interruptible_sleep", lambda *a: None), \
             mock.patch.object(task, "_click_template", fake_click), \
             mock.patch.object(task, "_click_roster_role", return_value=True), \
             mock.patch("mhxy.tasks.launch_login.launcher.launch", return_value=(True, "")):
            result = task._run_profile(_RunCtx(cfg), cfg["account_launch"]["profiles"][0],
                                      {k: object() for k in tc["templates"]}, 0.85, 5.0, 1.0,
                                      {"after": 0.05, "interval": 0.05, "max": 4},
                                      {"wait_min": 0.0, "wait_max": 0.0}, False)
        self.assertEqual(result, "ok")
        self.assertEqual(actions.count("重新点击开始游戏"), 3)

    def test_roster_ocr_returns_target_text_center(self):
        fake_engine = mock.Mock(return_value=([
            [[[10, 20], [50, 20], [50, 40], [10, 40]], "角色", 0.99],
            [[[80, 20], [120, 20], [120, 40], [80, 40]], "其它", 0.99],
        ], 0.01))
        with mock.patch("mhxy.core.accounts._get_ocr_engine", return_value=fake_engine):
            hit = accounts.locate_roster_name(object(), {"r1": {"name": "角色"}}, "r1")
        self.assertIsNotNone(hit)
        self.assertEqual(hit[:2], (30, 30))
        with mock.patch("mhxy.core.accounts._get_ocr_engine", return_value=fake_engine):
            manual_hit = accounts.locate_roster_name(object(), {}, expected_name="角色")
        self.assertEqual(manual_hit[:2], (30, 30))

    def test_verified_launch_binding_is_runtime_only(self):
        win = _Window()
        self.assertTrue(accounts.bind_launch_session(win, "r1", "main"))
        session = accounts.launch_session(win)
        self.assertEqual(session["role_id"], "r1")
        self.assertEqual(session["profile_id"], "main")
        self.assertIn("bound_at", session)


if __name__ == "__main__":
    unittest.main()
