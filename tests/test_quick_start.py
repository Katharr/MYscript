# -*- coding: utf-8 -*-
import unittest
from unittest import mock

from mhxy.gui.app import App


class _Widget:
    def __init__(self):
        self.destroy_calls = 0

    def destroy(self):
        self.destroy_calls += 1


class _CompactStub:
    @property
    def is_compact(self):
        return self._compact


class QuickStartShapeTests(unittest.TestCase):
    def test_compact_state_property(self):
        stub = _CompactStub()
        stub._compact = True
        self.assertTrue(App.is_compact.fget(stub))
        stub._compact = False
        self.assertFalse(App.is_compact.fget(stub))

    def test_reveal_compact_builds_placeholder_once(self):
        stub = _CompactStub()
        stub._compact = True
        stub._compact_placeholder = None
        stub.fonts = {"h2": object()}
        stub._ensure_revealed = mock.Mock()

        with mock.patch("mhxy.gui.app.ctk.CTkLabel") as label_cls:
            label = label_cls.return_value
            App.reveal_compact(stub)
            App.reveal_compact(stub)

        label_cls.assert_called_once()
        label.place.assert_called_once_with(relx=0.5, rely=0.5, anchor="center")
        self.assertIs(stub._compact_placeholder, label)
        self.assertEqual(stub._ensure_revealed.call_count, 2)

    def test_enter_full_is_idempotent(self):
        stub = _CompactStub()
        stub._compact = True
        quick_panel = _Widget()
        placeholder = _Widget()
        stub.quick_panel = quick_panel
        stub._compact_placeholder = placeholder
        stub.geometry = mock.Mock()
        stub.minsize = mock.Mock()
        stub.resizable = mock.Mock()
        stub.grid_columnconfigure = mock.Mock()
        stub.grid_rowconfigure = mock.Mock()
        stub._build_sidebar = mock.Mock()
        stub._build_log_panel = mock.Mock()
        stub._build_pages = mock.Mock()
        stub._build_pages_with_overlay = mock.Mock()
        stub._show = mock.Mock()
        stub._prebuild_idle = mock.Mock()
        stub.after = mock.Mock()

        App.enter_full(stub)

        self.assertFalse(stub._compact)
        self.assertIsNone(stub.quick_panel)
        self.assertIsNone(stub._compact_placeholder)
        self.assertEqual(quick_panel.destroy_calls, 1)
        self.assertEqual(placeholder.destroy_calls, 1)
        stub.geometry.assert_called_once_with("1360x720")
        stub.minsize.assert_called_once_with(1180, 640)
        stub.resizable.assert_called_once_with(True, True)
        stub._build_sidebar.assert_called_once_with()
        stub._build_log_panel.assert_called_once_with()
        stub._build_pages.assert_called_once_with()
        stub._build_pages_with_overlay.assert_called_once_with()
        stub._show.assert_called_once_with("general")
        stub.after.assert_called_once_with(800, stub._prebuild_idle)

        App.enter_full(stub)
        stub._build_sidebar.assert_called_once_with()
        stub._build_pages_with_overlay.assert_called_once_with()

    def test_emergency_stop_is_safe_without_task_pages(self):
        stub = _CompactStub()
        stub.cfg = {"hotkey_stop": "ctrl+alt+F12"}
        stub.stop_all_tasks = mock.Mock(return_value=0)
        stub.toast = mock.Mock()

        App._emergency_stop(stub)

        stub.stop_all_tasks.assert_called_once_with()
        stub.toast.assert_called_once_with("[ctrl+alt+F12] 急停（当前没有正在跑的任务）")

    def test_log_line_reaches_compact_sink_without_global_log(self):
        stub = _CompactStub()
        stub.log = None
        stub.float_log = mock.Mock()
        stub.quick_panel = mock.Mock()

        with mock.patch("mhxy.gui.app.T.append_log") as append_log:
            App.log_line(stub, "启动中", "info", "启动")

        append_log.assert_not_called()
        stub.float_log.append.assert_called_once_with("启动中", "info", "启动")
        stub.quick_panel.append.assert_called_once_with("启动中", "info", "启动")


if __name__ == "__main__":
    unittest.main()
