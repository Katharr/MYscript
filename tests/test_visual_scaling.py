# -*- coding: utf-8 -*-
"""离线缩放门禁：只用合成像素、假截图，无窗口激活或真实输入。"""
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from mhxy.core import list_row, vision, window


def pattern(seed, w=32, h=24):
    rng = np.random.default_rng(seed)
    # 平滑彩色块保留缩小再放大后的结构，避免随机单像素噪声主导得分。
    tiny = rng.integers(0, 256, (6, 8, 3), dtype=np.uint8)
    return cv2.resize(tiny, (w, h), interpolation=cv2.INTER_LINEAR)


class VisualScalingTests(unittest.TestCase):
    def setUp(self):
        # 任何遗漏的真实截图都立即失败；不读取真实模板、配置或注入输入。
        self.capture = patch.object(window, "grab", side_effect=AssertionError("real capture forbidden"))
        self.capture.start()
        self.addCleanup(self.capture.stop)

    def test_identity_without_calibration_or_with_size_tolerance(self):
        image = np.zeros((100, 100, 3), np.uint8)
        for calib in (None, [100, 100], [104, 104]):
            with self.subTest(calib=calib):
                scene = window.ScaledScene([31, -17, 100, 100], calib, image)
                self.assertEqual(scene.scale, 1.0)
                self.assertFalse(scene.normalized)
                self.assertIs(scene.img, image)
                self.assertEqual(scene.to_screen(80, 70), (111, 53))
                self.assertEqual(scene.to_screen_box([20, 30, 40, 50]), [51, 13, 40, 50])

    def test_deadzone_is_identity_for_pixels_points_boxes_and_threshold(self):
        image = np.zeros((800, 1000, 3), np.uint8)
        for calib in ([1010, 808], [990, 792]):
            with self.subTest(calib=calib):
                scene = window.ScaledScene([100, 200, 1000, 800], calib, image)
                self.assertFalse(scene.normalized)
                self.assertEqual(scene.scale, 1.0)
                self.assertIs(scene.img, image)
                self.assertEqual(scene.local_rect, [0, 0, 1000, 800])
                self.assertEqual(scene.to_screen(900, 700), (1000, 900))
                self.assertEqual(scene.to_screen_box([800, 600, 100, 100]), [900, 800, 100, 100])
                with patch.object(vision, "match_scaled", return_value=None) as match:
                    scene.match(pattern(1), 0.85)
                    self.assertEqual(match.call_args.args[3], 1.0)

    def test_integer_dimensions_and_per_axis_coordinate_roundtrip(self):
        image = np.zeros((79, 113, 3), np.uint8)
        for calib in ([156, 109], [83, 58]):
            with self.subTest(calib=calib):
                scene = window.ScaledScene([-50, 75, 113, 79], calib, image)
                h, w = scene.img.shape[:2]
                self.assertEqual(scene.local_rect, [0, 0, w, h])
                self.assertEqual(scene.to_screen(w, h), (63, 154))
                self.assertEqual(scene.to_screen_box([0, 0, w, h]), [-50, 75, 113, 79])
                self.assertEqual(scene.to_screen(w / 2, h / 2), (6, 115))
                self.assertIs(scene.img, scene.img)
                self.assertIsNone(scene.to_screen(None, 1))
                self.assertIsNone(scene.to_screen_box(None))

    def test_lazy_fake_capture_once_and_coordinates_do_not_capture(self):
        scene = window.ScaledScene([20, 30, 120, 80], [180, 120])
        self.assertEqual(scene.to_screen(90, 60), (80, 70))
        self.assertEqual(scene.local_rect, [0, 0, 180, 120])
        with patch.object(window, "grab", return_value=np.zeros((80, 120, 3), np.uint8)) as grab:
            self.assertEqual(scene.img.shape, (120, 180, 3))
            self.assertIs(scene.img, scene.img)
            grab.assert_called_once_with([20, 30, 120, 80])

    def test_threshold_contract_and_both_caller_directions(self):
        self.assertEqual(vision.scaled_threshold(0.85, 0.70), 0.75)
        self.assertEqual(vision.scaled_threshold(0.85, 0.80), 0.80)
        self.assertEqual(vision.scaled_threshold(0.85, 1.25), 0.85)
        self.assertEqual(vision.scaled_threshold(0.72, 0.70), 0.72)
        for current in (0.70, 0.80, 1.25):
            with self.subTest(current=current):
                width = int(1000 * current)
                scene = window.ScaledScene([0, 0, width, width], [1000, 1000],
                                           np.zeros((width, width, 3), np.uint8))
                with patch.object(vision, "match_scaled", return_value=None) as match:
                    scene.match(pattern(1), 0.85)
                    self.assertAlmostEqual(match.call_args.args[3], current)
                image = np.zeros((50, 100, 3), np.uint8)
                with patch.object(vision, "scaled_threshold", wraps=vision.scaled_threshold) as threshold:
                    list_row._anchor_peaks(image, pattern(1), 0.85, [1.0], norm=1.0 / current)
                    self.assertAlmostEqual(threshold.call_args.args[1], current)

    def test_synthetic_scene_matches_and_maps_screen_point(self):
        template = pattern(13)
        base = np.zeros((160, 240, 3), np.uint8)
        base[60:84, 80:112] = template
        for current in (0.75, 1.0, 1.25):
            with self.subTest(current=current):
                width, height = int(240 * current), int(160 * current)
                image = cv2.resize(base, (width, height), interpolation=cv2.INTER_AREA if current < 1 else cv2.INTER_LINEAR)
                scene = window.ScaledScene([400, 300, width, height], [240, 160], image)
                hit = scene.match(template, 0.85)
                self.assertIsNotNone(hit)
                point = scene.to_screen(*hit[:2])
                self.assertLessEqual(abs(point[0] - (400 + round(96 * current))), 1)
                self.assertLessEqual(abs(point[1] - (300 + round(72 * current))), 1)

    def test_synthetic_card_scaling_preserves_nearest_right_join(self):
        anchor, join = pattern(21), pattern(22, 24, 16)
        base = np.zeros((160, 400, 3), np.uint8)
        base[48:72, 32:64] = anchor
        base[52:68, 132:156] = join
        base[52:68, 252:276] = join
        for current in (0.75, 1.0, 1.25):
            with self.subTest(current=current):
                width, height = int(400 * current), int(160 * current)
                image = cv2.resize(base, (width, height), interpolation=cv2.INTER_AREA if current < 1 else cv2.INTER_LINEAR)
                hit = list_row.locate_card(image, [200, 300, width, height], anchor, join,
                                           0.85, [400, 160], {"join_min_score": 0.7},
                                           window_rect=[200, 300, width, height])
                self.assertIsNotNone(hit)
                self.assertIsNotNone(hit.join_x)
                self.assertLessEqual(abs(hit.anchor_x - (200 + round(48 * current))), 1)
                self.assertLessEqual(abs(hit.join_x - (200 + round(144 * current))), 1)
                self.assertLessEqual(abs(hit.join_y - (300 + round(60 * current))), 1)

    def test_geometry_band_nearest_right_and_joint_ranking_remain(self):
        anchor, join = pattern(31), pattern(32, 24, 16)
        image = np.zeros((160, 400, 3), np.uint8)
        # 得分更高的邻卡不胜过本卡最近的右侧按钮；左侧/重叠候选不能抢占。
        candidates = [(40, 60, 0.99, 1.0), (55, 60, 0.98, 1.0),
                      (144, 60, 0.75, 1.0), (264, 60, 0.99, 1.0)]
        with patch.object(list_row, "_join_candidates", return_value=candidates) as finder:
            got = list_row._locate_join(image, anchor, join, (48, 60),
                                        {"join_reach_ratio": 6, "join_band_ratio": 3.2}, [1.0], 0.7)
            self.assertEqual(got, candidates[2])
            self.assertEqual(finder.call_args.args[3], (40, 22))
            self.assertEqual(finder.call_args.args[0].shape[:2], (76, 200))
        # 条目最高分不必胜出，仍由条目分 + 0.5*参加分决定。
        with patch.object(list_row, "_anchor_peaks", return_value=[(48, 60, 0.95), (48, 120, 0.90)]), \
             patch.object(list_row, "_locate_join", side_effect=[(144, 60, 0.71, 1.0), (144, 120, 0.99, 1.0)]):
            hit = list_row.locate_card(image, [200, 300, 400, 160], anchor, join,
                                       cfg={"join_min_score": 0.7})
            self.assertEqual((hit.anchor_x, hit.anchor_y), (248, 420))
            self.assertEqual((hit.join_x, hit.join_y), (344, 420))


if __name__ == "__main__":
    unittest.main()
