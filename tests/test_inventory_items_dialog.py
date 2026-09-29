# -*- coding: utf-8 -*-
import copy
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import numpy as np

from mhxy.gui import inventory_items_dialog as mod


class _Label:
    def __init__(self):
        self.calls = []

    def configure(self, **kwargs):
        self.calls.append(kwargs)


class _DialogStub:
    def __init__(self, result):
        self.result = result


class InventoryDialogTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="inventory_test_")
        self.addCleanup(directory.cleanup)
        path_patch = mock.patch.object(mod.cfg_mod, "CONFIG_PATH", Path(directory.name) / "config.json")
        path_patch.start()
        self.addCleanup(path_patch.stop)
        save_patch = mock.patch.object(mod.cfg_mod, "save_config")
        self.saved_config = save_patch.start()
        self.addCleanup(save_patch.stop)
        self.dialog = mod.InventoryItemsDialog.__new__(mod.InventoryItemsDialog)
        self.dialog.cfg = {
            "tasks": {"organize_bag": {
                "items": [{"name": "旧物", "template": "templates/old.png", "action": "use"}],
                "templates_calib": {"旧物": 1},
            }},
        }
        self.dialog.tc = self.dialog.cfg["tasks"]["organize_bag"]
        self.dialog._adding = False
        self.dialog._thumbs = []
        self.dialog.status_lbl = _Label()
        self.dialog._refresh = mock.Mock()
        self.dialog._toast = mock.Mock()
        self.dialog.wait_window = mock.Mock()
        self.dialog.app = SimpleNamespace(fonts={})

    def test_next_item_name_skips_existing_numbers(self):
        items = [{"name": "物品 001"}, {"name": "物品 003"}]
        self.assertEqual(mod.next_item_name(items), "物品 002")
        self.assertEqual(mod.next_item_name(items + [{"name": "物品 002"}]), "物品 004")

    def test_apply_change_rolls_back_and_refreshes_on_save_error(self):
        before_cfg = copy.deepcopy(self.dialog.cfg)
        def change():
            self.dialog.tc["items"].append({"name": "临时", "action": "use"})
            raise RuntimeError("save failed")
        with mock.patch.object(self.dialog, "_save", side_effect=OSError("disk")):
            # change itself raises before _save; helper must still restore on any exception.
            ok = self.dialog._apply_items_change(change)
        self.assertFalse(ok)
        self.assertEqual(self.dialog.cfg, before_cfg)
        self.assertEqual(self.dialog.tc, before_cfg["tasks"]["organize_bag"])
        self.dialog._refresh.assert_called_once()
        self.dialog._toast.assert_called_once()

    def test_apply_change_success_returns_true(self):
        def change():
            self.dialog.tc["items"].append({"name": "新物", "action": "discard"})
        with mock.patch.object(self.dialog, "_save") as save:
            self.assertTrue(self.dialog._apply_items_change(change))
        save.assert_called_once()
        self.assertEqual(self.dialog.tc["items"][-1]["name"], "新物")

    def test_rename_migrates_profile_and_keeps_template_path(self):
        with mock.patch.object(mod.ctk, "CTkInputDialog", return_value=SimpleNamespace(get_input=lambda: "新名")):
            self.dialog._rename_item(0)
        item = self.dialog.tc["items"][0]
        self.assertEqual(item["template"], "templates/old.png")
        self.assertEqual(item["name"], "新名")
        self.assertNotIn("旧物", self.dialog.tc["templates_calib"])
        self.assertEqual(self.dialog.tc["templates_calib"]["新名"], 1)

    def test_rename_duplicate_keeps_old_item(self):
        self.dialog.tc["items"].append({"name": "另一个", "template": "x", "action": "use"})
        with mock.patch.object(mod.ctk, "CTkInputDialog", return_value=SimpleNamespace(get_input=lambda: "另一个")):
            self.dialog._rename_item(0)
        self.assertEqual(self.dialog.tc["items"][0]["name"], "旧物")
        self.dialog._refresh.assert_not_called()

    def test_existing_item_and_action_update_are_preserved(self):
        self.dialog._set_action(0, mod.ACTION_LABELS["discard"])
        self.assertEqual(self.dialog.tc["items"][0]["template"], "templates/old.png")
        self.assertEqual(self.dialog.tc["items"][0]["action"], "discard")

    def test_confirm_requires_explicit_action(self):
        dialog = mod.ItemActionDialog.__new__(mod.ItemActionDialog)
        dialog.action = None
        dialog.result = None
        dialog.status = _Label()
        dialog._accept(True)
        self.assertIsNone(dialog.result)
        self.assertTrue(dialog.status.calls)

    def test_add_single_cancel_does_not_append(self):
        crop = np.ones((4, 4, 3), dtype=np.uint8)
        results = [(None, None, None)]
        with mock.patch("mhxy.gui.calibrate_dialog.grab_roi_on_app", side_effect=results), \
             mock.patch.object(mod.vision, "save_image") as save:
            self.dialog._add_items(False)
        self.assertEqual(len(self.dialog.tc["items"]), 1)
        save.assert_not_called()
        self.assertFalse(self.dialog._adding)

    def test_batch_continue_then_complete_uses_fresh_actions(self):
        crop = np.ones((4, 4, 3), dtype=np.uint8)
        grabs = [([1, 2, 3, 4], crop, 1), ([1, 2, 3, 4], crop, 1)]
        confirmations = [_DialogStub(("discard", True)), _DialogStub(("use", False))]
        with mock.patch("mhxy.gui.calibrate_dialog.grab_roi_on_app", side_effect=grabs), \
             mock.patch.object(mod, "ItemActionDialog", side_effect=confirmations) as dialogs, \
             mock.patch.object(mod.vision, "save_image", return_value=True), \
             mock.patch.object(mod.cfg_mod, "save_config"):
            self.dialog._add_items(True)
        self.assertEqual([i["action"] for i in self.dialog.tc["items"]], ["use", "discard", "use"])
        self.assertEqual(dialogs.call_count, 2)
        self.assertFalse(self.dialog._adding)

    def test_batch_cancel_after_first_keeps_first(self):
        crop = np.ones((4, 4, 3), dtype=np.uint8)
        grabs = [([1, 2, 3, 4], crop, 1), (None, None, None)]
        with mock.patch("mhxy.gui.calibrate_dialog.grab_roi_on_app", side_effect=grabs), \
             mock.patch.object(mod, "ItemActionDialog", return_value=_DialogStub(("discard", True))), \
             mock.patch.object(mod.vision, "save_image", return_value=True), \
             mock.patch.object(mod.cfg_mod, "save_config"):
            self.dialog._add_items(True)
        self.assertEqual(len(self.dialog.tc["items"]), 2)
        self.assertEqual(self.dialog.tc["items"][-1]["action"], "discard")

    def test_batch_image_save_failure_does_not_append(self):
        crop = np.ones((4, 4, 3), dtype=np.uint8)
        with mock.patch("mhxy.gui.calibrate_dialog.grab_roi_on_app", return_value=([1, 2, 3, 4], crop, 1)), \
             mock.patch.object(mod, "ItemActionDialog", return_value=_DialogStub(("discard", True))), \
             mock.patch.object(mod.vision, "save_image", return_value=False), \
             mock.patch.object(mod.cfg_mod, "save_config"):
            self.dialog._add_items(True)
        self.assertEqual(len(self.dialog.tc["items"]), 1)
        self.assertFalse(self.dialog._adding)

    def test_batch_config_failure_keeps_only_saved_items_and_profiles(self):
        crop = np.ones((4, 4, 3), dtype=np.uint8)
        persisted = []

        def save(cfg):
            if persisted:
                raise OSError("disk full")
            persisted.append(copy.deepcopy(cfg))

        self.saved_config.side_effect = save
        with mock.patch("mhxy.gui.calibrate_dialog.grab_roi_on_app", return_value=([1, 2, 3, 4], crop, 2)), \
             mock.patch.object(mod, "ItemActionDialog", return_value=_DialogStub(("discard", True))), \
             mock.patch.object(mod.vision, "save_image", return_value=True):
            self.dialog._add_items(True)
        self.assertEqual(self.dialog.cfg, persisted[0])
        self.assertEqual(self.dialog.tc, persisted[0]["tasks"]["organize_bag"])
        self.assertEqual(len(self.dialog.tc["items"]), 2)
        self.assertEqual(self.dialog.tc["calib"]["profile"], 2)
        self.assertEqual(self.dialog.tc["templates_calib"], {"旧物": 1, "物品 001": 2})
        self.assertFalse(self.dialog._adding)

    def test_batch_second_image_failure_keeps_first_item(self):
        crop = np.ones((4, 4, 3), dtype=np.uint8)
        with mock.patch("mhxy.gui.calibrate_dialog.grab_roi_on_app", return_value=([1, 2, 3, 4], crop, 1)), \
             mock.patch.object(mod, "ItemActionDialog", return_value=_DialogStub(("use", True))), \
             mock.patch.object(mod.vision, "save_image", side_effect=[True, OSError("disk full")]):
            self.dialog._add_items(True)
        self.assertEqual(len(self.dialog.tc["items"]), 2)
        self.saved_config.assert_called_once()
        self.assertFalse(self.dialog._adding)

    def test_single_success_saves_selected_action_and_independent_path(self):
        crop = np.ones((4, 4, 3), dtype=np.uint8)
        with mock.patch("mhxy.gui.calibrate_dialog.grab_roi_on_app", return_value=([1, 2, 3, 4], crop, 2)) as grab, \
             mock.patch.object(mod, "ItemActionDialog", return_value=_DialogStub(("shop_sell", False))), \
             mock.patch.object(mod.vision, "save_image", return_value=True):
            self.dialog._add_items(False)
        item = self.dialog.tc["items"][-1]
        self.assertEqual(item["action"], "shop_sell")
        self.assertEqual(item["name"], "物品 001")
        self.assertRegex(item["template"], r"^templates/ob_[0-9a-f]{32}\.png$")
        self.assertEqual(grab.call_args.kwargs["alpha_windows"], (self.dialog.app, self.dialog))
        self.assertEqual(self.dialog.tc["templates_calib"][item["name"]], 2)
        self.saved_config.assert_called_once()

    def test_confirmation_cancel_never_saves_image_or_config(self):
        crop = np.ones((4, 4, 3), dtype=np.uint8)
        with mock.patch("mhxy.gui.calibrate_dialog.grab_roi_on_app", return_value=([1, 2, 3, 4], crop, 1)), \
             mock.patch.object(mod, "ItemActionDialog", return_value=_DialogStub(None)), \
             mock.patch.object(mod.vision, "save_image") as image:
            self.dialog._add_items(True)
        image.assert_not_called()
        self.saved_config.assert_not_called()
        self.assertEqual(len(self.dialog.tc["items"]), 1)

    def test_action_save_error_restores_previous_action(self):
        self.saved_config.side_effect = OSError("disk full")
        self.dialog._set_action(0, mod.ACTION_LABELS["discard"])
        self.assertEqual(self.dialog.tc["items"][0]["action"], "use")

    def test_reentry_guard_skips_nested_add(self):
        self.dialog._adding = True
        with mock.patch("mhxy.gui.calibrate_dialog.grab_roi_on_app") as grab:
            self.dialog._add_items(True)
        grab.assert_not_called()

    def test_center_does_not_steal_focus_during_batch(self):
        self.dialog._adding = True
        self.dialog.lift = mock.Mock()
        self.dialog.focus_force = mock.Mock()
        self.dialog._center_on_app()
        self.dialog.lift.assert_not_called()
        self.dialog.focus_force.assert_not_called()


if __name__ == "__main__":
    unittest.main()
