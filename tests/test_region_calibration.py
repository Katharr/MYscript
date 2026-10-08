# -*- coding: utf-8 -*-
"""区域标定门禁：草稿一次提交，假窗口与临时配置，不操作真实资产。"""
import copy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from mhxy.core import calib_profiles as cp
from mhxy.core import config as cfg_mod
from mhxy.gui import calibrate_dialog as cd


class RegionCalibrationTests(unittest.TestCase):
    def setUp(self):
        self.cfg = copy.deepcopy(cfg_mod.DEFAULT_CONFIG)
        self.first = cp.get_or_create(self.cfg, [1200, 900])
        self.cfg["tasks"]["sniper"]["regions"]["purchase"] = [10, 20, 30, 40]
        cp.set_task_profile(self.cfg, "sniper", self.first)
        cp.set_template_profile(self.cfg, "sniper", "buy", self.first)
        self.before = copy.deepcopy(self.cfg)
        self.dialog = object.__new__(cd.CalibrateDialog)
        self.dialog.cfg = self.cfg
        self.dialog.tc = self.cfg["tasks"]["sniper"]
        self.dialog.task_name = "sniper"
        self.dialog._refresh = mock.Mock()
        self.dialog._toast = mock.Mock()
        self.dialog._grab_roi = mock.Mock(side_effect=self.capture)
        self.save_patch = mock.patch.object(cfg_mod, "save_config")
        self.save = self.save_patch.start()
        self.addCleanup(self.save_patch.stop)

    def capture(self, prompt, cfg, save_profile):
        self.assertIsNot(cfg, self.cfg)
        self.assertFalse(save_profile)
        pid = cp.get_or_create(cfg, [1600, 1200])
        return [1, 2, 3, 4], None, pid

    def action(self):
        self.dialog._calibrate_region("purchase", "购买区域")

    def unchanged(self):
        self.assertIs(self.dialog.cfg, self.cfg)
        self.assertIs(self.dialog.tc, self.cfg["tasks"][self.dialog.task_name])
        self.assertEqual(self.cfg, self.before)
        self.dialog._refresh.assert_not_called()
        self.assertFalse(any(call.args[1] == cd.T.SUCCESS
                             for call in self.dialog._toast.call_args_list))

    def test_success_saves_region_and_profile_once_before_publication(self):
        def saved(draft):
            self.unchanged()
            self.assertEqual(draft["tasks"]["sniper"]["regions"]["purchase"], [1, 2, 3, 4])
            self.assertEqual(cp.task_profile(draft, "sniper"), cp.active_id(draft))
            self.assertEqual(draft["targets"]["base_size"], [1600, 1200])
            self.assertEqual(cp.template_profile(draft, "sniper", "buy"), self.first)
            self.assertEqual(draft["tasks"]["escort"], self.before["tasks"]["escort"])
        self.save.side_effect = saved
        self.action()
        self.save.assert_called_once()
        self.assertIs(self.dialog.cfg, self.save.call_args.args[0])
        self.assertIs(self.dialog.tc, self.dialog.cfg["tasks"]["sniper"])
        self.dialog._refresh.assert_called_once()
        self.assertEqual(self.dialog._toast.call_args.args[1], cd.T.SUCCESS)

    def test_cancel_discards_even_mutated_profile_draft(self):
        def cancel(prompt, cfg, save_profile):
            self.capture(prompt, cfg, save_profile)
            return None, None, None
        self.dialog._grab_roi.side_effect = cancel
        self.action()
        self.unchanged()
        self.save.assert_not_called()

    def test_capture_exception_discards_profile_draft(self):
        def fail(prompt, cfg, save_profile):
            self.capture(prompt, cfg, save_profile)
            raise OSError("capture")
        self.dialog._grab_roi.side_effect = fail
        self.action()
        self.unchanged()
        self.save.assert_not_called()
        self.assertIn("capture", self.dialog._toast.call_args.args[0])

    def test_save_failure_preserves_region_and_ownership(self):
        self.save.side_effect = OSError("save")
        self.action()
        self.unchanged()
        self.save.assert_called_once()
        self.assertIn("save", self.dialog._toast.call_args.args[0])

    def full_groups(self):
        cp.get_or_create(self.cfg, [1700, 1300])
        cp.get_or_create(self.cfg, [1900, 1400])
        cp.set_task_profile(self.cfg, "escort", self.first)
        cp.set_template_profile(self.cfg, "escort", "escort_join", self.first)
        self.before = copy.deepcopy(self.cfg)
        def capture(prompt, cfg, save_profile):
            self.assertFalse(save_profile)
            pid = cd.resolve_profile(cfg, [2200, 1600],
                                     app=SimpleNamespace(on_profile_full=lambda size: self.first))
            return [1, 2, 3, 4], None, pid
        self.dialog._grab_roi.side_effect = capture

    def test_group_replacement_failure_preserves_all_cascaded_pointers(self):
        self.full_groups()
        self.save.side_effect = OSError("save")
        self.action()
        self.unchanged()
        self.save.assert_called_once()

    def test_group_replacement_success_keeps_cascaded_cleanup(self):
        self.full_groups()
        self.action()
        draft = self.dialog.cfg
        self.assertIsNone(cp.get(draft, self.first))
        self.assertIsNone(cp.task_profile(draft, "escort"))
        self.assertIsNone(cp.template_profile(draft, "escort", "escort_join"))
        self.assertIsNone(cp.template_profile(draft, "sniper", "buy"))
        self.assertEqual(draft["tasks"]["escort"]["templates"],
                         self.before["tasks"]["escort"]["templates"])
        self.assertEqual(cp.task_profile(draft, "sniper"), cp.active_id(draft))
        self.save.assert_called_once()

    def test_use_full_window_saves_once_and_keeps_profiles(self):
        def saved(draft):
            self.unchanged()
            expected = copy.deepcopy(self.before)
            expected["tasks"]["sniper"]["regions"]["purchase"] = None
            self.assertEqual(draft, expected)
        self.save.side_effect = saved
        self.dialog._use_full_window("purchase", "购买区域")
        self.save.assert_called_once()
        self.dialog._grab_roi.assert_not_called()
        self.assertIsNone(self.dialog.tc["regions"]["purchase"])
        self.dialog._refresh.assert_called_once()

    def test_use_full_window_failure_preserves_previous_region(self):
        self.save.side_effect = OSError("save")
        self.dialog._use_full_window("purchase", "购买区域")
        self.unchanged()
        self.save.assert_called_once()

    def test_invalid_ownership_fails_before_save(self):
        self.dialog._grab_roi.side_effect = None
        self.dialog._grab_roi.return_value = ([1, 2, 3, 4], None, 999)
        self.action()
        self.unchanged()
        self.save.assert_not_called()

    def test_launch_login_capture_keeps_desktop_flags_and_no_size_tracking(self):
        self.dialog.task_name = "launch_login"
        self.dialog.tc = self.cfg["tasks"]["launch_login"]
        app = mock.Mock()
        self.dialog.app = app
        window = SimpleNamespace(rect=lambda: (100, 200, 1600, 1200))
        self.dialog._grab_roi = cd.CalibrateDialog._grab_roi.__get__(self.dialog)
        with mock.patch.object(cd.win_mod, "locate_desktop_windows", return_value=[window]), \
                mock.patch.object(cd.win_mod, "desktop_window_at_point", return_value=window), \
                mock.patch.object(cd, "select_roi_on_screen", return_value=[110, 220, 30, 40]), \
                mock.patch.object(cd.time, "sleep"), \
                mock.patch.object(cd, "resolve_profile") as resolve:
            self.action()
        resolve.assert_not_called()
        self.save.assert_called_once()
        expected = copy.deepcopy(self.before)
        expected["tasks"]["launch_login"]["regions"]["purchase"] = [10, 20, 30, 40]
        self.assertEqual(self.dialog.cfg, expected)

    def test_game_capture_bridge_defers_profile_save(self):
        self.dialog.app = mock.Mock()
        window = SimpleNamespace(rect=lambda: (100, 200, 1600, 1200))
        self.dialog._grab_roi = cd.CalibrateDialog._grab_roi.__get__(self.dialog)
        with mock.patch.object(cd.win_mod, "locate_all", return_value=[window]), \
                mock.patch.object(cd.win_mod, "window_at_point", return_value=window), \
                mock.patch.object(cd, "select_roi_on_screen", return_value=[110, 220, 30, 40]), \
                mock.patch.object(cd.time, "sleep"):
            self.action()
        self.save.assert_called_once()
        draft = self.save.call_args.args[0]
        self.assertEqual(draft["tasks"]["sniper"]["regions"]["purchase"], [10, 20, 30, 40])
        self.assertEqual(draft["targets"]["base_size"], [1600, 1200])
        self.assertEqual(cp.task_profile(draft, "sniper"), cp.active_id(draft))

    def test_atomic_replace_failure_leaves_disk_and_memory_unchanged(self):
        self.save_patch.stop()
        with tempfile.TemporaryDirectory(prefix="region_config_") as temp:
            path = Path(temp) / "config.json"
            with mock.patch.object(cfg_mod, "CONFIG_PATH", path), \
                    mock.patch.object(cfg_mod, "CONFIG_BACKUP_DIR", Path(temp) / "backups"):
                cfg_mod.save_config(self.before)
                old_bytes = path.read_bytes()
                with mock.patch.object(cfg_mod.os, "replace", side_effect=OSError("replace")):
                    self.action()
                self.unchanged()
                self.assertEqual(path.read_bytes(), old_bytes)
                self.assertIn("replace", self.dialog._toast.call_args.args[0])

    def test_real_guard_rejection_leaves_config_and_memory_unchanged(self):
        self.save_patch.stop()
        with tempfile.TemporaryDirectory(prefix="region_config_") as temp:
            path = Path(temp) / "config.json"
            with mock.patch.object(cfg_mod, "CONFIG_PATH", path), \
                    mock.patch.object(cfg_mod, "CONFIG_BACKUP_DIR", Path(temp) / "backups"):
                cfg_mod.save_config(self.before)
                old_bytes = path.read_bytes()
                del self.cfg["window_title"]
                self.before = copy.deepcopy(self.cfg)
                self.action()
                self.unchanged()
                self.assertEqual(path.read_bytes(), old_bytes)
                self.assertEqual(self.dialog._toast.call_args.args[1], cd.T.DANGER)
