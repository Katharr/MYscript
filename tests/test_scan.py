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


class ResumableScanTests(unittest.TestCase):
    """虚拟长背包 + 时钟：检验跨轮转覆盖、延迟刷新和失效后的重新回顶。"""

    def setUp(self):
        self.now = 100.0
        self.page = 4
        self.visible_page = self.page
        self.bottom = 25
        self.target = 22
        self.delay = 0.0
        self.refresh_at = 0.0
        self.rect = (10, 20, 100, 80)
        self.moves = []
        self.probed = []
        self.stop = False
        self.progress = scan.ScanProgress()
        patch = mock.patch.object(scan.time, "monotonic", side_effect=lambda: self.now)
        patch.start()
        self.addCleanup(patch.stop)
        self.progress.started = self.now
        self.mouse = mock.Mock()
        self.mouse.scroll.side_effect = self.scroll

    def sleep(self, seconds):
        self.now += seconds

    def scroll(self, direction, *args, **kwargs):
        self.moves.append(direction)
        self.page = max(0, min(self.bottom, self.page + (1 if direction < 0 else -1)))
        self.refresh_at = self.now + self.delay

    def capture(self):
        if self.now >= self.refresh_at:
            self.visible_page = self.page
        return ScrollSearchTests.frame(self.visible_page * 9), self.rect

    def probe(self, frame, rect):
        page = int(frame[1, 1, 0]) // 9
        self.probed.append(page)
        return (scan.ACCEPT, page) if page == self.target else (scan.SCROLL, None)

    def advance(self, **kwargs):
        return self.progress.advance(capture=self.capture, probe=self.probe,
            mouse=self.mouse, sleep=self.sleep, should_stop=lambda: self.stop,
            max_scrolls=kwargs.pop("max_scrolls", 3), **kwargs)

    def finish(self, **kwargs):
        for _ in range(100):
            result = self.advance(**kwargs)
            if not result.paused:
                return result
        self.fail("scan never finished")

    def test_long_bag_target_beyond_eight_scrolls_survives_rotation(self):
        result = self.finish(max_scrolls=2)
        self.assertTrue(result.found)
        self.assertEqual(result.payload, 22)
        first_down = self.moves.index(-3)
        self.assertTrue(all(step < 0 for step in self.moves[first_down:]))
        self.assertGreater(self.progress.scrolls, 8)
        self.assertEqual(set(range(23)), set(self.probed))

    def test_one_scroll_budget_preserves_boundary_evidence_without_double_count(self):
        self.target = None
        result = self.finish(max_scrolls=1)
        self.assertTrue(result.reached_top)
        self.assertTrue(result.reached_end)
        self.assertEqual(set(range(26)), set(self.probed))

    def test_delayed_old_frames_cannot_confirm_false_top_or_bottom(self):
        self.delay = .30
        result = self.finish()
        self.assertTrue(result.found)
        self.assertIn(0, self.probed)
        self.assertEqual(result.payload, 22)

    def test_animation_yield_resumes_wait_instead_of_scrolling_again(self):
        self.delay = .30
        first = self.advance(budget_sec=.10)
        self.assertTrue(first.paused)
        self.assertTrue(self.progress.pending)
        self.assertEqual(len(self.moves), 1)
        result = self.finish()
        self.assertTrue(result.found)
        self.assertIn(0, self.probed)

    def test_changed_checkpoint_invalidates_coverage_and_returns_to_top(self):
        while self.progress.phase != "down" or self.page < 5:
            self.assertTrue(self.advance().paused)
        self.page = 12  # 手动滚动后旧覆盖证据无效。
        before = len(self.moves)
        result = self.finish()
        self.assertTrue(result.found)
        self.assertGreater(self.moves[before], 0)

    def test_total_limit_is_failure_and_never_empty_evidence(self):
        result = self.finish(total_scrolls=10)
        self.assertTrue(result.stopped)
        self.assertFalse(result.reached_end)
        self.assertEqual(self.progress.last_reason, "scan_limit")

    def test_wall_timeout_includes_rotations_and_does_not_restart_budget(self):
        self.assertTrue(self.advance().paused)
        self.now += 121
        result = self.advance()
        self.assertTrue(result.stopped)
        self.assertEqual(self.progress.last_reason, "scan_limit")

    def test_capture_or_scroll_failure_and_stop_never_claim_end(self):
        self.mouse.scroll.return_value = False
        self.mouse.scroll.side_effect = None
        self.assertTrue(self.advance().stopped)
        self.assertFalse(self.progress.reached_top)
        self.stop = True
        self.mouse.reset_mock()
        self.assertTrue(self.advance().stopped)
        self.mouse.scroll.assert_not_called()

    def test_partial_animation_frames_are_not_probed(self):
        original_capture = self.capture
        def animated():
            frame, rect = original_capture()
            if self.now < self.refresh_at:
                # 翻页中途出现像藏宝图的画面，也不能提前点。
                frame = ScrollSearchTests.frame((180, 198, 210)[int(self.now * 100) % 3])
            return frame, rect
        self.delay = .3
        self.capture = animated
        result = self.finish()
        self.assertTrue(result.found)
        self.assertEqual(self.page, 22)

    def test_refreshed_stable_page_needs_only_short_wait(self):
        self.advance(max_scrolls=1)
        self.assertLess(self.now - 100.0, .30)


class ScrollHoverTests(unittest.TestCase):
    def test_cursor_stays_in_bag_and_reenters_after_switch(self):
        from mhxy.core import input as input_mod
        mouse = input_mod.Mouse("sendinput", {})
        with mock.patch.object(input_mod, "get_cursor", side_effect=[(30, 40), (800, 600)]), \
             mock.patch.object(mouse, "human_move") as move, \
             mock.patch.object(input_mod, "_raw_wheel") as wheel, \
             mock.patch.object(input_mod.time, "sleep"):
            mouse.scroll(-3, 60, 60, region=(10, 20, 100, 80))
            move.assert_not_called()
            mouse.scroll(-3, 60, 60, region=(10, 20, 100, 80))
            move.assert_called_once_with(60, 60, speed=None)
        self.assertEqual(wheel.call_count, 6)


if __name__ == "__main__":
    unittest.main()
