# -*- coding: utf-8 -*-
import copy
import queue
import unittest
from unittest import mock

from mhxy.core.config import DEFAULT_CONFIG
from mhxy.gui.app import App
from mhxy.gui.quick_start import QuickStartPanel


class _Widget:
    def __init__(self):
        self.destroy_calls = 0

    def destroy(self):
        self.destroy_calls += 1


class _CompactStub:
    @property
    def is_compact(self):
        return self._compact


class _Button:
    def __init__(self):
        self.calls = []

    def configure(self, **kwargs):
        self.calls.append(kwargs)


class _PanelStub:
    def __init__(self):
        self.status = []
        self.btn_run = _Button()
        self.app = mock.Mock()
        self.refresh = mock.Mock()
        self._launched = False

    def _set_status(self, text):
        self.status.append(text)


class QuickStartShapeTests(unittest.TestCase):
    def test_compact_state_property(self):
        stub = _CompactStub()
        stub._compact = True
        self.assertTrue(App.is_compact.fget(stub))
        stub._compact = False
        self.assertFalse(App.is_compact.fget(stub))

    def test_reveal_compact_only_reveals_existing_panel(self):
        stub = _CompactStub()
        stub._compact = True
        stub._ensure_revealed = mock.Mock()

        App.reveal_compact(stub)
        App.reveal_compact(stub)

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


class QuickStartPanelTests(unittest.TestCase):
    def test_track_progress_updates_status_and_ignores_noise(self):
        stub = _PanelStub()

        QuickStartPanel._track_progress(stub, "[角色] 开始处理（2/3）。")
        QuickStartPanel._track_progress(stub, "not a launch message")

        self.assertEqual(stub.status, ["状态：第 2/3 个"])

    def test_run_button_tracks_enabled_profiles(self):
        stub = _PanelStub()
        empty = copy.deepcopy(DEFAULT_CONFIG)
        empty["account_launch"]["profiles"] = [{"id": "one", "enabled": False}]
        enabled = copy.deepcopy(empty)
        enabled["account_launch"]["profiles"][0]["enabled"] = True

        stub._enabled_count = lambda: QuickStartPanel._enabled_count(stub)
        with mock.patch("mhxy.gui.quick_start.cfg_mod.load_config", side_effect=[empty, enabled]):
            QuickStartPanel._sync_run_button(stub)
            QuickStartPanel._sync_run_button(stub)

        self.assertEqual(stub.btn_run.calls[0]["state"], "disabled")
        self.assertEqual(stub.btn_run.calls[1]["state"], "normal")

    def test_set_enabled_persists_to_config(self):
        stub = _PanelStub()
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        cfg["account_launch"]["profiles"] = [{"id": "one", "enabled": True}]

        with mock.patch("mhxy.gui.quick_start.cfg_mod.load_config", return_value=cfg), \
             mock.patch("mhxy.gui.quick_start.cfg_mod.save_config") as save_config:
            QuickStartPanel._set_enabled(stub, 0, False)

        self.assertFalse(cfg["account_launch"]["profiles"][0]["enabled"])
        save_config.assert_called_once_with(cfg)
        self.assertIs(stub.app.cfg, cfg)
        stub.refresh.assert_called_once_with()

    def test_roster_profiles_append_missing_roles_disabled(self):
        profiles = [{"id": "one", "enabled": True, "expected_role_id": "r1"}]
        roster = {
            "r1": {"name": "已有角色"},
            "r2": {"name": "偶尔登录的角色"},
        }

        merged, added = QuickStartPanel._merge_roster_profiles(profiles, roster)

        self.assertTrue(added)
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0], profiles[0])
        self.assertEqual(merged[1]["expected_role_id"], "r2")
        self.assertEqual(merged[1]["expected_role_name"], "偶尔登录的角色")
        self.assertFalse(merged[1]["enabled"])

    def test_enter_full_requires_game_window(self):
        stub = _PanelStub()
        stub._has_game_window = mock.Mock(return_value=False)
        stub._sync_run_button = mock.Mock()

        self.assertFalse(QuickStartPanel._enter_full_if_game_ready(stub))

        stub.app.enter_full.assert_not_called()
        self.assertEqual(stub.status, ["状态：未检测到游戏窗口"])
        stub._sync_run_button.assert_called_once_with()

    def test_pump_enters_full_only_after_game_window_ready(self):
        stub = _PanelStub()
        stub._launched = True
        runner = mock.Mock()
        runner.log_queue = queue.Queue()
        runner.log_queue.put(("info", "[角色] 开始处理（1/1）。"))
        runner.is_running.return_value = False
        stub.runner = runner
        stub.append = mock.Mock()
        stub._track_progress = mock.Mock()
        stub._enter_full_if_game_ready = mock.Mock(return_value=True)

        QuickStartPanel.pump(stub)

        stub.append.assert_called_once_with("[角色] 开始处理（1/1）。", "info")
        stub._track_progress.assert_called_once_with("[角色] 开始处理（1/1）。")
        stub._enter_full_if_game_ready.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
