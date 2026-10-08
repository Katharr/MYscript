# -*- coding: utf-8 -*-
"""尺寸组共享能力门禁：只操作独立内存配置，不读取模板或操作窗口。"""
import copy
from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest import mock

from mhxy.core import calib_profiles as cp
from mhxy.core import config as cfg_mod


class CalibProfilesTests(unittest.TestCase):
    def setUp(self):
        self.cfg = copy.deepcopy(cfg_mod.DEFAULT_CONFIG)
        save_patch = mock.patch.object(
            cfg_mod, "save_config", side_effect=AssertionError("尺寸组纯逻辑不得写盘")
        )
        self.save = save_patch.start()
        self.addCleanup(save_patch.stop)

    def groups(self):
        first = cp.get_or_create(self.cfg, [1200, 900])
        second = cp.get_or_create(self.cfg, [1600, 1200])
        return first, second

    @staticmethod
    def setters(cfg, task, pid):
        return (
            lambda: cp.set_task_profile(cfg, task, pid),
            lambda: cp.set_template_profile(cfg, task, "entry", pid),
        )

    def test_valid_setters_only_change_ownership(self):
        first, second = self.groups()
        self.cfg["tasks"]["escort"]["templates"]["entry"] = "templates/tm_entry.png"
        before = copy.deepcopy(self.cfg)
        self.assertEqual(cp.set_task_profile(self.cfg, "escort", first), first)
        self.assertEqual(cp.set_template_profile(self.cfg, "escort", "entry", first), first)
        expected = copy.deepcopy(before)
        expected["tasks"]["escort"]["calib"] = {"profile": first}
        expected["tasks"]["escort"]["templates_calib"] = {"entry": first}
        self.assertEqual(self.cfg, expected)
        self.assertEqual(cp.active_id(self.cfg), second)
        self.assertEqual(cp.task_profile(self.cfg, "escort"), first)
        self.assertEqual(cp.template_profile(self.cfg, "escort", "entry"), first)
        self.save.assert_not_called()

    def test_integer_string_is_stored_as_integer(self):
        first, _ = self.groups()
        for setter in self.setters(self.cfg, "escort", str(first)):
            self.assertEqual(setter(), str(first))  # 保留原返回值约定
        self.assertEqual(cp.task_profile(self.cfg, "escort"), first)
        self.assertEqual(self.cfg["tasks"]["escort"]["templates_calib"]["entry"], first)

    def test_none_explicitly_clears_ownership(self):
        first, _ = self.groups()
        for setter in self.setters(self.cfg, "escort", first):
            setter()
        before = copy.deepcopy(self.cfg)
        for setter in self.setters(self.cfg, "escort", None):
            self.assertIsNone(setter())
        expected = copy.deepcopy(before)
        expected["tasks"]["escort"]["calib"]["profile"] = None
        expected["tasks"]["escort"]["templates_calib"]["entry"] = None
        self.assertEqual(self.cfg, expected)

    def test_invalid_assignment_preserves_existing_ownership(self):
        first, _ = self.groups()
        for setter in self.setters(self.cfg, "escort", first):
            setter()
        before = copy.deepcopy(self.cfg)
        for pid in (999, "999", "bad", "", 0, -1, True, False, 1.9, 1.0, [], {}):
            for index, setter in enumerate(self.setters(self.cfg, "escort", pid)):
                with self.subTest(pid=pid, setter=index):
                    with self.assertRaises(ValueError):
                        setter()
                    self.assertEqual(self.cfg, before)

    def test_invalid_assignment_does_not_create_task_blocks(self):
        cfg = {}
        for setter in self.setters(cfg, "new_task", 1):
            with self.assertRaises(ValueError):
                setter()
            self.assertEqual(cfg, {})

    def test_deleted_group_cannot_be_assigned_again(self):
        first, _ = self.groups()
        cp.delete(self.cfg, first)
        before = copy.deepcopy(self.cfg)
        for setter in self.setters(self.cfg, "escort", first):
            with self.assertRaises(ValueError):
                setter()
            self.assertEqual(self.cfg, before)

    def test_malformed_size_group_is_not_an_assignment_target(self):
        for size in (None, [], [100], ["bad", 900], [None, 900], [0, 900],
                     [-1, 900], "1200", 1200, {}, [float("inf"), 900]):
            with self.subTest(size=size):
                cfg = {"calib_profiles": {"items": [{"id": 1, "size": size}]}}
                before = copy.deepcopy(cfg)
                self.assertEqual(cp.items(cfg), [])
                for setter in self.setters(cfg, "escort", 1):
                    with self.assertRaises(ValueError):
                        setter()
                    self.assertEqual(cfg, before)

    def test_same_size_tolerance_reuses_and_syncs_mirror(self):
        first, _ = self.groups()
        self.assertEqual(cp.get_or_create(self.cfg, [1204, 896]), first)
        self.assertEqual(len(cp.items(self.cfg)), 2)
        self.assertEqual(cp.active_id(self.cfg), first)
        self.assertEqual(self.cfg["targets"]["base_size"], [1200, 900])

    def test_full_groups_reject_new_size_without_changes(self):
        self.groups()
        cp.get_or_create(self.cfg, [1900, 1400])
        before = copy.deepcopy(self.cfg)
        with self.assertRaises(ValueError):
            cp.get_or_create(self.cfg, [2200, 1600])
        self.assertEqual(self.cfg, before)
        self.assertEqual(len(cp.items(self.cfg)), cp.MAX_PROFILES)

    def test_invalid_activation_is_noop(self):
        self.groups()
        before = copy.deepcopy(self.cfg)
        self.assertFalse(cp.set_active(self.cfg, 999))
        self.assertEqual(self.cfg, before)

    def test_delete_clears_only_matching_pointers_and_preserves_assets(self):
        first, second = self.groups()
        tc = self.cfg["tasks"]["escort"]
        tc["regions"]["activity_list"] = [10, 20, 30, 40]
        tc["templates"]["entry"] = "templates/tm_entry.png"
        tc["calib"] = {"profile": str(first)}
        tc["templates_calib"] = {"entry": str(first), "other": second}
        self.cfg["tasks"]["teaming"]["templates"]["leader_id"] = "templates/tm_leader_id.png"
        regions = copy.deepcopy(tc["regions"])
        templates = {task: copy.deepcopy(block.get("templates"))
                     for task, block in self.cfg["tasks"].items()}
        cp.set_active(self.cfg, first)
        self.assertEqual(cp.delete(self.cfg, first), second)
        self.assertIsNone(cp.task_profile(self.cfg, "escort"))
        self.assertEqual(cp.template_profiles(self.cfg, "escort"), {"entry": None, "other": second})
        self.assertEqual(tc["regions"], regions)
        self.assertEqual({task: block.get("templates") for task, block in self.cfg["tasks"].items()},
                         templates)
        self.assertEqual(self.cfg["targets"]["base_size"], [1600, 1200])
        self.save.assert_not_called()

    def test_delete_last_group_clears_mirror(self):
        first = cp.get_or_create(self.cfg, [1200, 900])
        self.assertIsNone(cp.delete(self.cfg, first))
        self.assertEqual(cp.items(self.cfg), [])
        self.assertIsNone(cp.active_id(self.cfg))
        self.assertIsNone(cp.active_size(self.cfg))
        self.assertIsNone(self.cfg["targets"]["base_size"])

    def test_delete_with_stale_active_repairs_mirror(self):
        first, second = self.groups()
        self.cfg["calib_profiles"]["active"] = 999
        self.cfg["targets"]["base_size"] = [99, 99]
        self.assertEqual(cp.delete(self.cfg, second), first)
        self.assertEqual(self.cfg["calib_profiles"]["active"], first)
        self.assertEqual(self.cfg["targets"]["base_size"], [1200, 900])

    def test_missing_group_delete_is_noop(self):
        self.groups()
        before = copy.deepcopy(self.cfg)
        self.assertEqual(cp.delete(self.cfg, 999), cp.active_id(self.cfg))
        self.assertEqual(self.cfg, before)

    def test_reads_are_detached_and_legacy_size_fallback_is_preserved(self):
        self.cfg["targets"]["base_size"] = [1200, 900]
        before = copy.deepcopy(self.cfg)
        size = cp.active_size(self.cfg)
        size[0] = 1
        self.assertEqual(self.cfg, before)
        first, _ = self.groups()
        before = copy.deepcopy(self.cfg)
        cp.get(self.cfg, first)["size"][0] = 1
        cp.items(self.cfg)[0]["label"] = "changed"
        self.assertEqual(self.cfg, before)
        self.save.assert_not_called()
    def dialog(self):
        from mhxy.gui.calibrate_dialog import CalibrateDialog
        import customtkinter as ctk

        # 执行真实初始化的配置接线，但不创建 Tk 窗口或读取真实配置。
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(ctk.CTkToplevel, "__init__", return_value=None))
            stack.enter_context(mock.patch.object(cfg_mod, "load_config", return_value=self.cfg))
            for name in ("title", "geometry", "minsize", "configure", "transient", "after",
                         "_build", "_refresh"):
                stack.enter_context(mock.patch.object(CalibrateDialog, name))
            return CalibrateDialog(SimpleNamespace(fonts={}), task_name="escort")

    def test_calibration_save_preserves_new_region_and_ownership(self):
        first, _ = self.groups()
        dialog = self.dialog()
        self.assertIs(dialog.tc, self.cfg["tasks"]["escort"])
        dialog.tc["regions"]["activity_list"] = [10, 20, 30, 40]
        dialog._record_profile(dialog.cfg, dialog.task_name, None, first)
        saved = []
        with mock.patch.object(cfg_mod, "save_config", side_effect=lambda cfg: saved.append(copy.deepcopy(cfg))):
            dialog._save()
        self.assertEqual(len(saved), 1)
        self.assertEqual(cp.task_profile(saved[0], "escort"), first)
        self.assertEqual(saved[0]["tasks"]["escort"]["regions"]["activity_list"], [10, 20, 30, 40])

    def test_calibration_save_preserves_new_template_ownership(self):
        first, _ = self.groups()
        dialog = self.dialog()
        dialog.tc["templates"]["entry"] = "templates/tm_entry.png"
        dialog._record_profile(dialog.cfg, dialog.task_name, "entry", first)
        saved = []
        with mock.patch.object(cfg_mod, "save_config", side_effect=lambda cfg: saved.append(copy.deepcopy(cfg))):
            dialog._save()
        self.assertEqual(cp.template_profile(saved[0], "escort", "entry"), first)
        self.assertEqual(saved[0]["tasks"]["escort"]["templates"]["entry"], "templates/tm_entry.png")

    def test_calibration_save_does_not_revive_deleted_ownership(self):
        first, second = self.groups()
        cp.set_task_profile(self.cfg, "escort", first)
        cp.set_template_profile(self.cfg, "escort", "old", first)
        dialog = self.dialog()
        cp.delete(dialog.cfg, first)
        dialog._record_profile(dialog.cfg, dialog.task_name, "new", second)
        saved = []
        with mock.patch.object(cfg_mod, "save_config", side_effect=lambda cfg: saved.append(copy.deepcopy(cfg))):
            dialog._save()
        self.assertIsNone(cp.template_profile(saved[0], "escort", "old"))
        self.assertEqual(cp.template_profile(saved[0], "escort", "new"), second)
        self.assertFalse(dialog._record_profile(dialog.cfg, dialog.task_name, None, None))

    def test_setters_preserve_alias_used_by_inventory_save_sequence(self):
        first, _ = self.groups()
        tc = cfg_mod.task_config(self.cfg, "organize_bag")
        cfg_mod.set_task_config(self.cfg, "organize_bag", tc)
        cp.set_task_profile(self.cfg, "organize_bag", first)
        cp.set_template_profile(self.cfg, "organize_bag", "item", first)
        self.assertIs(self.cfg["tasks"]["organize_bag"], tc)
        cfg_mod.set_task_config(self.cfg, "organize_bag", tc)
        self.assertEqual(cp.task_profile(self.cfg, "organize_bag"), first)
        self.assertEqual(cp.template_profile(self.cfg, "organize_bag", "item"), first)
        self.save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
