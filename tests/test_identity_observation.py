# -*- coding: utf-8 -*-
"""身份共享能力门禁：假前台/窗口/像素/OCR，无真实截图或名册。"""
import copy
from types import SimpleNamespace
import unittest
from unittest import mock

import numpy as np

from mhxy.core import accounts, window


class IdentityObservationTests(unittest.TestCase):
    def setUp(self):
        self.win = SimpleNamespace(hwnd=701, _win=SimpleNamespace(_hWnd=701),
                                   rect=mock.Mock(return_value=[10, 20, 1000, 800]))
        self.evidence = (701, (10, 20, 1000, 800))
        self.roi = np.ones((40, 100, 3), dtype=np.uint8)
        self.names = {"a": {"name": "甲", "level": "40"},
                      "b": {"name": "乙", "level": "50"}}
        with accounts._lock:
            old_identity = copy.deepcopy(accounts._identity)
            old_order = list(accounts._cache["order"])
            old_sessions = copy.deepcopy(accounts._launch_sessions)
            accounts._identity.clear()
            accounts._launch_sessions.clear()
        def restore():
            with accounts._lock:
                accounts._identity.clear()
                accounts._identity.update(old_identity)
                accounts._launch_sessions.clear()
                accounts._launch_sessions.update(old_sessions)
                accounts._cache["order"] = old_order
        self.addCleanup(restore)
        self.front = self.patch(window, "foreground_evidence", return_value=self.evidence)
        self.grab = self.patch(window, "grab", return_value=self.roi)
        self.fingerprint = self.patch(accounts, "_fingerprint", return_value=b"old")
        self.ocr = self.patch(accounts, "_ocr_role_id_without_lock", return_value="a")
        self.patch(accounts, "roster", return_value=self.names)

    def patch(self, obj, attr, **kwargs):
        patch = mock.patch.object(obj, attr, **kwargs)
        result = patch.start()
        self.addCleanup(patch.stop)
        return result

    def confirm(self):
        self.assertEqual(accounts.labels_for([self.win]), ["甲（40）"])
        self.assertEqual(accounts.bound_role_id(self.win), "a")
        self.ocr.reset_mock()

    def test_new_fingerprint_hides_old_label_and_role_until_confirmed(self):
        self.confirm()
        self.fingerprint.return_value = b"new"
        self.assertEqual(accounts.labels_for([self.win]), ["号1"])
        self.assertEqual(accounts.cached_labels_for([self.win]), ["号1"])
        self.assertIsNone(accounts.bound_role_id(self.win))
        self.ocr.assert_not_called()
        self.ocr.return_value = "b"
        self.assertEqual(accounts.labels_for([self.win]), ["乙（50）"])
        self.assertEqual(accounts.bound_role_id(self.win), "b")
        self.ocr.assert_called_once()

    def test_ambiguous_new_pixels_remain_hidden_during_retry(self):
        self.confirm()
        self.fingerprint.return_value = b"new"
        self.ocr.return_value = None
        self.assertIsNone(accounts._role_for_window(self.win, self.names, 100))
        self.assertIsNone(accounts._role_for_window(self.win, self.names, 101))
        self.assertIsNone(accounts._role_for_window(self.win, self.names, 102))
        self.ocr.assert_called_once()
        self.assertEqual(accounts.cached_labels_for([self.win]), ["号1"])
        self.assertIsNone(accounts.bound_role_id(self.win))
        self.assertEqual(accounts._identity[701]["role_id"], "a")

    def test_same_pixels_skip_ocr_and_old_pixels_can_restore_confirmed_identity(self):
        self.confirm()
        self.assertEqual(accounts.labels_for([self.win]), ["甲（40）"])
        self.fingerprint.return_value = b"new"
        self.assertEqual(accounts.labels_for([self.win]), ["号1"])
        self.fingerprint.return_value = b"old"
        self.assertEqual(accounts.labels_for([self.win]), ["甲（40）"])
        self.assertEqual(accounts.bound_role_id(self.win), "a")
        self.ocr.assert_not_called()

    def test_background_unavailable_preserves_confirmed_cache_without_capture(self):
        self.confirm()
        self.front.return_value = None
        self.grab.reset_mock()
        self.assertEqual(accounts.labels_for([self.win]), ["甲（40）"])
        self.assertEqual(accounts.bound_role_id(self.win), "a")
        self.grab.assert_not_called()
        self.ocr.assert_not_called()

    def test_capture_loss_movement_or_handle_change_never_bind(self):
        for changed in (None, (701, (11, 20, 1000, 800)),
                        (702, (10, 20, 1000, 800))):
            with self.subTest(changed=changed):
                self.front.side_effect = [self.evidence, self.evidence, changed]
                self.assertIsNone(accounts._role_for_window(self.win, self.names, 100))
                self.assertIsNone(accounts.bound_role_id(self.win))
        self.ocr.assert_not_called()

    def test_empty_or_failed_capture_never_ocr(self):
        self.grab.side_effect = OSError("capture")
        self.assertIsNone(accounts._role_for_window(self.win, self.names, 100))
        self.grab.side_effect = None
        self.grab.return_value = None
        self.fingerprint.return_value = None
        self.assertIsNone(accounts._role_for_window(self.win, self.names, 101))
        self.ocr.assert_not_called()

    def test_ocr_focus_or_geometry_change_does_not_publish(self):
        for changed in (None, (701, (11, 20, 1000, 800)),
                        (702, (10, 20, 1000, 800))):
            with self.subTest(changed=changed):
                self.front.return_value = self.evidence
                def infer(*args):
                    self.front.return_value = changed
                    return "a"
                self.ocr.side_effect = infer
                self.assertIsNone(accounts._role_for_window(self.win, self.names, 100))
                self.assertIsNone(accounts.bound_role_id(self.win))
                self.assertNotIn(701, accounts._identity)

    def test_pending_after_compute_hides_stale_local_label(self):
        self.confirm()
        def compute(wins):
            with accounts._lock:
                accounts._identity[701]["pending"] = b"new"
            return {701: "甲（40）"}
        with mock.patch.object(accounts, "_compute", side_effect=compute):
            self.assertEqual(accounts.labels_for([self.win]), ["号1"])
        self.assertEqual(accounts.cached_labels_for([self.win]), ["号1"])
        self.assertIsNone(accounts.bound_role_id(self.win))

    def test_newer_pending_pixels_during_ocr_reject_stale_result(self):
        self.confirm()
        self.fingerprint.return_value = b"new"
        self.assertEqual(accounts.labels_for([self.win]), ["号1"])
        def infer(*args):
            with accounts._lock:
                accounts._identity[701]["pending"] = b"newer"
            return "b"
        self.ocr.side_effect = infer
        self.assertEqual(accounts.labels_for([self.win]), ["号1"])
        self.assertIsNone(accounts.bound_role_id(self.win))
        self.assertEqual(accounts._identity[701]["pending"], b"newer")
        self.assertEqual(accounts._identity[701]["role_id"], "a")

    def test_focus_change_after_new_pixels_keeps_old_identity_hidden(self):
        self.confirm()
        self.fingerprint.return_value = b"new"
        self.assertEqual(accounts.labels_for([self.win]), ["号1"])
        self.ocr.side_effect = lambda *args: setattr(self.front, "return_value", None) or "b"
        self.assertEqual(accounts.labels_for([self.win]), ["号1"])
        self.assertIsNone(accounts.bound_role_id(self.win))


