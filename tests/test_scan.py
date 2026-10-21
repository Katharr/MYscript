# -*- coding: utf-8 -*-
"""滚动覆盖判定：合成帧与假鼠标，不操作游戏。"""
import unittest
from unittest import mock

import numpy as np

from mhxy.core import scan


class ScrollSearchTests(unittest.TestCase):
    rect = (10, 20, 100, 80)

    @staticmethod
    def frame(value):
        scene = np.full((8, 10, 3), value, dtype=np.uint8)
        scene[::2, ::2] += 4
        return scene

    def search(self, frames, decisions=None, **kwargs):
        self.mouse = mock.Mock()
        self.mouse.scroll.return_value = None
        self.probe = mock.Mock(side_effect=decisions) if decisions else mock.Mock(
            return_value=(scan.SCROLL, None))
        with mock.patch.object(scan.win_mod, "grab", side_effect=frames):
            return scan.scroll_search(
                grab_rect=lambda: self.rect, probe=self.probe, mouse=self.mouse,
                should_stop=lambda: False, sleep=lambda _: None, **kwargs)

    def test_legacy_result_constructor_defaults_to_unknown_top(self):
        result = scan.ScanResult("exhausted", None, 3, True)
        self.assertTrue(result.reached_end)
        self.assertFalse(result.reached_top)

    def test_legacy_scan_can_skip_top_without_claiming_top_coverage(self):
        result = self.search([self.frame(0)] * 2, reset_to_top=False)
        self.assertTrue(result.reached_end)
        self.assertFalse(result.reached_top)
        self.assertEqual(self.probe.call_count, 2)

    def test_failed_upward_scroll_cannot_confirm_top(self):
        mouse = mock.Mock()
        mouse.scroll.return_value = False
        with mock.patch.object(scan.win_mod, "grab", return_value=self.frame(0)):
            result = scan.scroll_search(
                grab_rect=lambda: self.rect, probe=lambda *_: (scan.SCROLL, None),
                mouse=mouse, should_stop=lambda: False, sleep=lambda _: None,
                require_full_scan=True)
        self.assertTrue(result.stopped)
        self.assertFalse(result.reached_top)
        self.assertFalse(result.reached_end)

    def test_top_limit_does_not_claim_complete_coverage(self):
        result = self.search([self.frame(n) for n in (0, 20, 40)],
                             require_full_scan=True, reset_max=2)
        self.assertTrue(result.stopped)
        self.assertFalse(result.reached_top)
        self.assertFalse(result.reached_end)
        self.probe.assert_not_called()

    def test_strict_scan_rejects_zero_and_positive_scroll_steps(self):
        for step in (0, 3):
            with self.subTest(scroll_step=step):
                result = self.search([], require_full_scan=True, scroll_step=step)
                self.assertTrue(result.stopped)
                self.assertFalse(result.reached_top)
                self.assertFalse(result.reached_end)
                self.probe.assert_not_called()
                self.mouse.scroll.assert_not_called()

    def test_legacy_scan_keeps_nonnegative_scroll_step_behavior(self):
        for step in (0, 3):
            with self.subTest(scroll_step=step):
                result = self.search([self.frame(0)] * 2,
                                     reset_to_top=False, scroll_step=step)
                self.assertTrue(result.exhausted)
                self.assertTrue(result.reached_end)
                self.assertFalse(result.reached_top)

    def test_legacy_scan_continues_when_top_reset_reaches_limit(self):
        result = self.search([self.frame(n) for n in (0, 20, 40, 40, 40)],
                             reset_max=2)
        self.assertTrue(result.exhausted)
        self.assertTrue(result.reached_end)
        self.assertFalse(result.reached_top)
        self.assertEqual(self.probe.call_count, 2)

    def test_strict_scan_rejects_black_empty_or_flat_frames(self):
        for invalid in (np.zeros((8, 10, 3), dtype=np.uint8),
                        np.full((8, 10, 3), 255, dtype=np.uint8),
                        np.empty((0, 10, 3), dtype=np.uint8),
                        np.tile(np.array([0, 50, 100], dtype=np.uint8), (8, 10, 1))):
            for at_bottom in (False, True):
                with self.subTest(shape=invalid.shape, at_bottom=at_bottom):
                    frames = [self.frame(0)] * 4 + [invalid] if at_bottom else [invalid]
                    result = self.search(frames, require_full_scan=True)
                    self.assertTrue(result.stopped)
                    self.assertEqual(result.reached_top, at_bottom)
                    self.assertFalse(result.reached_end)

    def test_one_unresponsive_scroll_does_not_confirm_top(self):
        # 第一次上滚暂时没反应，第二次才移动；必须重新累计两次稳定上滚。
        result = self.search([self.frame(n) for n in (20, 20, 0, 0, 0, 0)],
                             decisions=[(scan.ACCEPT, "target")],
                             require_full_scan=True)
        self.assertTrue(result.found)
        self.assertTrue(result.reached_top)
        self.assertEqual(self.mouse.scroll.call_count, 4)

    def test_one_unresponsive_scroll_does_not_confirm_bottom(self):
        result = self.search([self.frame(n) for n in
                              (0, 0, 0, 0, 10, 10, 20, 20, 20)],
                             require_full_scan=True)
        self.assertTrue(result.exhausted)
        self.assertTrue(result.reached_top)
        self.assertTrue(result.reached_end)
        self.assertEqual(self.probe.call_count, 6)
        self.assertEqual(self.mouse.scroll.call_count, 7)  # 两次上滚、五次下滚

    def test_probe_finds_small_target_before_similar_frame_end(self):
        original = self.frame(0)
        target = original.copy()
        target[0, 0] = 1  # 整帧平均差小于 end_diff，目标仍必须被检查。
        for strict in (False, True):
            with self.subTest(strict=strict):
                frames = [original] * 3 + [original, target] if strict else [original, target]
                result = self.search(frames,
                                     decisions=[(scan.SCROLL, None), (scan.ACCEPT, "map")],
                                     reset_to_top=False, require_full_scan=strict)
                self.assertTrue(result.found)
                self.assertEqual(result.payload, "map")
                self.assertFalse(result.reached_end)

    def test_strict_scan_requires_top_even_if_reset_disabled(self):
        result = self.search([self.frame(0)] * 6, reset_to_top=False,
                             require_full_scan=True)
        self.assertTrue(result.reached_top)
        self.assertTrue(result.reached_end)
        self.assertEqual([call.args[0] for call in self.mouse.scroll.call_args_list],
                         [3, 3, -3, -3])

    def test_capture_failure_does_not_claim_bottom(self):
        result = self.search([self.frame(0)] * 5 + [None], require_full_scan=True)
        self.assertTrue(result.stopped)
        self.assertTrue(result.reached_top)
        self.assertFalse(result.reached_end)

    def test_capture_failure_does_not_claim_top(self):
        result = self.search([self.frame(0), None], require_full_scan=True)
        self.assertTrue(result.stopped)
        self.assertFalse(result.reached_top)
        self.assertFalse(result.reached_end)

    def test_scroll_failure_does_not_claim_bottom(self):
        mouse = mock.Mock()
        mouse.scroll.side_effect = [None, None, None, False]
        with mock.patch.object(scan.win_mod, "grab", return_value=self.frame(0)):
            result = scan.scroll_search(
                grab_rect=lambda: self.rect, probe=lambda *_: (scan.SCROLL, None),
                mouse=mouse, should_stop=lambda: False, sleep=lambda _: None,
                require_full_scan=True)
        self.assertTrue(result.stopped)
        self.assertTrue(result.reached_top)
        self.assertFalse(result.reached_end)

    def test_attempt_limit_is_not_bottom_confirmation(self):
        result = self.search([self.frame(0)] * 5,
                             require_full_scan=True, max_tries=1)
        self.assertTrue(result.exhausted)
        self.assertTrue(result.reached_top)
        self.assertFalse(result.reached_end)

    def test_in_place_actions_reset_bottom_confirmation(self):
        for action in (scan.HANDLED, scan.STAY):
            with self.subTest(action=action):
                result = self.search(
                    [self.frame(0)] * 9,
                    decisions=[(scan.SCROLL, None), (scan.SCROLL, None),
                               (action, None), (scan.SCROLL, None),
                               (scan.SCROLL, None), (scan.SCROLL, None)],
                    require_full_scan=True)
                self.assertTrue(result.reached_end)
                self.assertEqual(self.probe.call_count, 6)


if __name__ == "__main__":
    unittest.main()
