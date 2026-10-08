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
        self.root = Path(directory.name)
        backup_patch = mock.patch.object(mod.cfg_mod, "CONFIG_BACKUP_DIR", self.root / "config_backups")
        backup_patch.start()
        self.addCleanup(backup_patch.stop)
        root_patch = mock.patch.object(mod.cfg_mod, "PROJECT_ROOT", self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        self.real_save_config = mod.cfg_mod.save_config
        save_patch = mock.patch.object(mod.cfg_mod, "save_config")
        self.saved_config = save_patch.start()
        self.addCleanup(save_patch.stop)
        self.dialog = mod.InventoryItemsDialog.__new__(mod.InventoryItemsDialog)
        self.dialog.cfg = {
            "calib_profiles": {"items": [
                {"id": 1, "size": [1200, 900]},
                {"id": 2, "size": [1600, 1200]},
            ], "active": 1},
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
        with mock.patch("mhxy.gui.calibrate_dialog.grab_roi_on_app", side_effect=results) as grab, \
             mock.patch.object(mod.vision, "encode_png") as save:
            self.dialog._add_items(False)
        self.assertFalse(grab.call_args.kwargs["save_profile"])
        self.assertEqual(len(self.dialog.tc["items"]), 1)
        save.assert_not_called()
        self.assertFalse(self.dialog._adding)

    def test_batch_continue_then_complete_uses_fresh_actions(self):
        crop = np.ones((4, 4, 3), dtype=np.uint8)
        grabs = [([1, 2, 3, 4], crop, 1), ([1, 2, 3, 4], crop, 1)]
        confirmations = [_DialogStub(("discard", True)), _DialogStub(("use", False))]
        with mock.patch("mhxy.gui.calibrate_dialog.grab_roi_on_app", side_effect=grabs), \
             mock.patch.object(mod, "ItemActionDialog", side_effect=confirmations) as dialogs, \
             mock.patch.object(mod.vision, "encode_png", return_value=b"png"), \
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
             mock.patch.object(mod.vision, "encode_png", return_value=b"png"), \
             mock.patch.object(mod.cfg_mod, "save_config"):
            self.dialog._add_items(True)
        self.assertEqual(len(self.dialog.tc["items"]), 2)
        self.assertEqual(self.dialog.tc["items"][-1]["action"], "discard")

    def test_batch_image_save_failure_does_not_append(self):
        crop = np.ones((4, 4, 3), dtype=np.uint8)
        with mock.patch("mhxy.gui.calibrate_dialog.grab_roi_on_app", return_value=([1, 2, 3, 4], crop, 1)), \
             mock.patch.object(mod, "ItemActionDialog", return_value=_DialogStub(("discard", True))), \
             mock.patch.object(mod.vision, "encode_png", return_value=None), \
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
             mock.patch.object(mod.vision, "encode_png", return_value=b"png"):
            self.dialog._add_items(True)
        self.assertEqual(self.dialog.cfg, persisted[0])
        self.assertEqual(self.dialog.tc, persisted[0]["tasks"]["organize_bag"])
        self.assertEqual(len(self.dialog.tc["items"]), 2)
        self.assertEqual(self.dialog.tc["calib"]["profile"], 2)
        self.assertEqual(self.dialog.tc["templates_calib"], {"旧物": 1, "物品 001": 2})
        self.assertEqual(list((self.root / "templates").glob("*.png")),
                         [self.root / self.dialog.tc["items"][-1]["template"]])
        self.assertFalse(self.dialog._adding)

    def test_batch_second_image_failure_keeps_first_item(self):
        crop = np.ones((4, 4, 3), dtype=np.uint8)
        with mock.patch("mhxy.gui.calibrate_dialog.grab_roi_on_app", return_value=([1, 2, 3, 4], crop, 1)), \
             mock.patch.object(mod, "ItemActionDialog", return_value=_DialogStub(("use", True))), \
             mock.patch.object(mod.vision, "encode_png", side_effect=[b"png", OSError("encode")]):
            self.dialog._add_items(True)
        self.assertEqual(len(self.dialog.tc["items"]), 2)
        self.saved_config.assert_called_once()
        self.assertFalse(self.dialog._adding)

    def test_single_success_saves_selected_action_and_independent_path(self):
        crop = np.ones((4, 4, 3), dtype=np.uint8)
        with mock.patch("mhxy.gui.calibrate_dialog.grab_roi_on_app", return_value=([1, 2, 3, 4], crop, 2)) as grab, \
             mock.patch.object(mod, "ItemActionDialog", return_value=_DialogStub(("shop_sell", False))), \
             mock.patch.object(mod.vision, "encode_png", return_value=b"png"):
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
             mock.patch.object(mod.vision, "encode_png") as image:
            self.dialog._add_items(True)
        image.assert_not_called()
        self.saved_config.assert_not_called()
        self.assertEqual(len(self.dialog.tc["items"]), 1)

    def test_draft_profile_changes_are_discarded_before_commit(self):
        crop = np.ones((4, 4, 3), dtype=np.uint8)
        before = copy.deepcopy(self.dialog.cfg)

        def capture(app, draft, prompt, **kwargs):
            self.assertIsNot(draft, self.dialog.cfg)
            self.assertFalse(kwargs["save_profile"])
            mod.calib.delete(draft, 1)
            pid = mod.calib.get_or_create(draft, [1900, 1400])
            return [1, 2, 3, 4], crop, pid

        for result in (None, ("use", False)):
            with self.subTest(confirm=result), \
                    mock.patch("mhxy.gui.calibrate_dialog.grab_roi_on_app", side_effect=capture), \
                    mock.patch.object(mod, "ItemActionDialog", return_value=_DialogStub(result)), \
                    mock.patch.object(mod.vision, "encode_png", return_value=None):
                self.dialog._add_items(False)
                self.assertEqual(self.dialog.cfg, before)
                self.assertIs(self.dialog.tc, self.dialog.cfg["tasks"]["organize_bag"])
                self.saved_config.assert_not_called()
                self.dialog._refresh.assert_not_called()
                self.assertFalse((self.root / "templates").exists())

    def test_success_writes_image_before_saving_complete_draft_and_publishing(self):
        before = copy.deepcopy(self.dialog.cfg)
        complete = copy.deepcopy(mod.cfg_mod.DEFAULT_CONFIG)
        complete.update(before)
        self.dialog.cfg = complete
        self.dialog.tc = complete["tasks"]["organize_bag"]
        before = copy.deepcopy(complete)

        def capture(app, draft, prompt, **kwargs):
            mod.calib.delete(draft, 1)
            pid = mod.calib.get_or_create(draft, [1900, 1400])
            return [1, 2, 3, 4], SimpleNamespace(size=12), pid

        def save(draft):
            self.assertEqual(self.dialog.cfg, before)
            self.assertEqual(set(draft), set(mod.cfg_mod.DEFAULT_CONFIG))
            item = draft["tasks"]["organize_bag"]["items"][-1]
            self.assertEqual((self.root / item["template"]).read_bytes(), b"png")
            self.assertEqual(draft["targets"]["base_size"], [1900, 1400])
            self.assertIsNone(mod.calib.template_profile(draft, "organize_bag", "旧物"))
            self.assertEqual(mod.calib.template_profile(draft, "organize_bag", item["name"]),
                             mod.calib.active_id(draft))

        self.saved_config.side_effect = save
        with mock.patch("mhxy.gui.calibrate_dialog.grab_roi_on_app", side_effect=capture), \
                mock.patch.object(mod, "ItemActionDialog", return_value=_DialogStub(("use", False))), \
                mock.patch.object(mod.vision, "encode_png", return_value=b"png"):
            self.dialog._add_items(False)
        self.saved_config.assert_called_once()
        self.assertIs(self.dialog.tc, self.dialog.cfg["tasks"]["organize_bag"])
        self.assertIs(self.dialog.cfg, self.saved_config.call_args.args[0])
        self.dialog._refresh.assert_called_once()

    def test_config_failure_restores_collision_or_removes_new_image(self):
        path = self.root / "templates/ob_test.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        before = copy.deepcopy(self.dialog.cfg)
        self.saved_config.side_effect = OSError("save")
        for existing in (False, True):
            with self.subTest(existing=existing):
                if existing:
                    path.write_bytes(b"old")
                with mock.patch("mhxy.gui.calibrate_dialog.grab_roi_on_app",
                                return_value=([1, 2, 3, 4], SimpleNamespace(size=12), 1)), \
                        mock.patch.object(mod, "ItemActionDialog", return_value=_DialogStub(("use", False))), \
                        mock.patch.object(mod, "uuid4", return_value=SimpleNamespace(hex="test")), \
                        mock.patch.object(mod.vision, "encode_png", return_value=b"png"):
                    self.dialog._add_items(False)
                self.assertEqual(path.read_bytes() if path.exists() else None,
                                 b"old" if existing else None)
                self.assertEqual(self.dialog.cfg, before)
                self.dialog._refresh.assert_not_called()
                self.assertFalse(any(call.args[1] == mod.T.SUCCESS
                                     for call in self.dialog._toast.call_args_list))

    def test_cleanup_failure_is_reported_without_publishing(self):
        before = copy.deepcopy(self.dialog.cfg)
        self.saved_config.side_effect = OSError("save")
        with mock.patch("mhxy.gui.calibrate_dialog.grab_roi_on_app",
                        return_value=([1, 2, 3, 4], SimpleNamespace(size=12), 1)), \
                mock.patch.object(mod, "ItemActionDialog", return_value=_DialogStub(("use", False))), \
                mock.patch.object(mod.vision, "encode_png", return_value=b"png"), \
                mock.patch.object(Path, "unlink", side_effect=OSError("cleanup")):
            self.dialog._add_items(False)
        self.assertEqual(self.dialog.cfg, before)
        self.dialog._refresh.assert_not_called()
        self.assertIn("恢复失败", self.dialog._toast.call_args.args[0])

    def test_action_save_error_restores_previous_action(self):
        self.saved_config.side_effect = OSError("disk full")
        self.dialog._set_action(0, mod.ACTION_LABELS["discard"])
        self.assertEqual(self.dialog.tc["items"][0]["action"], "use")

    def test_delete_saves_complete_draft_before_publishing(self):
        complete = copy.deepcopy(mod.cfg_mod.DEFAULT_CONFIG)
        complete.update(self.dialog.cfg)
        self.dialog.cfg = complete
        self.dialog.tc = complete["tasks"]["organize_bag"]
        self.dialog.tc["items"].append(
            {"name": "保留物", "template": "templates/keep.png", "action": "discard"})
        self.dialog.tc["templates_calib"]["保留物"] = 2
        before = copy.deepcopy(complete)
        old_cfg, old_tc = self.dialog.cfg, self.dialog.tc
        path = self.root / "templates/old.png"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"old template")

        def save(draft):
            self.assertIs(self.dialog.cfg, old_cfg)
            self.assertIs(self.dialog.tc, old_tc)
            self.assertEqual(old_cfg, before)
            self.assertIsNot(draft, old_cfg)
            expected = copy.deepcopy(before)
            expected["tasks"]["organize_bag"]["items"].pop(0)
            expected["tasks"]["organize_bag"]["templates_calib"].pop("旧物")
            self.assertEqual(draft, expected)
            self.dialog._refresh.assert_not_called()

        self.saved_config.side_effect = save
        self.dialog._delete_item(0)
        self.saved_config.assert_called_once()
        self.assertIs(self.dialog.cfg, self.saved_config.call_args.args[0])
        self.assertIs(self.dialog.tc, self.dialog.cfg["tasks"]["organize_bag"])
        self.assertEqual(path.read_bytes(), b"old template")
        self.dialog._refresh.assert_called_once()
        self.dialog._toast.assert_called_once_with("已删除：旧物", mod.T.TEXT_DIM)

    def test_delete_failure_preserves_original_objects_and_all_references(self):
        old_cfg, old_tc = self.dialog.cfg, self.dialog.tc
        old_items = old_tc["items"]
        old_profiles = old_tc["templates_calib"]
        before = copy.deepcopy(old_cfg)
        self.saved_config.side_effect = OSError("disk full")
        self.dialog._delete_item(0)
        self.assertIs(self.dialog.cfg, old_cfg)
        self.assertIs(self.dialog.tc, old_tc)
        self.assertIs(self.dialog.tc["items"], old_items)
        self.assertIs(self.dialog.tc["templates_calib"], old_profiles)
        self.assertEqual(old_cfg, before)
        self.dialog._refresh.assert_not_called()
        self.assertIn("disk full", self.dialog._toast.call_args.args[0])
        self.assertEqual(self.dialog._toast.call_args.args[1], mod.T.DANGER)
        # 重试仍删除同一项，不丢失后续操作。
        self.saved_config.side_effect = None
        self.dialog._delete_item(0)
        self.assertEqual(self.dialog.tc["items"], [])
        self.assertEqual(self.dialog.tc["templates_calib"], {})
        self.assertEqual(old_cfg, before)

    def test_delete_detached_task_config_keeps_defaults_and_other_edits(self):
        self.dialog.tc = mod.cfg_mod.task_config(self.dialog.cfg, "organize_bag")
        self.dialog.tc["loop"]["custom_value"] = 7
        detached = self.dialog.tc
        before = copy.deepcopy(self.dialog.cfg)
        self.dialog._delete_item(0)
        self.assertEqual(self.dialog.tc["items"], [])
        self.assertEqual(self.dialog.tc["loop"]["custom_value"], 7)
        self.assertEqual(self.dialog.tc["regions"], detached["regions"])
        self.assertIs(self.dialog.tc, self.dialog.cfg["tasks"]["organize_bag"])
        self.assertEqual(detached["items"], before["tasks"]["organize_bag"]["items"])

    def test_delete_invalid_index_has_no_side_effects(self):
        before = copy.deepcopy(self.dialog.cfg)
        for idx in (-1, 1, 99):
            with self.subTest(idx=idx):
                self.dialog._delete_item(idx)
        self.assertEqual(self.dialog.cfg, before)
        self.saved_config.assert_not_called()
        self.dialog._refresh.assert_not_called()
        self.dialog._toast.assert_not_called()

    def test_delete_without_profile_mapping_does_not_create_one(self):
        self.dialog.tc.pop("templates_calib")
        self.dialog._delete_item(0)
        self.assertEqual(self.dialog.tc["items"], [])
        self.assertNotIn("templates_calib", self.dialog.tc)
        self.saved_config.assert_called_once()

    def test_delete_real_save_preserves_config_on_atomic_replace_failure(self):
        complete = copy.deepcopy(mod.cfg_mod.DEFAULT_CONFIG)
        complete.update(self.dialog.cfg)
        self.dialog.cfg = complete
        self.dialog.tc = complete["tasks"]["organize_bag"]
        old_cfg, old_tc = self.dialog.cfg, self.dialog.tc
        # 调用真实出口，不绕过 save_config；所有路径均在独立临时目录。
        self.saved_config.side_effect = self.real_save_config
        mod.cfg_mod.save_config(complete)
        disk_before = mod.cfg_mod.CONFIG_PATH.read_bytes()
        with mock.patch.object(mod.cfg_mod.os, "replace", side_effect=OSError("replace")):
            self.dialog._delete_item(0)
        self.assertEqual(mod.cfg_mod.CONFIG_PATH.read_bytes(), disk_before)
        self.assertIs(self.dialog.cfg, old_cfg)
        self.assertIs(self.dialog.tc, old_tc)
        self.dialog._refresh.assert_not_called()
        self.dialog._delete_item(0)
        persisted = mod.cfg_mod.load_config()
        self.assertEqual(persisted["tasks"]["organize_bag"]["items"], [])
        self.assertEqual(persisted["tasks"]["organize_bag"]["templates_calib"], {})
        self.assertTrue((self.root / "config_backups").is_dir())

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
