# -*- coding: utf-8 -*-
"""组队同观察候选门禁：假窗口、合成图像，无游戏输入或真实资产。"""
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

import numpy as np

from mhxy.core import scan, teaming
from mhxy.core.input_session import InputSession


class _Window:
    def __init__(self):
        self.bounds = (100, 200, 240, 120)
        self.rect_reads = 0
        self._win = SimpleNamespace(_hWnd=1)

    def rect(self):
        self.rect_reads += 1
        return self.bounds


class TeamingObservationTests(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(17)
        self.leader = rng.integers(0, 256, (20, 30, 3), dtype=np.uint8)
        self.arrow = rng.integers(0, 256, (8, 10, 3), dtype=np.uint8)
        self.scene = np.zeros((120, 240, 3), dtype=np.uint8)
        self.scene[30:50, 20:50] = self.leader
        self.scene[44:52, 150:160] = self.arrow
        self.window = _Window()
        self.raw_mouse = mock.Mock()
        self.raw_mouse.click.return_value = None
        self.raw_mouse.scroll.return_value = None
        self.cfg = {"tasks": {"teaming": {"dry_run": False}}}
        self.ctx = SimpleNamespace(cfg=self.cfg, window=self.window,
                                   mouse=self.raw_mouse, log=mock.Mock(),
                                   should_stop=lambda: False)
        # 不调用模板载入，即使配置路径未来改变也不读真实模板。
        with mock.patch.object(teaming.TeamFormation, "_load_templates",
                               return_value={"leader_id": self.leader,
                                             "team_arrow": self.arrow}):
            self.team = teaming.TeamFormation(
                self.ctx, [(self.ctx, teaming.TeamFormation.ROLE_MEMBER)],
                {"loop": {"scroll_reset_top": False, "scroll_max_tries": 1}})
        self.team._sleep = lambda seconds: None
        self.rec = self.team.records[0]
        self.rec.update(state=teaming.M_FIND_LEADER, t_state=time.time())

    def test_scan_matches_leader_and_arrow_in_one_capture(self):
        with mock.patch.object(teaming.win_mod, "grab", return_value=self.scene) as grab:
            self.team._mem_find(self.rec)
        grab.assert_called_once_with((100, 200, 240, 120))
        self.assertEqual(self.window.rect_reads, 1)
        self.raw_mouse.click.assert_called_once_with(255, 248)
        self.assertEqual(self.rec["state"], teaming.M_APPLY_JOIN)
        self.assertFalse(self.rec["done"])
        self.assertEqual(self.team.joined, 0)
        self.assertFalse(self.team.team_ready)

    def test_arrow_helper_does_not_read_window_or_capture(self):
        # 后续窗口位置已变，候选依然只能来自传入的旧观察；输入门禁另行拒绝。
        self.window.bounds = (600, 700, 240, 120)
        with mock.patch.object(teaming.win_mod, "grab", side_effect=AssertionError("recapture")):
            hit = self.team._find_arrow_on_row(self.scene, (100, 200, 240, 120), (135, 240))
        self.assertEqual(hit[:2], (255, 248))
        self.assertGreater(hit[2], 0.99)
        self.assertEqual(self.window.rect_reads, 0)

    def test_fallback_uses_same_anchor_and_rect_without_capture(self):
        for arrow in (None, self.arrow):
            with self.subTest(optional_arrow=arrow is None):
                self.team.tpl["team_arrow"] = arrow
                blank_arrow_scene = self.scene.copy()
                blank_arrow_scene[44:52, 150:160] = 0
                with mock.patch.object(teaming.win_mod, "grab", side_effect=AssertionError("recapture")):
                    hit = self.team._find_arrow_on_row(
                        blank_arrow_scene, (100, 200, 240, 120), (135, 240))
                self.assertEqual(hit, (178, 243, 0.0))

    def test_invalid_scene_never_produces_fallback(self):
        for arrow in (None, self.arrow):
            self.team.tpl["team_arrow"] = arrow
            for scene in (None, np.empty((0, 240, 3), dtype=np.uint8),
                          np.empty((120, 0, 3), dtype=np.uint8)):
                with self.subTest(optional_arrow=arrow is None, scene_shape=getattr(scene, "shape", None)):
                    self.assertIsNone(self.team._find_arrow_on_row(
                        scene, (100, 200, 240, 120), (135, 240)))
        self.assertIsNone(self.team._find_arrow_on_row(self.scene, None, (135, 240)))

    def test_failed_scan_capture_never_clicks_or_matches(self):
        with mock.patch.object(teaming.win_mod, "grab", return_value=None), \
                mock.patch.object(teaming.vision, "match") as match:
            self.team._mem_find(self.rec)
        match.assert_not_called()
        self.raw_mouse.click.assert_not_called()
        self.assertEqual(self.rec["state"], teaming.M_FIND_LEADER)

    def test_empty_probe_frame_does_not_use_previous_anchor(self):
        def fake_scan(**kwargs):
            decision, payload = kwargs["probe"](np.empty((0, 240, 3), dtype=np.uint8),
                                                 (100, 200, 240, 120))
            self.assertEqual((decision, payload), (scan.SCROLL, None))
            return scan.ScanResult("exhausted")
        with mock.patch.object(teaming.scan, "scroll_search", side_effect=fake_scan), \
                mock.patch.object(teaming.vision, "match") as match:
            self.team._mem_find(self.rec)
        match.assert_not_called()
        self.raw_mouse.click.assert_not_called()

    def _run_with_session(self, mutation=None, capture_success=True):
        session = InputSession(self.cfg, threading.Event(), "dungeon")
        session._foreground = lambda window: True
        self.ctx.mouse = session.bind_mouse(self.ctx, self.raw_mouse)

        def capture(rect):
            token = session.capture_token()
            if mutation is not None:
                mutation(session)
            session.observed(token, rect, capture_success)
            return self.scene if capture_success else None

        with session.activate(), mock.patch.object(teaming.win_mod, "grab", side_effect=capture):
            session.activated(self.window, True)
            self.team._mem_find(self.rec)

    def test_session_allows_same_frame_candidate(self):
        self._run_with_session()
        self.raw_mouse.click.assert_called_once_with(255, 248)
        self.assertEqual(self.rec["state"], teaming.M_APPLY_JOIN)

    def test_rect_change_rejects_candidate_without_accepting_it(self):
        self._run_with_session(lambda session: setattr(self.window, "bounds", (600, 700, 240, 120)))
        self.raw_mouse.click.assert_not_called()
        self.assertEqual(self.rec["state"], teaming.M_FIND_LEADER)
        self.assertEqual(self.team.joined, 0)
        self.assertFalse(self.team.team_ready)

    def test_session_failed_capture_has_no_input(self):
        self._run_with_session(capture_success=False)
        self.raw_mouse.click.assert_not_called()
        self.raw_mouse.scroll.assert_not_called()
        self.assertEqual(self.rec["state"], teaming.M_FIND_LEADER)

    def test_stop_and_dry_run_gate_candidate_click(self):
        for kind in ("stop", "dry_run"):
            with self.subTest(kind=kind):
                self.raw_mouse.reset_mock()
                self.rec.update(state=teaming.M_FIND_LEADER, t_state=time.time(), done=False)
                self.cfg["tasks"]["teaming"]["dry_run"] = kind == "dry_run"
                mutation = (lambda session: session.stop_event.set()) if kind == "stop" else None
                self._run_with_session(mutation)
                self.raw_mouse.click.assert_not_called()
                self.assertEqual(self.rec["state"], teaming.M_FIND_LEADER)


if __name__ == "__main__":
    unittest.main()
