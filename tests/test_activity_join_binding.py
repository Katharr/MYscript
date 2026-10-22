"""活动参加回归：本卡缺按钮时禁止借用邻卡，旧尺寸记录不强制重标。"""

from types import SimpleNamespace
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from mhxy.core import calibration_runtime, list_row


def pattern(seed, width, height):
    tiny = np.random.default_rng(seed).integers(0, 256, (6, 8, 3), dtype=np.uint8)
    return cv2.resize(tiny, (width, height), interpolation=cv2.INTER_LINEAR)


class ActivityJoinBindingTests(unittest.TestCase):
    def setUp(self):
        self.anchor = pattern(301, 40, 30)
        self.join = pattern(302, 30, 20)

    def scene(self, own_button=True, anchor_x=60, own_x=250, neighbor_x=590):
        image = np.zeros((180, 680, 3), dtype=np.uint8)
        image[55:85, anchor_x-20:anchor_x+20] = self.anchor
        if own_button:
            image[60:80, own_x-15:own_x+15] = self.join
        image[60:80, neighbor_x-15:neighbor_x+15] = self.join
        return image

    def locate(self, image, current=1.0, **kwargs):
        height, width = image.shape[:2]
        resized = cv2.resize(image, (round(width * current), round(height * current)))
        return list_row.locate_card(
            resized, [100, 200, resized.shape[1], resized.shape[0]], self.anchor, self.join,
            calib_size=[680, 180], window_rect=[100, 200, resized.shape[1], resized.shape[0]],
            cfg={"activity_columns": 2, "join_min_score": 0.7}, **kwargs)

    def test_missing_own_button_never_selects_high_scoring_neighbor(self):
        # 宽条目让旧 reach 搜索带直接覆盖邻卡按钮，最近候选也仍是邻卡。
        for scale in (0.75, 1.0, 1.5):
            with self.subTest(scale=scale):
                hit = self.locate(self.scene(False), scale)
                self.assertIsNotNone(hit)
                self.assertIsNone(hit.join_x)

    def test_weak_own_button_does_not_fall_through_to_perfect_neighbor(self):
        image = self.scene()
        noise = np.random.default_rng(77).integers(0, 256, self.join.shape, dtype=np.uint8)
        image[60:80, 235:265] = cv2.addWeighted(self.join, 0.25, noise, 0.75, 0)
        hit = self.locate(image)
        self.assertIsNotNone(hit)
        self.assertIsNone(hit.join_x)

    def test_own_button_stays_selected_across_window_sizes(self):
        for scale in (0.75, 1.0, 1.5):
            with self.subTest(scale=scale):
                hit = self.locate(self.scene(), scale)
                self.assertIsNotNone(hit.join_x)
                self.assertLessEqual(abs(hit.join_x - (100 + round(250 * scale))), 1)
                self.assertLessEqual(abs(hit.join_y - (200 + round(70 * scale))), 1)

    def test_offset_list_does_not_cut_own_button_at_equal_column_boundary(self):
        # 条目在列表中线左侧，自己的参加在中线右侧；按条目相对跨度仍能找到。
        hit = self.locate(self.scene(anchor_x=300, own_x=450, neighbor_x=650))
        self.assertEqual(hit.join_x, 550)

    def test_single_column_can_reach_its_far_right_button(self):
        image = self.scene(False, neighbor_x=500)
        hit = list_row.locate_card(image, [0, 0, 680, 180], self.anchor, self.join,
                                   cfg={"activity_columns": 1, "join_min_score": 0.7})
        self.assertEqual(hit.join_x, 500)

    def test_left_or_overlapping_candidates_never_become_click_targets(self):
        for candidates in ([(65, 70, 0.99, 1.0)], [(40, 70, 0.99, 1.0)]):
            with self.subTest(candidates=candidates), \
                 patch.object(list_row, "_join_candidates", return_value=candidates):
                attempt = list_row._locate_join(self.scene(False), self.anchor, self.join,
                                                (60, 70), {}, [1.0], 0.7)
                self.assertIsNone(attempt)

    def test_native_card_pixels_survive_legacy_smaller_window_profile(self):
        image = self.scene()
        hit = list_row.locate_card(
            image, [100, 200, 680, 180], self.anchor, self.join,
            calib_size=[453, 120], join_calib_size=[453, 120],
            window_rect=[100, 200, 680, 180], cfg={"join_min_score": 0.7})
        self.assertEqual((hit.anchor_x, hit.anchor_y), (160, 270))
        self.assertEqual((hit.join_x, hit.join_y), (350, 270))

    def test_native_anchor_with_new_common_join_profile_keeps_same_card(self):
        for own_button in (False, True):
            with self.subTest(own_button=own_button):
                hit = list_row.locate_card(
                    self.scene(own_button), [100, 200, 680, 180], self.anchor, self.join,
                    calib_size=[453, 120], join_calib_size=[680, 180],
                    window_rect=[100, 200, 680, 180], cfg={"join_min_score": 0.7})
                self.assertIsNotNone(hit)
                self.assertEqual(hit.join_x, 350 if own_button else None)

    def test_runtime_flag_accepts_native_pixels_with_legacy_profile(self):
        cfg = {"calib_profiles": {"items": [{"id": 1, "size": [453, 120]}], "active": 1},
               "tasks": {"escort": {"templates": {"escort_confirm": "old.png"},
                                     "templates_calib": {"escort_confirm": 1}}}}
        ctx = SimpleNamespace(cfg=cfg, window=SimpleNamespace(rect=lambda: [10, 20, 680, 180]))
        task = SimpleNamespace(name="escort")
        image = np.zeros((180, 680, 3), dtype=np.uint8)
        image[60:80, 235:265] = self.join
        hit = calibration_runtime.match_flag(task, image, self.join, "escort_confirm", 0.85, ctx)
        self.assertEqual(hit[:2], (250, 70))


if __name__ == "__main__":
    unittest.main()
