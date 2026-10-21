"""Qt 标定回归：真实临时配置/图片、冻结像素、假游戏窗口，不操作真实游戏。"""

import copy
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

import numpy as np
from PySide6.QtCore import QPointF, Qt, QTimer, QSettings
from PySide6.QtGui import QMouseEvent, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog

from mhxy.core import calib_profiles as cp, config as cfg_mod, vision
from mhxy.qtui.main import get_application
from mhxy.qtui import calibration_dialog as cd, roi_overlay as ro
from mhxy.qtui.calibration_page import CalibrationPage
from mhxy.qtui.window_size_panel import WindowSizePanel


class QtCalibrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = get_application(["qt-calibration-tests", "-platform", "offscreen"])

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="qt_标定_")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for name, value in (("PROJECT_ROOT", self.root), ("CONFIG_PATH", self.root / "config.json"),
                            ("CONFIG_BACKUP_DIR", self.root / "config_backups")):
            patcher = mock.patch.object(cfg_mod, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.cfg = copy.deepcopy(cfg_mod.DEFAULT_CONFIG)
        self.first = cp.get_or_create(self.cfg, [1200, 900])
        cp.set_task_profile(self.cfg, "sniper", self.first)
        cp.set_template_profile(self.cfg, "sniper", "sniper_product", self.first)
        cfg_mod.save_config(self.cfg)
        self.pixels = np.full((40, 30, 3), (13, 47, 91), dtype=np.uint8)
        self.dialogs = []
        self.addCleanup(self.close_dialogs)

    def close_dialogs(self):
        for dialog in reversed(self.dialogs):
            dialog.close()
            dialog.deleteLater()
        self.app.processEvents()
        # 后台解码不能跨测试的临时 PROJECT_ROOT 存活。
        for worker in threading.enumerate():
            if worker.name in ("mhxy-calibration-thumbnails", "mhxy-item-thumbnails"):
                worker.join(timeout=2)

    def dialog(self, task="sniper", cfg=None):
        dialog = cd.CalibrationDialog(task_name=task, cfg=self.cfg if cfg is None else cfg)
        self.dialogs.append(dialog)
        return dialog

    def select(self, dialog, group, key):
        dialog.view.selectRow(next(n for n, row in enumerate(dialog.rows) if row[:2] == (group, key)))

    def capture(self, draft, prompt):
        pid = cp.get_or_create(draft, [1600, 1200])
        return [1, 2, 30, 40], self.pixels, pid

    def disk(self):
        return json.loads(cfg_mod.CONFIG_PATH.read_text(encoding="utf-8"))

    def test_region_saves_once_before_signal_and_reopens(self):
        dialog = self.dialog()
        self.select(dialog, "regions", "purchase")
        before = copy.deepcopy(dialog.cfg)
        observed = []
        dialog.config_changed.connect(lambda cfg: observed.append((cfg, self.disk())))
        with mock.patch.object(dialog, "_grab_roi", side_effect=self.capture), \
                mock.patch.object(cfg_mod, "save_config", wraps=cfg_mod.save_config) as save:
            dialog.calibrate_selected()
        save.assert_called_once()
        self.assertEqual(self.cfg, before)
        self.assertEqual(observed[0][0], observed[0][1])
        reopened = self.dialog(cfg=self.disk())
        self.assertEqual(reopened.cfg["tasks"]["sniper"]["regions"]["purchase"], [1, 2, 30, 40])
        self.assertEqual(reopened.cfg["targets"]["base_size"], [1600, 1200])
        self.assertEqual(cp.task_profile(reopened.cfg, "sniper"), cp.active_id(reopened.cfg))

    def test_cancel_discards_modified_size_draft_without_save(self):
        dialog = self.dialog()
        before = copy.deepcopy(dialog.cfg)
        disk = cfg_mod.CONFIG_PATH.read_bytes()

        def cancel(draft, prompt):
            self.capture(draft, prompt)
            return None

        with mock.patch.object(dialog, "_grab_roi", side_effect=cancel), \
                mock.patch.object(cfg_mod, "save_config") as save:
            dialog.calibrate_selected()
        save.assert_not_called()
        self.assertEqual(dialog.cfg, before)
        self.assertEqual(cfg_mod.CONFIG_PATH.read_bytes(), disk)

    def test_region_atomic_failure_keeps_disk_and_memory(self):
        dialog = self.dialog()
        self.select(dialog, "regions", "purchase")
        before = copy.deepcopy(dialog.cfg)
        disk = cfg_mod.CONFIG_PATH.read_bytes()
        with mock.patch.object(dialog, "_grab_roi", side_effect=self.capture), \
                mock.patch.object(cfg_mod.os, "replace", side_effect=OSError("replace")):
            dialog.calibrate_selected()
        self.assertEqual(dialog.cfg, before)
        self.assertEqual(cfg_mod.CONFIG_PATH.read_bytes(), disk)
        self.assertIn("失败", dialog.status.text())

    def test_full_window_keeps_size_and_other_template_ownership(self):
        self.cfg["tasks"]["sniper"]["regions"]["purchase"] = [10, 20, 30, 40]
        dialog = self.dialog()
        self.select(dialog, "regions", "purchase")
        dialog.use_full_window()
        expected = copy.deepcopy(self.cfg)
        expected["tasks"]["sniper"]["regions"]["purchase"] = None
        self.assertEqual(self.disk(), expected)

    def test_template_failure_restores_old_or_removes_new_image(self):
        for existing in (False, True):
            with self.subTest(existing=existing):
                dialog = self.dialog()
                self.select(dialog, "templates", "sniper_product")
                path = self.root / "templates/tm_sniper_product.png"
                path.parent.mkdir(exist_ok=True)
                if existing:
                    path.write_bytes(b"old")
                before = copy.deepcopy(dialog.cfg)
                with mock.patch.object(dialog, "_grab_roi", side_effect=self.capture), \
                        mock.patch.object(cfg_mod, "save_config", side_effect=OSError("save")):
                    dialog.calibrate_selected()
                self.assertEqual(path.read_bytes() if path.exists() else None, b"old" if existing else None)
                self.assertEqual(dialog.cfg, before)

    def test_template_save_preserves_custom_chinese_path_and_reopens(self):
        self.cfg["tasks"]["sniper"]["templates"]["sniper_product"] = "templates/商品原图.png"
        dialog = self.dialog()
        self.select(dialog, "templates", "sniper_product")
        with mock.patch.object(dialog, "_grab_roi", side_effect=self.capture):
            dialog.calibrate_selected()
        path = self.root / "templates/商品原图.png"
        self.assertTrue(path.exists())
        reopened = self.dialog(cfg=self.disk())
        self.assertEqual(reopened.cfg["tasks"]["sniper"]["templates"]["sniper_product"], "templates/商品原图.png")
        self.assertEqual(cp.template_profile(reopened.cfg, "sniper", "sniper_product"), cp.active_id(reopened.cfg))
        np.testing.assert_array_equal(vision.load_template(str(path)), self.pixels)

    def test_encoding_and_invalid_ownership_fail_before_image_write(self):
        for invalid in (False, True):
            with self.subTest(invalid=invalid):
                dialog = self.dialog()
                self.select(dialog, "templates", "sniper_product")
                with mock.patch.object(dialog, "_grab_roi", return_value=([1, 2, 30, 40], self.pixels, 999 if invalid else self.first)), \
                        mock.patch.object(vision, "encode_png", return_value=None), \
                        mock.patch.object(cfg_mod, "save_config") as save:
                    dialog.calibrate_selected()
                save.assert_not_called()
                self.assertFalse((self.root / "templates/tm_sniper_product.png").exists())
                self.assertEqual(dialog.cfg, self.cfg)

    def test_chinese_equipment_save_reopen_recalibrate_delete_keeps_image(self):
        dialog = self.dialog()
        with mock.patch.object(dialog, "_grab_roi", side_effect=self.capture), \
                mock.patch.object(cd.QInputDialog, "getText", return_value=("中文装备", True)):
            dialog.add_item()
        path = self.root / "templates/中文装备.png"
        self.assertTrue(path.exists())
        reopened = self.dialog(cfg=self.disk())
        self.select(reopened, "watchlist", 0)
        with mock.patch.object(reopened, "_grab_roi", side_effect=self.capture):
            reopened.calibrate_selected()
        item = reopened.cfg["tasks"]["sniper"]["watchlist"][0]
        self.assertEqual(item["template"], "templates/中文装备.png")
        self.select(reopened, "watchlist", 0)
        before = copy.deepcopy(reopened.cfg)
        with mock.patch.object(cfg_mod, "save_config", side_effect=OSError("save")):
            reopened.delete_item()
        self.assertEqual(reopened.cfg, before)
        reopened.delete_item()
        self.assertEqual(self.disk()["tasks"]["sniper"]["watchlist"], [])
        self.assertIsNone(cp.template_profile(self.disk(), "sniper", "中文装备"))
        self.assertTrue(path.exists())

    def test_cancel_equipment_naming_does_not_save_size_or_image(self):
        dialog = self.dialog()
        with mock.patch.object(dialog, "_grab_roi", side_effect=self.capture), \
                mock.patch.object(cd.QInputDialog, "getText", return_value=("", False)), \
                mock.patch.object(cfg_mod, "save_config") as save:
            dialog.add_item()
        save.assert_not_called()
        self.assertEqual(dialog.cfg, self.cfg)
        self.assertFalse((self.root / "templates").exists())

    def test_full_group_replace_cancel_failure_then_success(self):
        cp.get_or_create(self.cfg, [1800, 1300])
        cp.get_or_create(self.cfg, [2000, 1400])
        cp.set_task_profile(self.cfg, "escort", self.first)
        cfg_mod.save_config(self.cfg)
        dialog = self.dialog()
        self.select(dialog, "regions", "purchase")
        before = copy.deepcopy(self.cfg)
        with mock.patch.object(cd, "capture_roi", return_value=([1, 2, 30, 40], self.pixels, [2400, 1700])), \
                mock.patch.object(cd.QInputDialog, "getItem", return_value=("", False)), \
                mock.patch.object(cfg_mod, "save_config") as save:
            dialog.calibrate_selected()
        save.assert_not_called()
        self.assertEqual(dialog.cfg, before)
        label = f"{cp.label_for(self.first)} · 1200×900"
        with mock.patch.object(cd, "capture_roi", return_value=([1, 2, 30, 40], self.pixels, [2400, 1700])), \
                mock.patch.object(cd.QInputDialog, "getItem", return_value=(label, True)):
            with mock.patch.object(cfg_mod, "save_config", side_effect=OSError("save")):
                dialog.calibrate_selected()
            self.assertEqual(dialog.cfg, before)
            dialog.calibrate_selected()
        self.assertEqual(len(cp.items(dialog.cfg)), 3)
        self.assertIsNone(cp.get(dialog.cfg, self.first))
        self.assertIsNone(cp.task_profile(dialog.cfg, "escort"))
        self.assertEqual(dialog.cfg["targets"]["base_size"], [2400, 1700])

    def test_profile_switch_delete_failure_and_last_delete_mirror(self):
        cp.get_or_create(self.cfg, [1600, 1200])
        cfg_mod.save_config(self.cfg)
        page = WindowSizePanel(cfg=self.cfg)
        self.dialogs.append(page)
        with mock.patch.object(cfg_mod, "save_config", side_effect=OSError("save")):
            page.activate_profile(0)
        self.assertEqual(page.cfg, self.cfg)
        page.activate_profile(0)
        self.assertEqual(page.cfg["targets"]["base_size"], [1200, 900])
        page.delete_profile(cp.active_id(page.cfg))
        self.assertIsNone(cp.template_profile(page.cfg, "sniper", "sniper_product"))
        self.assertEqual(page.cfg["targets"]["base_size"], [1600, 1200])
        page.delete_profile(cp.active_id(page.cfg))
        self.assertIsNone(page.cfg["targets"]["base_size"])

    def test_launcher_uses_desktop_without_tracking_size(self):
        dialog = self.dialog("launch_login")
        with mock.patch.object(cd, "capture_roi", return_value=([1, 2, 30, 40], self.pixels, [2400, 1700])) as capture:
            dialog.calibrate_selected()
        self.assertTrue(capture.call_args.kwargs["allow_desktop_windows"])
        self.assertEqual(dialog.cfg["calib_profiles"], self.cfg["calib_profiles"])
        self.assertEqual(dialog.cfg["targets"], self.cfg["targets"])

    def test_leader_template_uses_history_transaction(self):
        dialog = self.dialog("teaming")
        self.select(dialog, "templates", "leader_id")
        with mock.patch.object(dialog, "_grab_roi", side_effect=self.capture):
            dialog.calibrate_selected()
        from mhxy.core import leader_history as lh
        history = lh.get_history(dialog.cfg)
        self.assertEqual(len(history), 1)
        self.assertEqual((self.root / lh.ACTIVE_REL).read_bytes(), (self.root / history[0]["slot"]).read_bytes())
        self.assertEqual(dialog.cfg, self.disk())

    def test_thumbnails_decode_chinese_path_fail_closed_and_ignore_stale_generation(self):
        path = self.root / "中文模板.png"
        path.write_bytes(vision.encode_png(self.pixels))
        self.cfg["tasks"]["sniper"]["templates"]["sniper_product"] = str(path)
        dialog = self.dialog()
        row = next(n for n, it in enumerate(dialog.rows) if it[:2] == ("templates", "sniper_product"))
        for _ in range(100):
            if dialog.model.item(row, 1).text() == "已标定":
                break
            QTest.qWait(10)
        self.assertEqual(dialog.model.item(row, 1).text(), "已标定")
        generation = dialog._generation
        dialog.tasks.setCurrentIndex(dialog.tasks.findData("activity"))
        count = dialog.model.rowCount()
        dialog._apply_thumbnails(generation, [(cd._template_signature(path), QImage())])
        self.assertEqual(dialog.model.rowCount(), count)
        path.write_bytes(b"broken")
        dialog.tasks.setCurrentIndex(dialog.tasks.findData("sniper"))
        QTest.qWait(100)
        self.assertEqual(dialog.model.item(row, 1).text(), "未标定")

    def test_capture_relative_coordinates_and_cancel_never_mutate_config(self):
        window = SimpleNamespace(rect=lambda: [100, 200, 800, 600])
        before = copy.deepcopy(self.cfg)
        with mock.patch.object(ro.win_mod, "locate_all", return_value=[window]), \
                mock.patch.object(ro.win_mod, "window_at_point", return_value=window) as at_point, \
                mock.patch.object(ro, "select_roi_on_screen", return_value=([110, 220, 30, 40], self.pixels)):
            roi, crop, size = ro.capture_roi(None, self.cfg, "框选")
        self.assertEqual(roi, [10, 20, 30, 40])
        self.assertEqual(size, [800, 600])
        at_point.assert_called_once_with(self.cfg["window_title"], self.cfg["window_offset"], 125, 240)
        self.assertEqual(self.cfg, before)
        with mock.patch.object(ro.win_mod, "locate_all", return_value=[window]), \
                mock.patch.object(ro, "select_roi_on_screen", return_value=(None, None)):
            self.assertIsNone(ro.capture_roi(None, self.cfg, "框选"))
        self.assertEqual(self.cfg, before)

    def test_capture_cancel_and_exception_restore_opacity_without_ending_editor(self):
        dialog = self.dialog()
        window = SimpleNamespace(rect=lambda: [100, 200, 800, 600])
        finished = []
        remained_open = []
        dialog.finished.connect(finished.append)

        def capture():
            for result in ((None, None), OSError("screen")):
                with mock.patch.object(ro.win_mod, "locate_all", return_value=[window]), \
                        mock.patch.object(ro, "select_roi_on_screen") as select:
                    if isinstance(result, Exception):
                        select.side_effect = result
                        with self.assertRaises(OSError):
                            ro.capture_roi(dialog, self.cfg, "框选")
                    else:
                        select.return_value = result
                        self.assertIsNone(ro.capture_roi(dialog, self.cfg, "框选"))
                self.assertEqual(dialog.windowOpacity(), 1.0)
                self.assertEqual(finished, [])
            # 真正回到编辑器的 exec 事件循环后再关闭，不能因隐身而提前返回。
            QTimer.singleShot(10, lambda: remained_open.append(dialog.isVisible()))
            QTimer.singleShot(20, dialog.reject)

        QTimer.singleShot(0, capture)
        QTimer.singleShot(1500, dialog.reject)
        dialog.exec()
        self.assertEqual(remained_open, [True])

    def test_item_cancel_after_capture_does_not_publish_size_or_assets(self):
        from mhxy.qtui.inventory_items_dialog import InventoryItemsDialog

        dialog = InventoryItemsDialog(cfg=self.cfg)
        self.dialogs.append(dialog)
        with mock.patch.object(cd, "capture_roi", return_value=([1, 2, 30, 40], self.pixels, [1600, 1200])), \
                mock.patch.object(cd.QInputDialog, "getItem", return_value=("", False)), \
                mock.patch.object(cfg_mod, "save_config") as save:
            dialog.add_items()
        save.assert_not_called()
        self.assertEqual(dialog.cfg, self.cfg)
        self.assertEqual(dialog.model.items, [])
        self.assertFalse((self.root / "templates").exists())

    def test_item_editor_uses_same_capture_and_preserves_action_on_reopen(self):
        from mhxy.qtui.inventory_items_dialog import InventoryItemsDialog

        dialog = InventoryItemsDialog(cfg=self.cfg)
        self.dialogs.append(dialog)
        with mock.patch.object(cd, "capture_roi", return_value=([1, 2, 30, 40], self.pixels, [1600, 1200])), \
                mock.patch.object(cd.QInputDialog, "getItem", return_value=("使用", True)):
            dialog.add_items()
        self.assertEqual(len(dialog.model.items), 1)
        reopened = InventoryItemsDialog(cfg=self.disk())
        self.dialogs.append(reopened)
        self.assertEqual(reopened.model.items, dialog.model.items)
        path = self.root / dialog.model.items[0]["template"]
        reopened.view.selectRow(0)
        with mock.patch.object(cd, "capture_roi", return_value=([1, 2, 30, 40], self.pixels, [1600, 1200])):
            reopened.recalibrate()
        self.assertTrue(path.exists())
        reopened.delete_item()
        self.assertTrue(path.exists())
        self.assertEqual(self.disk()["tasks"]["organize_bag"]["items"], [])


class QtOverlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = get_application(["qt-overlay-tests", "-platform", "offscreen"])

    def overlay(self, choices=None):
        rgb = np.full((160, 320, 3), [13, 47, 91], dtype=np.uint8)
        overlay = ro.RoiOverlay(dict(left=-320, top=80, width=320, height=160),
                                (320, 160), rgb.tobytes(), action_choices=choices)
        # 模拟 200% DPI：逻辑宽高减半，物理像素仍为 320×160。
        overlay.resize(160, 80)
        self.addCleanup(overlay.deleteLater)
        self.addCleanup(overlay.close)
        return overlay, rgb

    def draw(self, overlay, start, end):
        for event_type, point, buttons in ((QMouseEvent.Type.MouseButtonPress, start, Qt.MouseButton.LeftButton),
                                           (QMouseEvent.Type.MouseButtonRelease, end, Qt.MouseButton.NoButton)):
            event = QMouseEvent(event_type, QPointF(*point), QPointF(*point),
                                Qt.MouseButton.LeftButton, buttons, Qt.KeyboardModifier.NoModifier)
            self.app.sendEvent(overlay, event)

    def test_dpi_reverse_drag_clamp_and_original_bgr_match_legacy(self):
        overlay, rgb = self.overlay([("sell", "出售")])
        self.draw(overlay, (165, 85), (130, 60))
        self.assertEqual(overlay.result(), QDialog.DialogCode.Rejected)
        overlay.choose("sell")
        rect, crop, action = overlay.result_data(True)
        self.assertEqual(rect, [-60, 200, 60, 40])
        self.assertEqual(action, "sell")
        np.testing.assert_array_equal(crop, rgb[120:160, 260:320, ::-1])

    def test_tiny_retry_and_cancel_clear_selection_without_recapture(self):
        overlay, rgb = self.overlay([("use", "使用")])
        self.draw(overlay, (1, 1), (2, 2))
        self.assertIsNone(overlay.roi)
        self.draw(overlay, (5, 5), (15, 15))
        overlay.choose("retry")
        self.draw(overlay, (40, 45), (50, 55))
        overlay.choose("use")
        rect, crop, action = overlay.result_data(True)
        self.assertEqual(rect, [-240, 170, 20, 20])
        np.testing.assert_array_equal(crop, rgb[90:110, 80:100, ::-1])
        overlay.reject()
        self.assertEqual(overlay.result_data(True), (None, None, None))

    def test_escape_cancels_and_desktop_capture_is_single_frozen_frame(self):
        mon = dict(left=-320, top=80, width=320, height=160)
        rgb = np.zeros((160, 320, 3), dtype=np.uint8)

        def escape():
            overlay = next(w for w in self.app.topLevelWidgets() if isinstance(w, ro.RoiOverlay) and w.isVisible())
            QTest.keyClick(overlay, Qt.Key.Key_Escape)

        with mock.patch.object(ro.vision, "capture_monitor_rgb", return_value=(mon, (320, 160), rgb.tobytes())) as capture:
            QTimer.singleShot(10, escape)
            self.assertEqual(ro.select_roi_on_screen(None, with_crop=True), (None, None))
        capture.assert_called_once()


class QtCalibrationEntryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = get_application(["qt-calibration-entry-tests", "-platform", "offscreen"])

    def test_main_navigation_and_task_buttons_open_editors_without_tk(self):
        from mhxy.qtui.window import MainWindow

        with tempfile.TemporaryDirectory() as folder, \
                mock.patch("mhxy.qtui.window.scan_windows", return_value=[]):
            main = MainWindow(QSettings(str(Path(folder) / "ui.ini"), QSettings.Format.IniFormat))
            try:
                main.show_page("calibration")
                self.assertIsInstance(main.pages["calibration"], CalibrationPage)
                for name in ("sniper", "treasure_map", "escort", "secret_realm", "dungeon", "general"):
                    main.show_page(name)
                    page = main.pages[name]
                    if name == "general":
                        page.open_calibration()
                    else:
                        page._open_calibration()
                    self.assertIsInstance(page._calibration_dialog, cd.CalibrationDialog)
                    page._calibration_dialog.reject()
                    self.assertIsNone(page._calibration_dialog)
            finally:
                main.close()
                main.deleteLater()
                self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
