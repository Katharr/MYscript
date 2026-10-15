# -*- coding: utf-8 -*-
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from mhxy.core import window_probe as probe


class QtWindowProbeTests(unittest.TestCase):
    def window(self, hwnd, *, minimized=False, rect=(0, 0, 800, 600)):
        return SimpleNamespace(_win=SimpleNamespace(_hWnd=hwnd, isMinimized=minimized),
                               rect=Mock(return_value=rect))

    def test_qt_scan_includes_minimized_and_only_reads_identity_cache(self):
        from mhxy.core import accounts, window as win_mod
        from mhxy.qtui.window import scan_windows
        wins = [self.window(101), self.window(102, minimized=True, rect=None)]
        with patch.object(win_mod, "locate_all", return_value=wins) as locate, \
             patch.object(win_mod, "set_game_process") as process, \
             patch.object(accounts, "set_game_dir"), \
             patch.object(accounts, "set_launcher_path"), \
             patch.object(accounts, "_identity", {101: {"label": "示例甲（50）"}}), \
             patch.object(accounts, "_cache", {"order": []}), \
             patch.object(accounts, "labels_for") as identify, \
             patch.object(accounts, "prime_visible_labels") as prime, \
             patch.object(accounts, "_compute") as compute:
            states = scan_windows({"window_title": "game", "window_offset": [2, 3],
                                   "window_process": "CustomGame.exe"})
            locate.assert_called_once_with("game", [2, 3], include_minimized=True)
            process.assert_called_once_with("CustomGame.exe")
            self.assertEqual(accounts.cached_labels(), ["示例甲（50）", "号2"])
            self.assertEqual([(s.hwnd, s.label, s.status, s.minimized) for s in states],
                             [(101, "示例甲（50）", "ready", False), (102, "号2", "pending", True)])
            identify.assert_not_called()
            prime.assert_not_called()
            compute.assert_not_called()
            wins[1].rect.assert_not_called()

    def test_qt_scan_keeps_one_failed_window_separate_from_healthy_window(self):
        from mhxy.core import accounts, window as win_mod
        from mhxy.qtui.window import scan_windows
        wins = [self.window(101, rect=None), self.window(102)]
        with patch.object(win_mod, "locate_all", return_value=wins), \
             patch.object(accounts, "sync_window_labels", return_value=["甲", "乙"]), \
             patch.object(accounts, "set_game_dir"), patch.object(accounts, "set_launcher_path"):
            states = scan_windows({})
        self.assertEqual([s.status for s in states], ["failed", "ready"])

    def test_qt_selection_order_matches_runtime_including_limit_and_fallback(self):
        from mhxy.core import window as win_mod
        from mhxy.qtui.role_window_page import target_indices
        wins = [self.window(i) for i in range(4)]
        cases = [
            {}, {"single_index": 2}, {"single_index": 99},
            {"multi": True, "multi_indices": [], "max_windows": 3},
            {"multi": True, "multi_indices": [0, 2], "max_windows": 0},
            {"multi": True, "multi_indices": [2, 0], "max_windows": 1},
            {"multi": True, "multi_indices": [99]},
        ]
        for targets in cases:
            with self.subTest(targets=targets), patch.object(win_mod, "locate_all", return_value=wins):
                actual = win_mod.resolve_targets("game", (0, 0), targets)
                self.assertEqual([wins.index(win) for win in actual],
                                 target_indices(targets, len(wins), apply_limit=True))


class _Window:
    def __init__(self, title="梦幻西游：时空", left=10, top=20, width=500, height=400,
                 minimized=False, exe="mytabctrl_x64r.exe", pid=1, hwnd=100):
        self.title = title
        self.left = left
        self.top = top
        self.width = width
        self.height = height
        self.isMinimized = minimized
        self.exe = exe
        self.pid = pid
        self._hWnd = hwnd