class ForegroundEvidenceTests(unittest.TestCase):
    def test_real_game_window_handle_method_with_fake_native_window(self):
        win = object.__new__(window.GameWindow)
        win.offset = [0, 0]
        win._win = SimpleNamespace(_hWnd=2 ** 40 + 701, left=-300, top=20,
                                   width=1000, height=800)
        api = SimpleNamespace(IsWindow=lambda h: h == win.hwnd(),
                              IsIconic=lambda h: False,
                              GetForegroundWindow=lambda: win.hwnd())
        with mock.patch.object(window, "_user32", api):
            self.assertEqual(window.foreground_evidence(win),
                             (win.hwnd(), (-300, 20, 1000, 800)))

    def test_read_only_evidence_accepts_exact_foreground_handle(self):
        win = SimpleNamespace(hwnd=2 ** 40 + 701, rect=lambda: [-300, 20, 1000, 800])
        api = SimpleNamespace(IsWindow=mock.Mock(return_value=True),
                              IsIconic=mock.Mock(return_value=False),
                              GetForegroundWindow=mock.Mock(return_value=win.hwnd))
        with mock.patch.object(window, "_user32", api):
            self.assertEqual(window.foreground_evidence(win), (win.hwnd, (-300, 20, 1000, 800)))
            api.GetForegroundWindow.return_value = 702
            self.assertIsNone(window.foreground_evidence(win))
            api.GetForegroundWindow.return_value = win.hwnd
            api.IsIconic.return_value = True
            self.assertIsNone(window.foreground_evidence(win))
            api.IsIconic.return_value = False
            api.IsWindow.return_value = False
            self.assertIsNone(window.foreground_evidence(win))

    def test_missing_handle_geometry_and_api_failure_reject(self):
        api = SimpleNamespace(IsWindow=lambda h: True, IsIconic=lambda h: False,
                              GetForegroundWindow=lambda: 701)
        with mock.patch.object(window, "_user32", api):
            for hwnd, rect in ((0, [0, 0, 1, 1]), (701, None), (701, [0, 0, 0, 800])):
                self.assertIsNone(window.foreground_evidence(SimpleNamespace(hwnd=hwnd, rect=lambda: rect)))
            api.IsWindow = mock.Mock(side_effect=OSError("window"))
            self.assertIsNone(window.foreground_evidence(SimpleNamespace(hwnd=701, rect=lambda: [0, 0, 1, 1])))
