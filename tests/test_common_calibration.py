"""通用标定继承门禁：纯配置、独立尺寸和路径隔离，不操作真实游戏。"""

import copy
from pathlib import Path
import unittest
from unittest import mock

from mhxy.core import calib_profiles as profiles
from mhxy.core import calibration as cal
from mhxy.core import config as cfg_mod


class CommonCalibrationTests(unittest.TestCase):
    def setUp(self):
        self.cfg = copy.deepcopy(cfg_mod.DEFAULT_CONFIG)
        self.first = profiles.get_or_create(self.cfg, [1200, 900])
        self.second = profiles.get_or_create(self.cfg, [1600, 1200])
        patcher = mock.patch.object(cfg_mod, "save_config", side_effect=AssertionError("纯逻辑不得写盘"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def common_battle(self, path="templates/common_battle.png", pid=None):
        return cal.set_item(self.cfg, "common", "templates", "battle_indicator", path,
                            self.first if pid is None else pid)

    def test_no_common_preserves_old_task_values(self):
        tc = self.cfg["tasks"]["escort"]
        tc["templates"]["escort_battle"] = "templates/old_escort.png"
        tc["templates_calib"] = {"escort_battle": self.first}
        before = copy.deepcopy(self.cfg)
        item = cal.resolve_item(self.cfg, "escort", "templates", "escort_battle")
        self.assertEqual((item["value"], item["source"], item["profile"]),
                         ("templates/old_escort.png", "local", self.first))
        self.assertEqual(cal.effective_task_config(self.cfg, "escort")["templates"]["escort_battle"],
                         "templates/old_escort.png")
        self.assertEqual(self.cfg, before)

    def test_common_item_is_inherited_by_each_consumer(self):
        self.common_battle()
        for task, key in (("escort", "escort_battle"), ("treasure_map", "flag_battle"),
                          ("secret_realm", "sr_battle")):
            with self.subTest(task=task):
                item = cal.resolve_item(self.cfg, task, "templates", key)
                self.assertEqual(item["source"], "common")
                self.assertEqual(item["canonical_key"], "battle_indicator")
                self.assertEqual(item["value"], "templates/common_battle.png")
                self.assertEqual(item["profile"], self.first)

    def test_common_changes_are_live_without_copying_to_consumers(self):
        before_local = copy.deepcopy(self.cfg["tasks"]["escort"])
        self.common_battle()
        self.common_battle("templates/new_common.png")
        self.assertEqual(cal.effective_task_config(self.cfg, "escort")["templates"]["escort_battle"],
                         "templates/new_common.png")
        self.assertEqual(self.cfg["tasks"]["escort"], before_local)

    def test_local_override_is_per_item_and_task(self):
        self.common_battle()
        cal.set_item(self.cfg, "common", "templates", "activity_join", "templates/join.png", self.first)
        cal.set_item(self.cfg, "escort", "templates", "escort_battle", "templates/local.png", self.second)
        escort = cal.effective_task_config(self.cfg, "escort")
        self.assertEqual(escort["templates"]["escort_battle"], "templates/local.png")
        self.assertEqual(escort["templates"]["escort_join"], "templates/join.png")
        self.assertEqual(cal.resolve_item(self.cfg, "secret_realm", "templates", "sr_battle")["source"],
                         "common")

    def test_restore_only_removes_the_selected_override(self):
        self.common_battle()
        cal.set_item(self.cfg, "escort", "templates", "escort_battle", "templates/local.png", self.second)
        cal.set_item(self.cfg, "escort", "templates", "escort_join", "templates/local_join.png", self.second)
        common = copy.deepcopy(self.cfg["tasks"]["common"])
        item = cal.restore_common(self.cfg, "escort", "templates", "escort_battle")
        self.assertEqual((item["source"], item["profile"]), ("common", self.first))
        self.assertNotIn("escort_battle", self.cfg["tasks"]["escort"]["templates_calib"])
        self.assertEqual(cal.resolve_item(self.cfg, "escort", "templates", "escort_join")["source"], "local")
        self.assertEqual(self.cfg["tasks"]["common"], common)

    def test_inherit_marker_overrides_stale_local_value(self):
        self.common_battle()
        self.cfg["tasks"]["escort"]["templates"]["escort_battle"] = "templates/stale.png"
        self.cfg["tasks"]["escort"]["calibration_modes"] = {"templates": {"escort_battle": "inherit"}}
        self.assertEqual(cal.resolve_item(self.cfg, "escort", "templates", "escort_battle")["source"], "common")

    def test_full_window_override_and_inheritance_are_different(self):
        roi = [10, 20, 500, 400]
        cal.set_item(self.cfg, "common", "regions", "scene", roi, self.first)
        cal.set_item(self.cfg, "escort", "regions", "scene", None, self.second)
        item = cal.resolve_item(self.cfg, "escort", "regions", "scene")
        self.assertEqual((item["value"], item["source"], item["profile"]), (None, "local", self.second))
        restored = cal.restore_common(self.cfg, "escort", "regions", "scene")
        self.assertEqual((restored["value"], restored["source"]), (roi, "common"))

    def test_common_explicit_full_window_is_a_configured_value(self):
        cal.set_item(self.cfg, "common", "regions", "scene", None, self.first)
        self.assertEqual(cal.resolve_item(self.cfg, "escort", "regions", "scene")["source"], "common")

    def test_missing_values_are_not_materialized(self):
        before = copy.deepcopy(self.cfg)
        item = cal.resolve_item(self.cfg, "escort", "templates", "escort_battle")
        self.assertEqual((item["source"], item["value"]), ("missing", None))
        cal.effective_task_config(self.cfg, "escort")
        self.assertEqual(self.cfg, before)

    def test_damaged_local_path_never_falls_back_silently(self):
        self.common_battle()
        cal.set_item(self.cfg, "escort", "templates", "escort_battle", "missing/local.png", self.second)
        item = cal.resolve_item(self.cfg, "escort", "templates", "escort_battle")
        self.assertEqual((item["source"], item["value"]), ("local", "missing/local.png"))

    def test_resolved_roi_and_effective_config_do_not_alias_persistent_data(self):
        cal.set_item(self.cfg, "common", "regions", "activity_list", [1, 2, 300, 400], self.first)
        before = copy.deepcopy(self.cfg)
        item = cal.resolve_item(self.cfg, "escort", "regions", "activity_list")
        item["value"][0] = 999
        effective = cal.effective_task_config(self.cfg, "escort")
        effective["regions"]["activity_list"][1] = 999
        effective["loop"]["match_threshold"] = 0
        self.assertEqual(self.cfg, before)

    def test_size_uses_same_source_as_the_value(self):
        self.common_battle()
        profiles.set_task_profile(self.cfg, "escort", self.second)
        self.assertEqual(cal.size_for(self.cfg, "escort", "templates", "escort_battle"), [1200, 900])
        cal.set_item(self.cfg, "escort", "templates", "escort_battle", "templates/local.png", self.second)
        self.assertEqual(cal.size_for(self.cfg, "escort", "templates", "escort_battle"), [1600, 1200])

    def test_regions_have_independent_profiles(self):
        cal.set_item(self.cfg, "escort", "regions", "scene", [0, 0, 100, 100], self.first)
        cal.set_item(self.cfg, "escort", "regions", "activity_list", [0, 0, 200, 200], self.second)
        self.assertEqual(cal.size_for(self.cfg, "escort", "regions", "scene"), [1200, 900])
        self.assertEqual(cal.size_for(self.cfg, "escort", "regions", "activity_list"), [1600, 1200])

    def test_legacy_profile_fallback_and_explicitly_unrecorded_profile(self):
        self.cfg["tasks"]["escort"]["regions"]["scene"] = [0, 0, 100, 100]
        profiles.set_task_profile(self.cfg, "escort", self.first)
        self.assertEqual(cal.size_for(self.cfg, "escort", "regions", "scene"), [1200, 900])
        self.cfg["tasks"]["escort"]["regions_calib"] = {"scene": None}
        self.assertEqual(cal.size_for(self.cfg, "escort", "regions", "scene"), [1600, 1200])

    def test_profile_validation_precedes_any_mutation(self):
        before = copy.deepcopy(self.cfg)
        for pid in (999, "bad", True, False, 1.9, [], {}):
            with self.subTest(pid=pid):
                with self.assertRaises(ValueError):
                    cal.set_item(self.cfg, "new_task", "regions", "scene", None, pid)
                self.assertEqual(self.cfg, before)

    def test_unbound_items_keep_private_config_and_cannot_restore_common(self):
        tc = self.cfg["tasks"]["escort"]
        tc["templates"]["escort_silver"] = "templates/silver.png"
        self.assertEqual(cal.resolve_item(self.cfg, "escort", "templates", "escort_silver")["source"], "local")
        before = copy.deepcopy(self.cfg)
        with self.assertRaises(ValueError):
            cal.restore_common(self.cfg, "escort", "templates", "escort_silver")
        self.assertEqual(self.cfg, before)
        self.assertEqual(cal.template_path(self.cfg, "escort", "escort_silver"), "templates/silver.png")

    def test_different_confirmation_text_has_different_common_identity(self):
        self.assertEqual(cal.canonical_key("escort", "templates", "escort_confirm"), "dialog_confirm")
        self.assertEqual(cal.canonical_key("secret_realm", "templates", "sr_confirm"), "dialog_ok")

    def test_legacy_activity_is_preserved_as_shared_page_override(self):
        cal.set_item(self.cfg, "common", "templates", "daily_activity", "templates/common_daily.png", self.first)
        self.cfg["tasks"].setdefault("activity", {}).setdefault("templates", {})["daily_activity"] = "templates/old_daily.png"
        self.assertEqual(cal.resolve_item(self.cfg, "activity", "templates", "daily_activity")["value"],
                         "templates/old_daily.png")
        cal.restore_common(self.cfg, "activity", "templates", "daily_activity")
        self.assertEqual(cal.resolve_item(self.cfg, "activity", "templates", "daily_activity")["value"],
                         "templates/common_daily.png")

    def test_daily_common_overrides_legacy_shared_for_activity_consumers(self):
        cal.set_item(self.cfg, "activity", "templates", "daily_activity", "templates/legacy_daily.png", self.second)
        cal.set_item(self.cfg, "common", "templates", "daily_activity", "templates/common_daily.png", self.first)
        for task in ("escort", "treasure_map", "secret_realm", "taohaiqu"):
            with self.subTest(task=task):
                item = cal.resolve_item(self.cfg, task, "templates", "daily_activity")
                self.assertEqual((item["value"], item["source"], item["source_task"], item["profile"]),
                                 ("templates/common_daily.png", "common", "common", self.first))
                self.assertEqual(cal.size_for(self.cfg, task, "templates", "daily_activity"), [1200, 900])
        self.assertEqual(cal.resolve_item(self.cfg, "activity", "templates", "daily_activity")["value"],
                         "templates/legacy_daily.png")

    def test_daily_legacy_shared_fallback_keeps_own_profile_without_copying(self):
        cal.set_item(self.cfg, "activity", "templates", "daily_activity", "templates/legacy_daily.png", self.first)
        profiles.set_task_profile(self.cfg, "escort", self.second)
        before = copy.deepcopy(self.cfg)
        for task in ("escort", "treasure_map", "secret_realm", "taohaiqu"):
            with self.subTest(task=task):
                item = cal.resolve_item(self.cfg, task, "templates", "daily_activity")
                self.assertEqual((item["value"], item["source"], item["source_task"], item["profile"]),
                                 ("templates/legacy_daily.png", "common", "activity", self.first))
                self.assertEqual(cal.effective_task_config(self.cfg, task)["templates"]["daily_activity"],
                                 "templates/legacy_daily.png")
                self.assertEqual(cal.size_for(self.cfg, task, "templates", "daily_activity"), [1200, 900])
        self.assertEqual(self.cfg, before)

    def test_daily_per_activity_override_is_isolated_and_can_restore_shared(self):
        cal.set_item(self.cfg, "activity", "templates", "daily_activity", "templates/legacy_daily.png", self.first)
        cal.set_item(self.cfg, "escort", "templates", "daily_activity", "templates/escort_daily.png", self.second)
        item = cal.resolve_item(self.cfg, "escort", "templates", "daily_activity")
        self.assertEqual((item["source"], item["value"], item["profile"]),
                         ("local", "templates/escort_daily.png", self.second))
        self.assertEqual(cal.resolve_item(self.cfg, "secret_realm", "templates", "daily_activity")["value"],
                         "templates/legacy_daily.png")
        item = cal.restore_common(self.cfg, "escort", "templates", "daily_activity")
        self.assertEqual((item["source"], item["value"], item["profile"]),
                         ("common", "templates/legacy_daily.png", self.first))

    def test_daily_per_activity_default_paths_are_independent(self):
        paths = [cal.template_path(self.cfg, task, "daily_activity")
                 for task in ("escort", "treasure_map", "secret_realm", "taohaiqu", "activity", "common")]
        self.assertEqual(len(paths), len(set(paths)))

    def test_preexisting_shared_local_asset_is_not_overwritten_on_edit(self):
        path = "templates/manually_shared.png"
        cal.set_item(self.cfg, "escort", "templates", "daily_activity", path, self.first)
        cal.set_item(self.cfg, "secret_realm", "templates", "daily_activity", path, self.first)
        self.assertNotEqual(cal.template_path(self.cfg, "escort", "daily_activity"), path)

    def test_local_path_cannot_overwrite_common_asset_even_at_default_path(self):
        common_path = "templates/tm_escort_battle.png"
        self.common_battle(common_path)
        self.cfg["tasks"]["escort"]["templates"]["escort_battle"] = common_path
        path = cal.template_path(self.cfg, "escort", "escort_battle")
        self.assertNotEqual(path.casefold(), common_path.casefold())
        self.assertTrue(path.startswith("templates/"))

    def test_absolute_common_path_is_isolated_from_relative_alias(self):
        relative = "templates/tm_escort_battle.png"
        self.common_battle(str(cfg_mod.PROJECT_ROOT / relative))
        self.cfg["tasks"]["escort"]["templates"]["escort_battle"] = relative
        self.assertNotEqual(cal.template_path(self.cfg, "escort", "escort_battle"), relative)

    def test_common_path_cannot_overwrite_legacy_local_asset(self):
        path = "templates/tm_common_battle_indicator.png"
        self.cfg["tasks"]["escort"]["templates"]["escort_battle"] = path
        cal.set_item(self.cfg, "common", "templates", "battle_indicator", path, self.first)
        self.assertNotEqual(cal.template_path(self.cfg, "common", "battle_indicator"), path)

    def test_common_default_paths_are_unique_and_not_per_size(self):
        paths = [cal.template_path(self.cfg, "common", item[0])
                 for item in cal.COMMON_CALIBRATION["templates"]]
        self.assertEqual(len(paths), len(set(paths)))
        for path in paths:
            self.assertNotIn("1200", path)
            self.assertNotIn("1600", path)
            self.assertTrue(Path(path).name.startswith("tm_common_"))

    def test_common_items_with_manually_aliased_paths_are_separated_on_edit(self):
        path = "templates/tm_common_battle_indicator.png"
        self.common_battle(path)
        cal.set_item(self.cfg, "common", "templates", "activity_join", path, self.first)
        self.assertNotEqual(cal.template_path(self.cfg, "common", "battle_indicator"), path)

    def test_legacy_shared_activity_path_is_not_reused_by_local_override(self):
        path = "templates/tm_escort_join.png"
        self.cfg["tasks"].setdefault("activity", {}).setdefault("templates", {})["daily_activity"] = path
        self.cfg["tasks"]["escort"]["templates"]["escort_join"] = path
        self.assertNotEqual(cal.template_path(self.cfg, "escort", "escort_join"), path)


if __name__ == "__main__":
    unittest.main()
