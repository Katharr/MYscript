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
        self.append = mock.Mock()
        self._launched = False
        self._profile_count = 0
        self._roles_checked_at = 0.0
        self._roster_ids = set()
        self._sync_calibrate_button = lambda: None
        self._keep_behind_target = lambda: None
        self._maybe_refresh_roles = lambda: None

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
        stub._set_state = mock.Mock()
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

    def test_center_full_window_uses_current_work_area(self):
        stub = _PanelStub()
        stub.app.winfo_width.return_value = 1360
        stub.app.winfo_height.return_value = 720
        stub.app.winfo_id.return_value = 123

        with mock.patch("mhxy.gui.quick_start.win_mod.monitor_work_area",
                        return_value=(0, 0, 1920, 1032)) as work_area:
            QuickStartPanel._center_full_window(stub)

        stub.app.update_idletasks.assert_called_once_with()
        work_area.assert_called_once_with(123)
        stub.app.geometry.assert_called_once_with("+280+156")

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


class QuickStartLauncherTests(unittest.TestCase):
    """零窗口时小窗是唯一入口：首次使用者必须能自己选启动器、记住目录并读到角色名。"""

    def test_browse_launcher_saves_path_and_reports_roles(self):
        stub = _PanelStub()
        stub._profile_count = 2
        cfg = copy.deepcopy(DEFAULT_CONFIG)

        with mock.patch("mhxy.gui.quick_start.filedialog.askopenfilename", return_value=r"C:\Game\launcher.exe"), \
             mock.patch("mhxy.gui.quick_start.cfg_mod.load_config", return_value=cfg), \
             mock.patch("mhxy.gui.quick_start.cfg_mod.save_config") as save_config, \
             mock.patch("mhxy.gui.quick_start.accounts.set_launcher_path") as set_path:
            QuickStartPanel._browse_launcher(stub)

        self.assertEqual(cfg["account_launch"]["launcher_path"], r"C:\Game\launcher.exe")
        save_config.assert_called_once_with(cfg)
        set_path.assert_called_once_with(r"C:\Game\launcher.exe")
        stub.refresh.assert_called_once_with()
        self.assertEqual(stub.append.call_args[0][1], "hit")

    def test_browse_launcher_cancel_changes_nothing(self):
        stub = _PanelStub()
        cfg = copy.deepcopy(DEFAULT_CONFIG)

        with mock.patch("mhxy.gui.quick_start.filedialog.askopenfilename", return_value=""), \
             mock.patch("mhxy.gui.quick_start.cfg_mod.load_config", return_value=cfg), \
             mock.patch("mhxy.gui.quick_start.cfg_mod.save_config") as save_config:
            QuickStartPanel._browse_launcher(stub)

        save_config.assert_not_called()
        stub.refresh.assert_not_called()

    def test_browse_launcher_warns_when_roster_still_empty(self):
        stub = _PanelStub()
        stub._profile_count = 0
        cfg = copy.deepcopy(DEFAULT_CONFIG)

        with mock.patch("mhxy.gui.quick_start.filedialog.askopenfilename", return_value=r"C:\Game\launcher.exe"), \
             mock.patch("mhxy.gui.quick_start.cfg_mod.load_config", return_value=cfg), \
             mock.patch("mhxy.gui.quick_start.cfg_mod.save_config"), \
             mock.patch("mhxy.gui.quick_start.accounts.set_launcher_path"):
            QuickStartPanel._browse_launcher(stub)

        self.assertEqual(stub.append.call_args[0][1], "warn")
        self.assertEqual(stub.status[-1], "状态：未读到角色名册")

    def test_remember_game_dir_writes_once(self):
        stub = _PanelStub()
        cfg = copy.deepcopy(DEFAULT_CONFIG)

        with mock.patch("mhxy.gui.quick_start.accounts.data_dir", return_value=r"C:\Game\LocalData"), \
             mock.patch("mhxy.gui.quick_start.accounts.set_game_dir") as set_dir:
            self.assertTrue(QuickStartPanel._remember_game_dir(stub, cfg))

        self.assertEqual(cfg["game_dir"], r"C:\Game")
        set_dir.assert_called_once_with(r"C:\Game")
        self.assertIs(stub.app.cfg, cfg)
        # 已经记过就不再写：避免每次 refresh 都改配置
        self.assertFalse(QuickStartPanel._remember_game_dir(stub, cfg))

    def test_sync_roster_profiles_also_remembers_game_dir(self):
        stub = _PanelStub()
        stub._merge_roster_profiles = QuickStartPanel._merge_roster_profiles
        stub._remember_game_dir = lambda cfg: QuickStartPanel._remember_game_dir(stub, cfg)
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        roster = {"r1": {"name": "角色甲"}}

        with mock.patch("mhxy.gui.quick_start.accounts.roster", return_value=roster), \
             mock.patch("mhxy.gui.quick_start.accounts.data_dir", return_value=r"C:\Game\LocalData"), \
             mock.patch("mhxy.gui.quick_start.accounts.set_game_dir"), \
             mock.patch("mhxy.gui.quick_start.cfg_mod.save_config") as save_config:
            merged = QuickStartPanel._sync_roster_profiles(stub, cfg)

        self.assertEqual(len(merged), 1)
        self.assertEqual(cfg["game_dir"], r"C:\Game")
        save_config.assert_called_once_with(cfg)
        self.assertEqual(stub._roster_ids, {"r1"})

    def test_maybe_refresh_roles_picks_up_late_roster(self):
        stub = _PanelStub()
        stub._profile_count = 0
        stub._ROLE_REFRESH_SEC = QuickStartPanel._ROLE_REFRESH_SEC

        with mock.patch("mhxy.gui.quick_start.accounts.roster",
                        return_value={"r9": {"name": "后到的角色"}}) as roster:
            QuickStartPanel._maybe_refresh_roles(stub)

        roster.assert_called_once_with()
        stub.refresh.assert_called_once_with()

    def test_maybe_refresh_roles_skips_when_profiles_exist(self):
        stub = _PanelStub()
        stub._profile_count = 3

        with mock.patch("mhxy.gui.quick_start.accounts.roster") as roster:
            QuickStartPanel._maybe_refresh_roles(stub)

        roster.assert_not_called()
        stub.refresh.assert_not_called()

    def test_pump_polls_roster(self):
        stub = _PanelStub()
        stub._maybe_refresh_roles = mock.Mock()
        stub.runner = None

        QuickStartPanel.pump(stub)

        stub._maybe_refresh_roles.assert_called_once_with()


