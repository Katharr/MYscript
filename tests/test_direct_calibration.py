# -*- coding: utf-8 -*-
"""普通快捷标定门禁：独立临时资产、假框选与配置，不操作游戏。"""
import copy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from mhxy.core import calib_profiles as cp
from mhxy.core import config as cfg_mod
from mhxy.gui import calibrate_dialog as cd


class DirectCalibrationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="direct_calibration_")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.path = self.root / "templates/tm_buy.png"
        self.path.parent.mkdir()
        self.path.write_bytes(b"old png")
        self.loaded = copy.deepcopy(cfg_mod.DEFAULT_CONFIG)
        self.first = cp.get_or_create(self.loaded, [1200, 900])
        self.loaded["tasks"]["sniper"]["templates"]["buy"] = "templates/tm_buy.png"
        cp.set_task_profile(self.loaded, "sniper", self.first)
        cp.set_template_profile(self.loaded, "sniper", "buy", self.first)
        self.before = copy.deepcopy(self.loaded)
        self.app = SimpleNamespace(cfg=copy.deepcopy(self.loaded))
        self.original_app_cfg = self.app.cfg
        self.toast = mock.Mock()
        self.save = self.patch(cfg_mod, "save_config")
        self.patch(cfg_mod, "PROJECT_ROOT", self.root)
        self.patch(cfg_mod, "load_config", return_value=self.loaded)
        self.grab = self.patch(cd, "grab_roi_on_app", side_effect=self.capture)
        self.encode = self.patch(cd.vision, "encode_png", return_value=b"new png")
        self.save_image = self.patch(cd.vision, "save_image")

    def patch(self, obj, name, *args, **kwargs):
        patcher = mock.patch.object(obj, name, *args, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def capture(self, app, draft, prompt, **kwargs):
        self.assertIsNot(draft, self.loaded)
        self.assertFalse(kwargs["save_profile"])
        self.assertTrue(kwargs["with_crop"])
        pid = cp.get_or_create(draft, [1600, 1200]) if kwargs["track_profile"] else None
        return [1, 2, 3, 4], SimpleNamespace(size=12), pid

    def run_action(self, task="sniper"):
        return cd.calibrate_template_direct(self.app, task, "buy", "购买", self.toast)

    def unchanged(self):
        self.assertIs(self.app.cfg, self.original_app_cfg)
        self.assertEqual(self.app.cfg, self.before)
        self.assertEqual(self.loaded, self.before)
        self.save_image.assert_not_called()
        self.assertFalse(any(call.args[1] == cd.T.SUCCESS for call in self.toast.call_args_list))

    def test_success_saves_once_before_publishing_config_and_ownership(self):
        def save(draft):
            self.unchanged()
            self.assertEqual(self.path.read_bytes(), b"new png")
            pid = cp.active_id(draft)
            self.assertEqual(cp.task_profile(draft, "sniper"), pid)
            self.assertEqual(cp.template_profile(draft, "sniper", "buy"), pid)
            self.assertEqual(draft["targets"]["base_size"], [1600, 1200])
        self.save.side_effect = save
        self.assertTrue(self.run_action())
        self.save.assert_called_once()
        self.assertIs(self.app.cfg, self.save.call_args.args[0])
        self.assertEqual(self.loaded, self.before)
        self.save_image.assert_not_called()

    def test_cancel_after_size_edit_discards_draft(self):
        def cancel(*args, **kwargs):
            self.capture(*args, **kwargs)
            return None, None, None
        self.grab.side_effect = cancel
        self.assertFalse(self.run_action())
        self.unchanged()
        self.save.assert_not_called()
        self.encode.assert_not_called()
        self.assertEqual(self.path.read_bytes(), b"old png")

    def test_empty_capture_never_commits(self):
        for crop in (None, SimpleNamespace(size=0)):
            with self.subTest(crop=crop):
                self.grab.side_effect = lambda *a, **kw: (self.capture(*a, **kw)[0], crop, self.first)
                self.assertFalse(self.run_action())
                self.unchanged()
        self.save.assert_not_called()
        self.encode.assert_not_called()

    def test_encoding_failure_keeps_config_and_asset(self):
        for failure in (None, OSError("encode")):
            with self.subTest(failure=failure):
                self.encode.return_value = None
                self.encode.side_effect = failure
                self.assertFalse(self.run_action())
                self.unchanged()
                self.assertEqual(self.path.read_bytes(), b"old png")
        self.save.assert_not_called()

    def test_capture_exception_returns_failure_without_publication(self):
        def fail(*args, **kwargs):
            self.capture(*args, **kwargs)
            raise OSError("capture")
        self.grab.side_effect = fail
        self.assertFalse(self.run_action())
        self.unchanged()
        self.save.assert_not_called()
        self.assertIn("失败", self.toast.call_args.args[0])

    def test_invalid_ownership_rejected_before_image_write(self):
        self.grab.side_effect = None
        self.grab.return_value = ([1, 2, 3, 4], SimpleNamespace(size=12), 999)
        self.assertFalse(self.run_action())
        self.unchanged()
        self.save.assert_not_called()
        self.encode.assert_not_called()
        self.assertEqual(self.path.read_bytes(), b"old png")

    def test_save_failure_restores_old_or_removes_new_image(self):
        self.save.side_effect = OSError("save")
        for existing in (True, False):
            with self.subTest(existing=existing):
                if not existing:
                    self.path.unlink()
                self.save.reset_mock()
                self.assertFalse(self.run_action())
                self.save.assert_called_once()
                self.unchanged()
                self.assertEqual(self.path.read_bytes() if self.path.exists() else None,
                                 b"old png" if existing else None)

    def test_partial_image_write_failure_restores_old_bytes(self):
        original = Path.write_bytes
        def write(path, data):
            if data == b"new png":
                original(path, b"partial")
                raise OSError("write")
            return original(path, data)
        with mock.patch.object(Path, "write_bytes", write):
            self.assertFalse(self.run_action())
        self.unchanged()
        self.save.assert_not_called()
        self.assertEqual(self.path.read_bytes(), b"old png")

    def test_restore_failure_is_reported(self):
        original = Path.write_bytes
        def write(path, data):
            if data == b"old png":
                raise OSError("restore")
            return original(path, data)
        self.save.side_effect = OSError("save")
        with mock.patch.object(Path, "write_bytes", write):
            self.assertFalse(self.run_action())
        self.unchanged()
        self.assertIn("恢复失败", self.toast.call_args.args[0])

    def test_full_group_replacement_keeps_delete_cascade_on_success_only(self):
        cp.get_or_create(self.loaded, [1700, 1300])
        cp.get_or_create(self.loaded, [1900, 1400])
        cp.set_task_profile(self.loaded, "escort", self.first)
        cp.set_template_profile(self.loaded, "escort", "join", self.first)
        self.before = copy.deepcopy(self.loaded)
        self.app.cfg = copy.deepcopy(self.loaded)
        self.original_app_cfg = self.app.cfg
        def capture(app, draft, prompt, **kwargs):
            self.assertFalse(kwargs["save_profile"])
            pid = cd.resolve_profile(draft, [2200, 1600],
                                     app=SimpleNamespace(on_profile_full=lambda size: self.first))
            return [1, 2, 3, 4], SimpleNamespace(size=12), pid
        self.grab.side_effect = capture
        self.save.side_effect = OSError("save")
        self.assertFalse(self.run_action())
        self.unchanged()
        self.assertEqual(cp.task_profile(self.loaded, "escort"), self.first)
        self.save.reset_mock()
        self.save.side_effect = None
        self.assertTrue(self.run_action())
        self.save.assert_called_once()
        self.assertIsNone(cp.task_profile(self.app.cfg, "escort"))
        self.assertIsNone(cp.template_profile(self.app.cfg, "escort", "join"))
        self.assertEqual(self.app.cfg["targets"]["base_size"], [2200, 1600])

    def test_launch_login_does_not_change_size_groups(self):
        self.assertTrue(self.run_action("launch_login"))
        self.assertFalse(self.grab.call_args.kwargs["track_profile"])
        self.assertEqual(self.app.cfg["calib_profiles"], self.before["calib_profiles"])
        self.assertEqual(self.app.cfg["targets"], self.before["targets"])
        self.save.assert_called_once()

    def test_config_load_failure_returns_false(self):
        with mock.patch.object(cfg_mod, "load_config", side_effect=OSError("load")):
            self.assertFalse(self.run_action())
        self.unchanged()
        self.grab.assert_not_called()
        self.save.assert_not_called()
