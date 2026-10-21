"""通用/活动标定的实际 Qt 草稿提交与来源展示，不操作游戏。"""

import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

import numpy as np

from mhxy.core import calib_profiles as cp, calibration, config as cfg_mod, vision
from mhxy.qtui import calibration_dialog as cd
from mhxy.qtui.calibration_page import CalibrationPage
from mhxy.qtui.main import get_application


class QtCommonCalibrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = get_application(["common-calibration-tests", "-platform", "offscreen"])

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="qt_common_calibration_")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for name, value in (("PROJECT_ROOT", self.root),
                            ("CONFIG_PATH", self.root / "config.json"),
                            ("CONFIG_BACKUP_DIR", self.root / "backups")):
            patcher = mock.patch.object(cfg_mod, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.cfg = copy.deepcopy(cfg_mod.DEFAULT_CONFIG)
        self.common_pid = cp.get_or_create(self.cfg, [1200, 900])
        self.local_pid = cp.get_or_create(self.cfg, [1600, 1200])
        self.shared = "templates/shared_join.png"
        self.pixels = np.full((24, 36, 3), (17, 71, 143), dtype=np.uint8)
        vision.save_image(str(self.root / self.shared), self.pixels)
        calibration.set_item(self.cfg, "common", "templates", "activity_join", self.shared, self.common_pid)
        calibration.set_item(self.cfg, "common", "regions", "scene", [10, 20, 100, 200], self.common_pid)
        cfg_mod.save_config(self.cfg)
        self.dialogs = []
        self.addCleanup(self.close_dialogs)

    def close_dialogs(self):
        for dialog in self.dialogs:
            dialog.close()
            dialog.deleteLater()
        self.app.processEvents()
        for worker in threading.enumerate():
            if worker.name == "mhxy-calibration-thumbnails":
                worker.join(timeout=2)

    def dialog(self, name="escort"):
        dialog = cd.CalibrationDialog(task_name=name, cfg=self.cfg)
        self.dialogs.append(dialog)
        return dialog

    def select(self, dialog, group, key):
        row = next(n for n, item in enumerate(dialog.rows) if item[:2] == (group, key))
        dialog.view.selectRow(row)
        return row

    def disk(self):
        return json.loads(cfg_mod.CONFIG_PATH.read_text(encoding="utf-8"))

    def test_common_entry_and_inherited_source_profile(self):
        self.assertEqual(cd.calibration_tasks()[0], ("common", "通用标定"))
        dialog = self.dialog()
        row = self.select(dialog, "templates", "escort_join")
        self.assertEqual(dialog.model.item(row, 4).text(), "通用")
        self.assertIn("1200×900", dialog.model.item(row, 2).text())
        self.assertEqual(dialog.capture_button.text(), "单独标定")
        self.assertFalse(dialog.restore_button.isEnabled())
        self.assertIn(self.shared, dialog.model.item(row, 3).text())

    def test_bound_shared_item_appears_without_duplicate_task_definition(self):
        dialog = self.dialog("taohaiqu")
        row = self.select(dialog, "templates", "daily_activity")
        self.assertEqual(dialog.model.item(row, 4).text(), "未标定")
        self.assertEqual(dialog.capture_button.text(), "单独标定")
        self.assertEqual(sum(item[:2] == ("templates", "daily_activity") for item in dialog.rows), 1)

    def test_common_page_direct_button_opens_common_editor(self):
        page = CalibrationPage(cfg=self.cfg)
        self.dialogs.append(page)
        with mock.patch.object(cd.CalibrationDialog, "exec", return_value=0), \
                mock.patch("mhxy.qtui.calibration_page.CalibrationDialog", wraps=cd.CalibrationDialog) as factory:
            page.common_button.click()
        self.assertEqual(factory.call_args.args[1], "common")

    def test_activity_capture_creates_independent_image_and_keeps_other_sizes(self):
        self.cfg["tasks"]["escort"]["regions"]["activity_list"] = [1, 2, 300, 400]
        cp.set_task_profile(self.cfg, "escort", self.common_pid)
        before_bytes = (self.root / self.shared).read_bytes()
        dialog = self.dialog()
        self.select(dialog, "templates", "escort_join")
        with mock.patch.object(dialog, "_grab_roi", return_value=([1, 2, 36, 24], self.pixels, self.local_pid)):
            dialog.calibrate_selected()
        item = calibration.resolve_item(dialog.cfg, "escort", "templates", "escort_join")
        self.assertEqual(item["source"], "local")
        self.assertEqual(item["profile"], self.local_pid)
        self.assertNotEqual(item["value"], self.shared)
        self.assertTrue((self.root / item["value"]).exists())
        self.assertEqual((self.root / self.shared).read_bytes(), before_bytes)
        self.assertEqual(calibration.resolve_item(dialog.cfg, "escort", "regions", "activity_list")["profile"],
                         self.common_pid)
        self.assertNotIn("escort_battle", dialog.cfg["tasks"]["escort"].get("templates_calib", {}))

    def test_restore_only_selected_override_and_keep_private_image(self):
        local = "templates/local_join.png"
        vision.save_image(str(self.root / local), self.pixels)
        calibration.set_item(self.cfg, "escort", "templates", "escort_join", local, self.local_pid)
        calibration.set_item(self.cfg, "escort", "templates", "escort_battle", "templates/battle.png", self.local_pid)
        dialog = self.dialog()
        row = self.select(dialog, "templates", "escort_join")
        self.assertEqual(dialog.model.item(row, 4).text(), "本活动")
        self.assertTrue(dialog.restore_button.isEnabled())
        dialog.restore_selected()
        self.assertEqual(calibration.resolve_item(dialog.cfg, "escort", "templates", "escort_join")["value"], self.shared)
        self.assertEqual(calibration.resolve_item(dialog.cfg, "escort", "templates", "escort_battle")["value"],
                         "templates/battle.png")
        self.assertTrue((self.root / local).exists())
        self.assertEqual(self.disk(), dialog.cfg)

    def test_explicit_full_window_is_distinct_from_inheritance(self):
        dialog = self.dialog()
        self.select(dialog, "regions", "scene")
        dialog.use_full_window()
        item = calibration.resolve_item(dialog.cfg, "escort", "regions", "scene")
        self.assertEqual((item["source"], item["value"]), ("local", None))
        row = self.select(dialog, "regions", "scene")
        self.assertEqual(dialog.model.item(row, 1).text(), "整窗检测")
        self.assertEqual(dialog.model.item(row, 4).text(), "本活动")
        dialog.restore_selected()
        item = calibration.resolve_item(dialog.cfg, "escort", "regions", "scene")
        self.assertEqual((item["source"], item["value"]), ("common", [10, 20, 100, 200]))

    def test_failed_capture_retains_inheritance_and_shared_bytes(self):
        dialog = self.dialog()
        self.select(dialog, "templates", "escort_join")
        before = copy.deepcopy(dialog.cfg)
        image_before = (self.root / self.shared).read_bytes()
        disk_before = cfg_mod.CONFIG_PATH.read_bytes()
        with mock.patch.object(dialog, "_grab_roi", return_value=([1, 2, 36, 24], self.pixels, self.local_pid)), \
                mock.patch.object(cfg_mod, "save_config", side_effect=OSError("save")):
            dialog.calibrate_selected()
        self.assertEqual(dialog.cfg, before)
        self.assertEqual(cfg_mod.CONFIG_PATH.read_bytes(), disk_before)
        self.assertEqual((self.root / self.shared).read_bytes(), image_before)
        self.assertFalse((self.root / "templates/tm_escort_join.png").exists())

    def test_failed_restore_does_not_publish_draft(self):
        calibration.set_item(self.cfg, "escort", "templates", "escort_join", "templates/private.png", self.local_pid)
        cfg_mod.save_config(self.cfg)
        dialog = self.dialog()
        self.select(dialog, "templates", "escort_join")
        before = copy.deepcopy(dialog.cfg)
        disk_before = cfg_mod.CONFIG_PATH.read_bytes()
        with mock.patch.object(cfg_mod, "save_config", side_effect=OSError("save")):
            dialog.restore_selected()
        self.assertEqual(dialog.cfg, before)
        self.assertEqual(cfg_mod.CONFIG_PATH.read_bytes(), disk_before)
        self.assertIn("失败", dialog.status.text())

    def test_common_recalibration_updates_inheritors_without_materializing(self):
        calibration.set_item(self.cfg, "secret_realm", "templates", "sr_join", "templates/private.png", self.local_pid)
        dialog = self.dialog("common")
        self.select(dialog, "templates", "activity_join")
        with mock.patch.object(dialog, "_grab_roi", return_value=([1, 2, 36, 24], self.pixels, self.local_pid)):
            dialog.calibrate_selected()
        self.assertEqual(calibration.resolve_item(dialog.cfg, "escort", "templates", "escort_join")["profile"],
                         self.local_pid)
        self.assertIsNone(dialog.cfg["tasks"]["escort"]["templates"]["escort_join"])
        self.assertEqual(calibration.resolve_item(dialog.cfg, "secret_realm", "templates", "sr_join")["value"],
                         "templates/private.png")


if __name__ == "__main__":
    unittest.main()
