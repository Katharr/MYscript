# -*- coding: utf-8 -*-
import copy
import os
import sys
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
            "expected_role_id": "r1", "expected_role_name": "角色", "role_template": "role.png",
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
        templates = {"start_game": "start", "enter_game": "enter", "existing_role": "existing",
                     "in_game_ready": "ready"}
        self.assertEqual(task._template_for_state("WAIT_START", templates, "role"), "start")
        self.assertEqual(task._template_for_state("WAIT_ENTER", templates, "role"), "enter")
        self.assertEqual(task._template_for_state("WAIT_ROLE", templates, "role"), "role")
        self.assertEqual(task._template_for_state("WAIT_READY", templates, "role"), "ready")
        self.assertEqual(task._previous_template_for_state("WAIT_ENTER", templates, "role"),
                         ("start", "开始游戏"))
        self.assertEqual(task._previous_template_for_state("WAIT_ROLE", templates, "role"),
                         (templates.get("existing_role"), "已有角色"))
        self.assertIsNone(task._previous_template_for_state("WAIT_START", templates, "role"))

    def test_verified_launch_binding_is_runtime_only(self):
        win = _Window()
        self.assertTrue(accounts.bind_launch_session(win, "r1", "main"))
        session = accounts.launch_session(win)
        self.assertEqual(session["role_id"], "r1")
        self.assertEqual(session["profile_id"], "main")
        self.assertIn("bound_at", session)


if __name__ == "__main__":
    unittest.main()