class QuickStartCalibrationTests(unittest.TestCase):
    """零窗口时小窗是唯一入口：一键启动的标定必须也能在小窗里做。"""

    def test_open_calibrate_uses_launch_login_task(self):
        stub = _PanelStub()
        stub._cal_dialog = None
        opened = {}

        class _Dialog:
            def __init__(self, app, task_name=None, on_done=None):
                opened["app"], opened["task_name"], opened["on_done"] = app, task_name, on_done

        with mock.patch("mhxy.gui.calibrate_dialog.CalibrateDialog", _Dialog):
            QuickStartPanel._open_calibrate(stub)

        self.assertIs(opened["app"], stub.app)
        self.assertEqual(opened["task_name"], "launch_login")
        self.assertIsInstance(stub._cal_dialog, _Dialog)

    def test_open_calibrate_raises_existing_dialog_instead_of_duplicating(self):
        stub = _PanelStub()
        existing = mock.Mock()
        stub._cal_dialog = existing

        with mock.patch("mhxy.gui.calibrate_dialog.CalibrateDialog") as dialog_cls:
            QuickStartPanel._open_calibrate(stub)

        dialog_cls.assert_not_called()
        existing.lift.assert_called_once_with()
        existing.focus_force.assert_called_once_with()

    def test_open_calibrate_failure_is_reported_not_raised(self):
        stub = _PanelStub()
        stub._cal_dialog = None
        stub.append = mock.Mock()

        with mock.patch("mhxy.gui.calibrate_dialog.CalibrateDialog",
                        side_effect=RuntimeError("boom")):
            QuickStartPanel._open_calibrate(stub)

        self.assertIsNone(stub._cal_dialog)
        self.assertTrue(stub.append.call_args[0][0].startswith("打开标定向导失败"))
        self.assertEqual(stub.append.call_args[0][1], "error")

    def test_calibrate_button_disabled_while_launch_running(self):
        stub = _PanelStub()
        stub.btn_calibrate = _Button()
        stub.btn_launcher = _Button()
        sync = QuickStartPanel._sync_calibrate_button
        stub._launched = True
        sync(stub)
        self.assertEqual(stub.btn_calibrate.calls[-1]["state"], "disabled")
        self.assertEqual(stub.btn_launcher.calls[-1]["state"], "disabled")

        stub._launched = False
        sync(stub)
        self.assertEqual(stub.btn_calibrate.calls[-1]["state"], "normal")
        self.assertEqual(stub.btn_launcher.calls[-1]["state"], "normal")

    def test_keep_behind_target_pushes_panel_below_target(self):
        stub = _PanelStub()
        stub.app.winfo_id.return_value = 555
        stub._target_hwnd = lambda: 999
        with mock.patch("mhxy.gui.quick_start.win_mod.toplevel_hwnd", return_value=111) as root, \
             mock.patch("mhxy.gui.quick_start.win_mod.is_window_above", return_value=True), \
             mock.patch("mhxy.gui.quick_start.win_mod.place_below") as below:
            QuickStartPanel._keep_behind_target(stub)
        root.assert_called_once_with(555)
        below.assert_called_once_with(111, 999)

    def test_keep_behind_target_leaves_panel_alone_when_already_below(self):
        stub = _PanelStub()
        stub.app.winfo_id.return_value = 555
        stub._target_hwnd = lambda: 999
        with mock.patch("mhxy.gui.quick_start.win_mod.toplevel_hwnd", return_value=111), \
             mock.patch("mhxy.gui.quick_start.win_mod.is_window_above", return_value=False), \
             mock.patch("mhxy.gui.quick_start.win_mod.place_below") as below:
            QuickStartPanel._keep_behind_target(stub)
        below.assert_not_called()

    def test_keep_behind_target_does_nothing_without_target(self):
        stub = _PanelStub()
        stub._target_hwnd = lambda: 0
        with mock.patch("mhxy.gui.quick_start.win_mod.place_below") as below:
            QuickStartPanel._keep_behind_target(stub)
        below.assert_not_called()

    def test_pump_keeps_panel_behind_target_while_running(self):
        stub = _PanelStub()
        stub._launched = True
        stub._dodged_at = 0.0
        stub._keep_behind_target = mock.Mock()
        runner = mock.Mock()
        runner.log_queue = queue.Queue()
        runner.is_running.return_value = True
        stub.runner = runner

        QuickStartPanel.pump(stub)

        stub._keep_behind_target.assert_called_once_with()

    def test_done_callback_refreshes_and_logs(self):
        stub = _PanelStub()
        stub._cal_dialog = None
        stub.append = mock.Mock()
        captured = {}

        class _Dialog:
            def __init__(self, app, task_name=None, on_done=None):
                captured["on_done"] = on_done

        with mock.patch("mhxy.gui.calibrate_dialog.CalibrateDialog", _Dialog):
            QuickStartPanel._open_calibrate(stub)
        captured["on_done"]()

        self.assertIsNone(stub._cal_dialog)
        stub.refresh.assert_called_once_with()
        self.assertIn("标定完成", stub.append.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
