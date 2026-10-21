"""通用标定运行回归：真实像素匹配、跨尺寸坐标和上下文只读解析。"""

import copy
from types import SimpleNamespace
import unittest
from unittest import mock

import cv2
import numpy as np

from mhxy.core import activity, calibration, calibration_runtime, list_row
from mhxy.core.context import TaskContext
from mhxy.tasks.escort import EscortTask
from mhxy.tasks.secret_realm import SecretRealmTask
from mhxy.tasks.taohaiqu import TaohaiquTask
from mhxy.tasks.treasure_map import TreasureMapTask


def configured():
    return {
        "calib_profiles": {
            "items": [{"id": 1, "size": [100, 100]}, {"id": 2, "size": [200, 200]}],
            "active": 2,
        },
        "tasks": {
            "common": {
                "templates": {"battle_indicator": "common-battle.png"},
                "templates_calib": {"battle_indicator": 1},
                "regions": {"activity_list": [10, 15, 20, 25]},
                "regions_calib": {"activity_list": 1},
            },
            "escort": {"calib": {"profile": 2}},
        },
    }


class CommonRuntimeTests(unittest.TestCase):
    def test_context_resolves_shared_preflight_values_without_persisting_them(self):
        ctx = TaskContext.__new__(TaskContext)
        ctx.cfg = configured()
        before = copy.deepcopy(ctx.cfg)
        effective = ctx.task_cfg("escort")
        self.assertEqual(effective["templates"]["escort_battle"], "common-battle.png")
        self.assertEqual(effective["regions"]["activity_list"], [10, 15, 20, 25])
        effective["templates"]["escort_battle"] = "changed.png"
        self.assertEqual(ctx.cfg, before)

    def test_common_battle_matches_its_own_profile_and_returns_current_pixels(self):
        cfg = configured()
        ctx = SimpleNamespace(cfg=cfg, window=SimpleNamespace(rect=lambda: [100, 200, 200, 200]))
        task = EscortTask()
        template = np.random.default_rng(61).integers(0, 256, (12, 14, 3), dtype=np.uint8)
        base = np.zeros((100, 100, 3), dtype=np.uint8)
        base[40:52, 28:42] = template
        scene = cv2.resize(base, (200, 200), interpolation=cv2.INTER_NEAREST)
        task.flags = {"escort_battle": template}
        task._scene_rect(ctx, {})
        hit = task._match_scene(scene, [100, 200, 200, 200], "escort_battle", 0.99)
        self.assertIsNotNone(hit)
        self.assertEqual(hit[:2], (170, 292))
        self.assertTrue(task._present(scene, "escort_battle", 0.99))

    def test_region_uses_common_profile_then_local_profile_after_override(self):
        cfg = configured()
        ctx = SimpleNamespace(cfg=cfg, window=SimpleNamespace(rect=lambda: [100, 200, 200, 200]))
        value = [10, 15, 20, 25]
        self.assertEqual(calibration_runtime.region_rect(ctx, "escort", "activity_list", value),
                         [120, 230, 40, 50])
        cfg["tasks"]["escort"].update(regions={"activity_list": value},
                                        regions_calib={"activity_list": 2})
        self.assertEqual(calibration_runtime.region_rect(ctx, "escort", "activity_list", value),
                         [110, 215, 20, 25])
        self.assertEqual(calibration_runtime.region_rect(ctx, "escort", "scene", None),
                         [100, 200, 200, 200])

    def test_daily_navigation_uses_activity_override_then_restored_common_asset(self):
        ctx = TaskContext.__new__(TaskContext)
        ctx.cfg = configured()
        ctx.window = SimpleNamespace(rect=lambda: [100, 200, 200, 200])
        ctx.mouse = mock.Mock()
        ctx.should_stop = lambda: False
        ctx.log = mock.Mock()
        calibration.set_item(ctx.cfg, "common", "templates", "daily_activity", "common-daily.png", 1)
        calibration.set_item(ctx.cfg, "escort", "templates", "daily_activity", "escort-daily.png", 2)
        scene = mock.Mock()
        scene.match.return_value = (10, 20, 0.99)
        scene.to_screen.return_value = (110, 220)
        for expected_path, expected_size in [("escort-daily.png", [200, 200]),
                                             ("common-daily.png", [100, 100])]:
            with mock.patch.object(activity.vision, "load_template", return_value=object()) as load, \
                 mock.patch.object(activity.win_mod, "grab_scene", return_value=scene) as grab:
                self.assertTrue(activity.select_daily(ctx, mock.Mock(), "escort"))
                load.assert_called_once_with(expected_path)
                grab.assert_called_once_with([100, 200, 200, 200], expected_size)
            calibration.restore_common(ctx.cfg, "escort", "templates", "daily_activity")

    def test_broken_daily_override_warns_without_loading_working_common_template(self):
        ctx = TaskContext.__new__(TaskContext)
        ctx.cfg = configured()
        ctx.mouse = mock.Mock()
        ctx.should_stop = lambda: False
        ctx.log = mock.Mock()
        calibration.set_item(ctx.cfg, "common", "templates", "daily_activity", "good-common.png", 1)
        calibration.set_item(ctx.cfg, "escort", "templates", "daily_activity", "broken-local.png", 2)
        with mock.patch.object(activity.vision, "load_template", side_effect=lambda path:
                               object() if path == "good-common.png" else None) as load, \
             mock.patch.object(activity.win_mod, "grab_scene") as grab:
            self.assertFalse(activity.select_daily(ctx, mock.Mock(), "escort"))
        load.assert_called_once_with("broken-local.png")
        grab.assert_not_called()
        ctx.mouse.click.assert_not_called()
        ctx.log.assert_called_once()
        warning = ctx.log.call_args
        self.assertEqual(warning.kwargs, {"level": "warn"})
        self.assertIn("本活动标定", warning.args[0])
        self.assertIn("broken-local.png", warning.args[0])
        self.assertIn("已跳过日常活动导航", warning.args[0])

    def test_daily_asset_override_preserves_shared_navigation_threshold_and_timing(self):
        ctx = TaskContext.__new__(TaskContext)
        ctx.cfg = configured()
        ctx.cfg["tasks"]["activity"] = {"loop": {"match_threshold": 0.73,
                                                   "after_click_wait_sec": 0.91}}
        ctx.cfg["tasks"]["escort"]["loop"] = {"match_threshold": 0.99,
                                                  "after_click_wait_sec": 8.0}
        calibration.set_item(ctx.cfg, "escort", "templates", "daily_activity", "local-daily.png", 2)
        ctx.window = SimpleNamespace(rect=lambda: [100, 200, 200, 200])
        ctx.mouse = mock.Mock()
        ctx.should_stop = lambda: False
        ctx.log = mock.Mock()
        template = object()
        scene = mock.Mock()
        scene.match.return_value = (10, 20, 0.99)
        scene.to_screen.return_value = (110, 220)
        wait = mock.Mock()
        with mock.patch.object(activity.vision, "load_template", return_value=template), \
             mock.patch.object(activity.win_mod, "grab_scene", return_value=scene):
            self.assertTrue(activity.select_daily(ctx, wait, "escort"))
        scene.match.assert_called_once_with(template, 0.73)
        wait.assert_called_once_with(0.91)

    def test_uncalibrated_optional_daily_navigation_is_quiet(self):
        ctx = TaskContext.__new__(TaskContext)
        ctx.cfg = configured()
        ctx.should_stop = lambda: False
        ctx.log = mock.Mock()
        with mock.patch.object(activity.vision, "load_template") as load:
            self.assertFalse(activity.select_daily(ctx, mock.Mock(), "escort"))
        load.assert_not_called()
        ctx.log.assert_not_called()

    def test_join_calibrated_separately_stays_bound_to_nearest_card_button(self):
        rng = np.random.default_rng(23)
        anchor = rng.integers(0, 256, (12, 12, 3), dtype=np.uint8)
        join_base = rng.integers(0, 256, (12, 12, 3), dtype=np.uint8)
        join = cv2.resize(join_base, (24, 24), interpolation=cv2.INTER_NEAREST)
        scene = np.zeros((200, 200, 3), dtype=np.uint8)
        scene[70:94, 40:64] = cv2.resize(anchor, (24, 24), interpolation=cv2.INTER_NEAREST)
        scene[70:94, 120:144] = join
        scene[70:94, 166:190] = join
        hit = list_row.locate_card(scene, [100, 200, 200, 200], anchor, join,
                                   match_threshold=0.99, calib_size=[100, 100],
                                   cfg={"join_min_score": 0.99},
                                   window_rect=[100, 200, 200, 200],
                                   join_calib_size=[200, 200])
        self.assertIsNotNone(hit)
        self.assertEqual((hit.anchor_x, hit.anchor_y), (152, 282))
        self.assertEqual((hit.join_x, hit.join_y), (232, 282))

    def test_bag_fallback_tracks_loaded_asset_profile_and_current_window(self):
        marker = np.random.default_rng(82).integers(0, 256, (12, 14, 3), dtype=np.uint8)
        base = np.zeros((100, 100, 3), dtype=np.uint8)
        base[40:52, 28:42] = marker
        scene = cv2.resize(base, (200, 200), interpolation=cv2.INTER_NEAREST)
        current_marker = cv2.resize(marker, (28, 24), interpolation=cv2.INTER_NEAREST)
        for local_path in (None, "dedicated.png", "missing.png"):
            with self.subTest(local_path=local_path):
                cfg = configured()
                cfg["tasks"]["organize_bag"] = {
                    "templates": {"sort_button": "sort.png"},
                    "templates_calib": {"sort_button": 1},
                }
                cfg["tasks"]["treasure_map"] = {
                    "templates": {"flag_bag_open": local_path},
                    "templates_calib": {"flag_bag_open": 2},
                }
                ctx = TaskContext.__new__(TaskContext)
                ctx.cfg = cfg
                ctx.window = SimpleNamespace(rect=lambda: [100, 200, 200, 200])
                task = TreasureMapTask()
                # Previously scanned character had a different size. The bag
                # check must bind to the current character before matching.
                task._calibration_ctx = SimpleNamespace(
                    cfg=cfg, window=SimpleNamespace(rect=lambda: [0, 0, 100, 100]))

                def load(path):
                    return {"sort.png": marker, "dedicated.png": current_marker}.get(path)

                with mock.patch("mhxy.tasks.treasure_map.vision.load_template", side_effect=load):
                    task.flags = {"flag_bag_open": task._load_bag_template(ctx)}
                expected_source = (("treasure_map", "flag_bag_open") if local_path == "dedicated.png"
                                   else ("organize_bag", "sort_button"))
                with mock.patch("mhxy.tasks.treasure_map.win_mod.grab", return_value=scene), \
                     mock.patch.object(calibration_runtime, "item_size", wraps=calibration_runtime.item_size) as size:
                    self.assertTrue(task._bag_visible(ctx, 0.99))
                    size.assert_called_once_with(ctx, expected_source[0], "templates", expected_source[1])

    def test_subregion_matching_uses_current_context_after_scanning_another_window(self):
        marker = np.random.default_rng(83).integers(0, 256, (12, 14, 3), dtype=np.uint8)
        base = np.zeros((100, 100, 3), dtype=np.uint8)
        base[40:52, 28:42] = marker
        scene = cv2.resize(base, (200, 200), interpolation=cv2.INTER_NEAREST)
        for cls, key in [(SecretRealmTask, "sr_dungeon_enter"), (TaohaiquTask, "thq_enter")]:
            with self.subTest(task=cls.name):
                cfg = configured()
                cfg["tasks"][cls.name] = {"templates": {key: "entry.png"}, "templates_calib": {key: 1}}
                ctx = SimpleNamespace(cfg=cfg, window=SimpleNamespace(rect=lambda: [100, 200, 200, 200]))
                task = cls()
                task.flags = {key: marker}
                task._calibration_ctx = SimpleNamespace(
                    cfg=cfg, window=SimpleNamespace(rect=lambda: [0, 0, 100, 100]))
                with mock.patch("mhxy.core.window.grab", return_value=scene):
                    hit = task._match_subregion(ctx, {}, marker, 0.99, (0, 1), (0, 1))
                self.assertIsNotNone(hit)
                self.assertEqual(hit[:2], (170, 292))


if __name__ == "__main__":
    unittest.main()
