# -*- coding: utf-8 -*-
import unittest
from unittest import mock

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