class WindowProbeTests(unittest.TestCase):
    def test_parse_process_names(self):
        self.assertEqual(probe.parse_process_names(" Game.exe "), frozenset({"game.exe"}))
        expected = frozenset({"game.exe", "shell.exe"})
        for value in (
            "Game.exe,Shell.exe",
            "Game.exe，Shell.exe",
            "Game.exe;Shell.exe",
            "Game.exe；Shell.exe",
            "Game.exe|Shell.exe",
            "Game.exe Shell.exe",
            ["Game.exe", "Shell.exe"],
        ):
            with self.subTest(value=value):
                self.assertEqual(probe.parse_process_names(value), expected)
        for value in (None, "", "   ", [], set()):
            with self.subTest(value=value):
                self.assertIsNone(probe.parse_process_names(value))

    def test_is_game_window_preserves_legacy_rules(self):
        names = frozenset({"mytabctrl_x64r.exe"})
        valid = _Window()
        self.assertTrue(probe.is_game_window(valid, "梦幻西游", names))
        self.assertFalse(probe.is_game_window(_Window(title="其它窗口"), "梦幻西游", names))
        self.assertFalse(probe.is_game_window(_Window(width=100), "梦幻西游", names))
        self.assertFalse(probe.is_game_window(_Window(height=100), "梦幻西游", names))
        minimized = _Window(width=20, height=20, minimized=True)
        self.assertFalse(probe.is_game_window(minimized, "梦幻西游", names))
        self.assertTrue(probe.is_game_window(minimized, "梦幻西游", names,
                                             allow_minimized=True))
        self.assertFalse(probe.is_game_window(_Window(exe="other.exe"), "梦幻西游", names))
        self.assertTrue(probe.is_game_window(_Window(exe="other.exe"), "梦幻西游", None))

    def test_list_game_windows_partitions_evidence(self):
        visible = _Window(left=300, pid=10, hwnd=110)
        minimized = _Window(left=20, top=160, minimized=True, width=20, height=20,
                            pid=11, hwnd=111)
        ignored = _Window(title="其它窗口", pid=12, hwnd=112)
        processes = [
            {"pid": 10, "ppid": 1, "exe": "mytabctrl_x64r.exe"},
            {"pid": 11, "ppid": 1, "exe": "mygame_x64r.exe"},
            {"pid": 13, "ppid": 1, "exe": "mypclauncher_x64r.exe"},
        ]
        kwargs = {
            "enumerator": lambda: [visible, minimized, ignored],
            "process_lister": lambda: processes,
        }
        result = probe.list_game_windows("梦幻西游", "MyTabCtrl_x64r.exe,MyGame_x64r.exe",
                                         **kwargs)
        self.assertEqual(result.visible, (result.visible[0],))
        self.assertEqual(result.visible[0].native, visible)
        self.assertEqual(result.minimized, ())
        self.assertTrue(result.running)
        self.assertTrue(result.launcher_running)
        self.assertTrue(result.any)
        self.assertEqual(result.count, 1)

        included = probe.list_game_windows(
            "梦幻西游", "MyTabCtrl_x64r.exe,MyGame_x64r.exe",
            include_minimized=True, **kwargs)
        self.assertEqual([item.native for item in included.visible], [visible])
        self.assertEqual([item.native for item in included.minimized], [minimized])
        self.assertEqual(included.count, 2)
        self.assertEqual(included.windows, included.visible + included.minimized)

    def test_list_game_windows_isolates_enumerator_and_process_errors(self):
        def broken_enumerator():
            raise RuntimeError("desktop unavailable")

        result = probe.list_game_windows(
            "梦幻西游", "MyGame_x64r.exe", enumerator=broken_enumerator,
            process_lister=lambda: [{"pid": 1, "exe": "mygame_x64r.exe"}])
        self.assertEqual(result.windows, ())
        self.assertTrue(result.running)

        def broken_processes():
            raise RuntimeError("snapshot unavailable")

        native = _Window(exe="mygame_x64r.exe")
        result = probe.list_game_windows(
            "梦幻西游", "MyGame_x64r.exe", enumerator=lambda: [native],
            process_lister=broken_processes)
        self.assertEqual([item.native for item in result.visible], [native])
        self.assertFalse(result.running)

    def test_position_sort_key_groups_rows_then_columns(self):
        windows = [
            _Window(left=80, top=250),
            _Window(left=100, top=10),
            _Window(left=20, top=100),
            _Window(left=10, top=130),
        ]
        ordered = sorted(windows, key=probe.position_sort_key)
        self.assertEqual(ordered, [windows[2], windows[1], windows[3], windows[0]])

    def test_startup_decision_has_for_visible_window(self):
        calls = {"enumerator": 0, "process_lister": 0}

        def enumerator():
            calls["enumerator"] += 1
            return [_Window()]

        def process_lister():
            calls["process_lister"] += 1
            return []

        result = probe.startup_decision(
            {"window_title": "梦幻西游", "window_process": "MyTabCtrl_x64r.exe"},
            enumerator=enumerator,
            process_lister=process_lister,
        )
        self.assertEqual(result, "has")
        self.assertEqual(calls, {"enumerator": 1, "process_lister": 1})

    def test_startup_decision_has_for_minimized_window(self):
        result = probe.startup_decision(
            {"window_process": "MyTabCtrl_x64r.exe"},
            enumerator=lambda: [_Window(minimized=True, width=20, height=20)],
            process_lister=lambda: [],
        )
        self.assertEqual(result, "has")

    def test_startup_decision_has_for_running_process(self):
        result = probe.startup_decision(
            {"window_process": "MyGame_x64r.exe"},
            enumerator=lambda: [],
            process_lister=lambda: [{"pid": 1, "exe": "mygame_x64r.exe"}],
        )
        self.assertEqual(result, "has")

    def test_startup_decision_none_without_window_or_process(self):
        result = probe.startup_decision(
            {"window_process": "MyGame_x64r.exe"},
            enumerator=lambda: [],
            process_lister=lambda: [],
        )
        self.assertEqual(result, "none")

    def test_startup_decision_unknown_when_probe_raises(self):
        def broken_enumerator():
            raise RuntimeError("desktop unavailable")

        result = probe.startup_decision(
            {"window_process": "MyGame_x64r.exe"},
            enumerator=broken_enumerator,
            process_lister=lambda: [],
        )
        self.assertEqual(result, "unknown")

    def test_startup_decision_defaults_process_spec(self):
        result = probe.startup_decision(
            {},
            enumerator=lambda: [],
            process_lister=lambda: [{"pid": 1, "exe": "mygame_x64r.exe"}],
        )
        self.assertEqual(result, "has")

    def test_startup_decision_uses_cfg_process_spec_not_window_global(self):
        from mhxy.core import window as win_mod

        win_mod.set_game_process("other.exe")
        try:
            result = probe.startup_decision(
                {"window_process": "CustomGame.exe"},
                enumerator=lambda: [],
                process_lister=lambda: [{"pid": 1, "exe": "customgame.exe"}],
            )
        finally:
            win_mod.set_game_process(probe.DEFAULT_GAME_PROCESS_SPEC)
        self.assertEqual(result, "has")


if __name__ == "__main__":
    unittest.main()
