# -*- coding: utf-8 -*-
"""普通标定提交门禁：假窗口、独立图片和配置，无 Tk 或真实资产。"""
import copy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from mhxy.core import calibration_assets as assets
from mhxy.core import calib_profiles as cp
from mhxy.core import config as cfg_mod
from mhxy.gui import calibrate_dialog as cd


class CalibrationAssetsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="calibration_assets_")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.patch = mock.patch.object(cfg_mod, "PROJECT_ROOT", self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        for attr, value in (("CONFIG_PATH", self.root / "config.json"),
                            ("CONFIG_BACKUP_DIR", self.root / "config_backups")):
            patch = mock.patch.object(cfg_mod, attr, value)
            patch.start()
            self.addCleanup(patch.stop)
        self.cfg = copy.deepcopy(cfg_mod.DEFAULT_CONFIG)
        self.first = cp.get_or_create(self.cfg, [1200, 900])
        self.cfg["tasks"]["sniper"]["templates"]["buy"] = "templates/tm_buy.png"
        self.before = copy.deepcopy(self.cfg)
        self.dialog = object.__new__(cd.CalibrateDialog)
        self.dialog.cfg = self.cfg
        self.dialog.tc = self.cfg["tasks"]["sniper"]
        self.dialog.task_name = "sniper"
        self.dialog._refresh = mock.Mock()
        self.dialog._toast = mock.Mock()
        self.dialog._ask_name = mock.Mock(return_value="item")
        self.dialog._grab_roi = mock.Mock(side_effect=self.capture)
        self.real_save = cfg_mod.save_config
        self.save_patch = mock.patch.object(cfg_mod, "save_config")
        self.save = self.save_patch.start()
        self.addCleanup(self.save_patch.stop)
        self.encode_patch = mock.patch.object(cd.vision, "encode_png", return_value=b"new png")
        self.encode = self.encode_patch.start()
        self.addCleanup(self.encode_patch.stop)

    def capture(self, prompt, with_crop, cfg, save_profile):
        self.assertTrue(with_crop)
        self.assertFalse(save_profile)
        self.assertIsNot(cfg, self.cfg)
        pid = cp.get_or_create(cfg, [1600, 1200])
        return [1, 2, 3, 4], SimpleNamespace(size=12), pid

    def image(self, item=False):
        path = self.root / ("templates/item.png" if item else "templates/tm_buy.png")
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def run_action(self, item=False):
        if item:
            self.dialog._add_item()
        else:
            self.dialog._calibrate_template("buy", "购买")

    def unchanged(self):
        self.assertEqual(self.dialog.cfg, self.before)
        self.assertIs(self.dialog.tc, self.dialog.cfg["tasks"]["sniper"])
        self.dialog._refresh.assert_not_called()
        self.assertFalse(any(call.args[1] == cd.T.SUCCESS
                             for call in self.dialog._toast.call_args_list))

    def test_success_commits_image_config_and_ownership_once(self):
        for item in (False, True):
            with self.subTest(item=item):
                self.save.reset_mock()
                path = self.image(item)
                path.write_bytes(b"old")
                def saved(draft):
                    self.assertEqual(path.read_bytes(), b"new png")
                    self.assertEqual(self.cfg, self.before)
                    self.assertEqual(draft["targets"]["base_size"], [1600, 1200])
                    key = "item" if item else "buy"
                    self.assertEqual(cp.template_profile(draft, "sniper", key), cp.active_id(draft))
                    self.assertEqual(cp.task_profile(draft, "sniper"), cp.active_id(draft))
                    if item:
                        self.assertEqual(draft["tasks"]["sniper"]["watchlist"][-1]["template"], "templates/item.png")
                self.save.side_effect = saved
                self.run_action(item)
                self.save.assert_called_once()
                self.assertIs(self.dialog.tc, self.dialog.cfg["tasks"]["sniper"])

    def test_cancel_naming_discards_size_draft(self):
        self.dialog._ask_name.return_value = None
        self.run_action(True)
        self.unchanged()
        self.save.assert_not_called()
        self.encode.assert_not_called()
        self.assertFalse(self.image(True).exists())

    def test_cancel_and_empty_capture_do_not_commit(self):
        for item in (False, True):
            for result in ((None, None, None), ([1, 2, 3, 4], None, self.first),
                           ([1, 2, 3, 4], SimpleNamespace(size=0), self.first)):
                with self.subTest(item=item, result=result):
                    self.dialog._grab_roi.side_effect = None
                    self.dialog._grab_roi.return_value = result
                    self.run_action(item)
                    self.unchanged()
        self.save.assert_not_called()
        self.encode.assert_not_called()

    def test_encoding_failure_preserves_old_asset(self):
        for item in (False, True):
            for failure in (None, OSError("encode")):
                with self.subTest(item=item, failure=failure):
                    path = self.image(item)
                    path.write_bytes(b"old")
                    self.encode.side_effect = failure if isinstance(failure, Exception) else None
                    self.encode.return_value = None
                    self.run_action(item)
                    self.assertEqual(path.read_bytes(), b"old")
                    self.unchanged()
        self.save.assert_not_called()

    def test_save_failure_restores_existing_or_removes_new_asset(self):
        self.save.side_effect = OSError("save")
        for item in (False, True):
            for existing in (False, True):
                with self.subTest(item=item, existing=existing):
                    path = self.image(item)
                    if existing:
                        path.write_bytes(b"old")
                    self.run_action(item)
                    self.assertEqual(path.read_bytes() if path.exists() else None,
                                     b"old" if existing else None)
                    self.unchanged()

    def test_partial_write_failure_restores_old_bytes(self):
        path = self.image()
        path.write_bytes(b"old")
        original = Path.write_bytes
        def write(target, data):
            if data == b"new png":
                original(target, b"partial")
                raise OSError("write")
            return original(target, data)
        with mock.patch.object(Path, "write_bytes", write):
            self.run_action()
        self.assertEqual(path.read_bytes(), b"old")
        self.unchanged()
        self.save.assert_not_called()

    def test_read_failure_never_overwrites_asset(self):
        path = self.image()
        path.write_bytes(b"old")
        with mock.patch.object(Path, "read_bytes", side_effect=OSError("read")), \
                mock.patch.object(Path, "write_bytes") as write:
            self.run_action()
            write.assert_not_called()
        self.unchanged()
        self.save.assert_not_called()

    def test_restore_failure_is_explicit_and_never_reports_success(self):
        path = self.image()
        path.write_bytes(b"old")
        self.save.side_effect = OSError("save")
        original = Path.write_bytes
        def write(target, data):
            if data == b"old":
                raise OSError("restore")
            return original(target, data)
        with mock.patch.object(Path, "write_bytes", write):
            self.run_action()
        self.unchanged()
        self.assertIn("恢复失败", self.dialog._toast.call_args.args[0])

    def test_replacing_full_group_is_discarded_on_save_failure(self):
        cp.get_or_create(self.cfg, [1700, 1300])
        cp.get_or_create(self.cfg, [1900, 1400])
        cp.set_template_profile(self.cfg, "sniper", "buy", self.first)
        self.before = copy.deepcopy(self.cfg)
        def capture(prompt, with_crop, cfg, save_profile):
            pid = cd.resolve_profile(cfg, [2200, 1600],
                                     app=SimpleNamespace(on_profile_full=lambda size: self.first))
            return [1, 2, 3, 4], SimpleNamespace(size=12), pid
        self.dialog._grab_roi.side_effect = capture
        self.save.side_effect = OSError("save")
        self.run_action()
        self.unchanged()
        self.assertEqual(cp.template_profile(self.cfg, "sniper", "buy"), self.first)

    def test_real_save_guard_rejection_restores_asset(self):
        path = self.image()
        path.write_bytes(b"old")
        self.save_patch.stop()
        # 残缺配置会被真实 save_config 在写盘前拒绝。
        with self.assertRaises(assets.CalibrationAssetError):
            assets.commit_image({}, "templates/tm_buy.png", b"new png")
        self.assertEqual(path.read_bytes(), b"old")

    def prepare_delete(self):
        self.dialog.tc["watchlist"] = [
            {"name": "item", "template": "templates/item.png", "extra": 7},
            {"name": "other", "template": "templates/other.png"}]
        cp.set_template_profile(self.cfg, "sniper", "item", self.first)
        self.before = copy.deepcopy(self.cfg)

    def test_delete_success_saves_once_before_publish_preserves_file(self):
        self.prepare_delete()
        path = self.image(True)
        path.write_bytes(b"old")
        original_tc = self.dialog.tc
        def saved(draft):
            self.unchanged()
            tc = draft["tasks"]["sniper"]
            self.assertEqual(tc["watchlist"], self.before["tasks"]["sniper"]["watchlist"][1:])
            self.assertNotIn("item", tc["templates_calib"])
            self.assertEqual(tc["templates"], original_tc["templates"])
        self.save.side_effect = saved
        self.dialog._delete_item(0)
        self.save.assert_called_once()
        self.assertIsNot(self.dialog.cfg, self.cfg)
        self.assertEqual(self.cfg, self.before)
        self.assertEqual(path.read_bytes(), b"old")
        self.dialog._refresh.assert_called_once()

    def test_delete_failure_preserves_old_objects_and_references(self):
        self.prepare_delete()
        original_tc, original_items = self.dialog.tc, self.dialog.tc["watchlist"]
        self.save.side_effect = OSError("save")
        self.dialog._delete_item(0)
        self.unchanged()
        self.assertIs(self.dialog.cfg, self.cfg)
        self.assertIs(self.dialog.tc, original_tc)
        self.assertIs(self.dialog.tc["watchlist"], original_items)
        self.assertIn("删除装备失败", self.dialog._toast.call_args.args[0])

    def test_delete_real_atomic_failure_then_success_in_isolated_root(self):
        import json
        self.prepare_delete()
        path = self.image(True)
        path.write_bytes(b"old")
        self.real_save(self.cfg)
        disk_before = cfg_mod.CONFIG_PATH.read_bytes()
        self.save.side_effect = self.real_save
        with mock.patch.object(cfg_mod.os, "replace", side_effect=OSError("replace")):
            self.dialog._delete_item(0)
        self.unchanged()
        self.assertIs(self.dialog.cfg, self.cfg)
        self.assertEqual(cfg_mod.CONFIG_PATH.read_bytes(), disk_before)
        self.assertTrue(list((self.root / "config_backups").glob("config-*.json")))
        self.dialog._delete_item(0)
        disk = json.loads(cfg_mod.CONFIG_PATH.read_text(encoding="utf-8"))
        self.assertEqual(disk, self.dialog.cfg)
        self.assertEqual(path.read_bytes(), b"old")

    def test_delete_preserves_shared_fixed_template_ownership(self):
        self.prepare_delete()
        self.dialog.tc["templates"]["item"] = "templates/item.png"
        self.dialog._delete_item(0)
        self.assertEqual(cp.template_profile(self.dialog.cfg, "sniper", "item"), self.first)

    def test_delete_invalid_index_does_not_save(self):
        self.prepare_delete()
        for idx in (-1, 2, 100):
            self.dialog._delete_item(idx)
        self.unchanged()
        self.save.assert_not_called()

    def test_dialog_grab_forwards_draft_without_profile_save(self):
        self.dialog.app = SimpleNamespace()
        draft = copy.deepcopy(self.cfg)
        with mock.patch.object(cd, "grab_roi_on_app", return_value=(None, None, None)) as grab:
            cd.CalibrateDialog._grab_roi(self.dialog, "capture", with_crop=True,
                                        cfg=draft, save_profile=False)
        self.assertIs(grab.call_args.args[1], draft)
        self.assertFalse(grab.call_args.kwargs["save_profile"])
        self.assertTrue(grab.call_args.kwargs["track_profile"])
