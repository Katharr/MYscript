# -*- coding: utf-8 -*-
import unittest
from unittest import mock

from mhxy.core import accounts
from mhxy.core import rotation
from mhxy.core import window as win_mod
from mhxy.tasks.base import Task


class _Window:
    def __init__(self, rect=(10, 20, 800, 600), activates=True):
        self._rect = rect
        self._activates = activates
        self.activate_calls = 0

    def rect(self):
        return self._rect

    def activate(self):
        self.activate_calls += 1
        return self._activates


class _WakeWindow:
    def __init__(self, name, hwnd, events, foreground, activates=True):
        self.name = name
        self._win = type("Native", (), {"_hWnd": hwnd})()
        self._events = events
        self._foreground = foreground
        self._activates = activates

    def activate(self):
        self._events.append(("activate", self.name))
        if not self._activates:
            return False
        self._foreground[:] = [self]
        return True


class _Context:
    def __init__(self, window):
        self.window = window

    @staticmethod
    def should_stop():
        return False


class _NativeWindow:
    def __init__(self, left, top, minimized=False):
        self.left = left
        self.top = top
        self.isMinimized = minimized
        self._hWnd = 100 + left


class RuntimeWindowActivationTests(unittest.TestCase):
    def test_resolve_targets_includes_minimized_windows(self):
        selected = object()
        with mock.patch.object(win_mod, "locate_all", return_value=[selected]) as locate:
            result = win_mod.resolve_targets("game", (0, 0), {"single_index": 0})

        self.assertEqual(result, [selected])
        locate.assert_called_once_with("game", (0, 0), include_minimized=True)

    def test_minimized_window_keeps_its_normal_position_order(self):
        left = _NativeWindow(left=100, top=10)
        minimized = _NativeWindow(left=-32000, top=-32000, minimized=True)
        with mock.patch.object(win_mod.gw, "getAllWindows", return_value=[minimized, left]), \
             mock.patch.object(win_mod, "_match_basic", return_value=True), \
             mock.patch.object(win_mod, "_normal_position", side_effect=[(200, 10), (100, 10)]):
            wins = win_mod.locate_all("game", include_minimized=True)

        self.assertEqual([win._win for win in wins], [left, minimized])

    def test_wake_all_activates_in_reverse_and_callbacks_are_immediate(self):
        events = []
        foreground = []
        windows = [
            _WakeWindow("first", 201, events, foreground),
            _WakeWindow("second", 202, events, foreground, activates=False),
            _WakeWindow("third", 203, events, foreground),
        ]

        def on_woken(win):
            events.append(("callback", win.name))
            if win.name == "third":
                raise RuntimeError("callback failure")

        with mock.patch.object(win_mod, "locate_all", return_value=windows):
            result = win_mod.wake_all_to_front("game", (3, 4), on_woken=on_woken)

        self.assertEqual(result, (3, 2))
        self.assertEqual(events, [
            ("activate", "third"),
            ("callback", "third"),
            ("activate", "second"),
            ("activate", "first"),
            ("callback", "first"),
        ])
        self.assertIs(foreground[0], windows[0])

    def test_wake_all_callbacks_bind_each_hwnd_for_cached_labels(self):
        events = []
        foreground = []
        windows = [
            _WakeWindow("first", 301, events, foreground),
            _WakeWindow("second", 302, events, foreground),
        ]
        roster = {
            "r1": {"role_id": "r1", "name": "甲", "level": "41"},
            "r2": {"role_id": "r2", "name": "乙", "level": "52"},
        }
        roles = {301: "r1", 302: "r2"}
        labels_seen = []

        def on_woken(win):
            labels_seen.append((
                win.name,
                accounts.labels_for([win]),
                accounts.cached_labels_for([win]),
            ))

        def role_for_window(win, names, now):
            rid = roles[win._win._hWnd]
            with accounts._lock:
                accounts._identity[win._win._hWnd] = {
                    "role_id": rid,
                    "label": accounts.display_name(names[rid]),
                    "seen_at": now,
                }
            return rid

        old_identity = {}
        with accounts._lock:
            old_cache_order = list(accounts._cache["order"])
            for win in windows:
                old_identity[win._win._hWnd] = accounts._identity.pop(win._win._hWnd, None)
        try:
            with mock.patch.object(win_mod, "locate_all", return_value=windows), \
                 mock.patch.object(accounts, "roster", return_value=roster), \
                 mock.patch.object(accounts, "_role_for_window",
                                   side_effect=role_for_window):
                self.assertEqual(
                    win_mod.wake_all_to_front("game", on_woken=on_woken),
                    (2, 2),
                )

            self.assertEqual(labels_seen, [
                ("second", ["乙（52）"], ["乙（52）"]),
                ("first", ["甲（41）"], ["甲（41）"]),
            ])
            accounts.sync_window_labels(windows)
            self.assertEqual(accounts.cached_labels_for(windows), ["甲（41）", "乙（52）"])
            self.assertEqual(accounts.cached_labels(), ["甲（41）", "乙（52）"])
            self.assertEqual(accounts.cached_label(1), "乙（52）")
            self.assertEqual(accounts.cached_labels_for([windows[1]]), ["乙（52）"])
            self.assertEqual(accounts.cached_labels(), ["甲（41）", "乙（52）"])
        finally:
            with accounts._lock:
                accounts._cache["order"] = old_cache_order
                for win in windows:
                    accounts._identity.pop(win._win._hWnd, None)
                    if old_identity[win._win._hWnd] is not None:
                        accounts._identity[win._win._hWnd] = old_identity[win._win._hWnd]

    def test_task_preparation_activates_single_window(self):
        window = _Window()
        ctx = _Context(window)

        self.assertTrue(Task()._prepare_window(ctx, multi=False))
        self.assertEqual(window.activate_calls, 1)

    def test_rotation_activates_single_window(self):
        window = _Window()
        ctx = _Context(window)
        record = {"ctx": ctx, "state": "ready", "done": False}

        def step_once(rec):
            rec["done"] = True

        config = rotation.RotationConfig(
            records=[record],
            step_once=step_once,
            should_stop=lambda: False,
            log=lambda *args, **kwargs: None,
            multi=False,
            tick=0,
        )
        with mock.patch.object(rotation, "_sleep"):
            self.assertEqual(rotation.run_rotation(config), "ok")

        self.assertEqual(window.activate_calls, 1)


if __name__ == "__main__":
    unittest.main()
