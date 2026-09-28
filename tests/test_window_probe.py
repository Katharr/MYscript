# -*- coding: utf-8 -*-
import unittest

from mhxy.core import window_probe as probe


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


if __name__ == "__main__":
    unittest.main()
