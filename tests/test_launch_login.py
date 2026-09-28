# -*- coding: utf-8 -*-
import copy
import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

from mhxy.core import accounts
from mhxy.core import launcher
from mhxy.core.config import DEFAULT_CONFIG
from mhxy.tasks.launch_login import LaunchLoginTask


class _Ctx:
    def __init__(self, cfg):
        self.cfg = cfg

    def task_cfg(self, name):
        return self.cfg["tasks"][name]


class _Window:
    class _Native:
        _hWnd = 778899

    _win = _Native()


class LaunchLoginTests(unittest.TestCase):
    def test_normalize_existing_launcher_path(self):
        expected = os.path.abspath(sys.executable)
        self.assertEqual(launcher.normalize_path(sys.executable), expected)
        self.assertEqual(launcher.normalize_path("start.py"), "")
        self.assertEqual(launcher.normalize_path("missing-launcher.exe"), "")

    def test_auto_detect_accepts_only_official_launcher_name(self):
        official = Path("C:/game") / launcher.LAUNCHER_EXE
        wrong = Path("C:/game/MyGame_x64r.exe")
        found, seen = [], set()
        with mock.patch.object(Path, "is_file", return_value=True):
            self.assertFalse(launcher._append_launcher(found, seen, wrong, 8))
            self.assertEqual(found, [])
            self.assertFalse(launcher._append_launcher(found, seen, official, 8))
        self.assertEqual(found, [str(official.resolve())])

    def test_preflight_accepts_complete_live_profile(self):
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        cfg["account_launch"]["launcher_path"] = os.path.abspath(sys.executable)
        cfg["account_launch"]["profiles"] = [{
            "id": "main", "label": "主号", "enabled": True,
            "expected_role_id": "r1", "expected_role_name": "角色",
        }]
        cfg["tasks"]["launch_login"]["dry_run"] = False
        for key in cfg["tasks"]["launch_login"]["templates"]:
            cfg["tasks"]["launch_login"]["templates"][key] = key + ".png"
        ctx = _Ctx(cfg)
        task = LaunchLoginTask()
        with mock.patch.object(task, "_is_admin", return_value=True), \
             mock.patch("mhxy.tasks.launch_login.vision.load_template", return_value=object()), \
             mock.patch("mhxy.tasks.launch_login.win_mod.locate_all", return_value=[]), \
             mock.patch("mhxy.tasks.launch_login.accounts.roster", return_value={"r1": {"name": "角色"}}):
            ok, problems = task.preflight(ctx)
        self.assertTrue(ok, problems)

    def test_preflight_blocks_non_admin(self):
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        ctx = _Ctx(cfg)
        task = LaunchLoginTask()
        with mock.patch.object(task, "_is_admin", return_value=False), \
             mock.patch("mhxy.tasks.launch_login.win_mod.locate_all", return_value=[]), \
             mock.patch("mhxy.tasks.launch_login.accounts.roster", return_value={}):
            ok, problems = task.preflight(ctx)
        self.assertFalse(ok)
        self.assertTrue(any("不是管理员" in item for item in problems))

    def test_state_template_uses_next_expected_screen(self):
        task = LaunchLoginTask()
        templates = {"start_game": "start", "enter_game": "enter", "existing_role": "existing"}
        self.assertEqual(task._template_for_state("WAIT_START", templates), "start")
        self.assertEqual(task._template_for_state("WAIT_ENTER", templates), "enter")
        self.assertIsNone(task._template_for_state("WAIT_ROLE", templates))
        self.assertEqual(task._previous_template_for_state("WAIT_ENTER", templates),
                         ("start", "开始游戏"))
        self.assertEqual(task._previous_template_for_state("WAIT_ROLE", templates),
                         (templates.get("existing_role"), "已有角色"))
        self.assertIsNone(task._previous_template_for_state("WAIT_START", templates))
        # 通用规则：每一步没成功都回查上一步，故 WAIT_EXISTING 也要回查到“切换”。
        self.assertEqual(task._previous_template_for_state("WAIT_EXISTING", {"switch_role": "sw"}),
                         ("sw", "切换"))

    def test_recheck_and_switch_defaults_match_user_request(self):
        loop = DEFAULT_CONFIG["tasks"]["launch_login"]["loop"]
        self.assertEqual(loop["switch_wait_min_sec"], 2.0)
        self.assertEqual(loop["switch_wait_max_sec"], 3.0)
        self.assertGreaterEqual(loop["recheck_max"], 2)          # 反复回查，不是只补点一次
        self.assertGreaterEqual(loop["recheck_interval_sec"], 1.0)

    def test_switch_waits_then_retries_until_existing_appears(self):
        """顺序验证：进入游戏页后先等待再点“切换”；“已有角色”没出现时反复回查“切换”。"""
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        cfg["account_launch"]["launcher_path"] = os.path.abspath(sys.executable)
        cfg["account_launch"]["profiles"] = [{
            "id": "main", "label": "主号", "enabled": True,
            "expected_role_id": "r1", "expected_role_name": "角色",
        }]
        tc = cfg["tasks"]["launch_login"]
        tc["dry_run"] = False
        tc["templates"] = {key: key + ".png" for key in tc["templates"]}
        tc["loop"].update({"switch_wait_min_sec": 0.2, "switch_wait_max_sec": 0.2,
                           "state_timeout_sec": 5.0})

        task = LaunchLoginTask()
        events = []
        seen = {"switch_retries": 0}

        class _Win:
            def rect(self): return [0, 0, 800, 600]
            def activate(self): return True

        class _RunCtx(_Ctx):
            def should_stop(self): return False
            def log(self, msg, level="info"): events.append((time.time(), "log:" + msg))

        def fake_click(ctx, win, tpl, threshold, action):
            events.append((time.time(), action))
            if action == "重新点击切换":
                seen["switch_retries"] += 1
            if action == "点击已有角色":
                return seen["switch_retries"] >= 3      # 回查 3 次后才出现“已有角色”
            return True

        with mock.patch.object(task, "_desktop_hwnds", return_value=set()), \
             mock.patch.object(task, "_wait_new_window", return_value=_Win()), \
             mock.patch.object(task, "_interruptible_sleep", lambda *a: None), \
             mock.patch.object(task, "_click_template", fake_click), \
             mock.patch.object(task, "_click_roster_role", return_value=True), \
             mock.patch("mhxy.tasks.launch_login.launcher.launch", return_value=(True, "")):
            result = task._run_profile(_RunCtx(cfg), cfg["account_launch"]["profiles"][0],
                                      {k: object() for k in tc["templates"]}, 0.85, 5.0, 1.0,
                                      {"after": 0.05, "interval": 0.05, "max": 3},
                                      {"wait_min": 0.2, "wait_max": 0.2}, False)

        actions = [a for _t, a in events]
        self.assertEqual(result, "ok")
        enter_at = next(t for t, a in events if a == "点击进入游戏")
        switch_at = next(t for t, a in events if a == "点击切换角色")
        self.assertGreaterEqual(switch_at - enter_at, 0.2)          # 先等 2~3 秒再点切换
        self.assertEqual(actions.count("重新点击切换"), 3)          # 反复回查切换，不限一次
        self.assertEqual([a for a in actions if not a.startswith("log:")][-1], "点击已有角色")

    def test_any_step_repeatedly_rechecks_previous(self):
        """通用规则验证：不是只有“切换”步反复回查，任何步没成功都反复回查上一步。"""
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        cfg["account_launch"]["profiles"] = [{
            "id": "main", "label": "主号", "enabled": True,
            "expected_role_id": "r1", "expected_role_name": "角色",
        }]
        tc = cfg["tasks"]["launch_login"]
        tc["dry_run"] = False
        task = LaunchLoginTask()
        actions = []
        seen = {"start_retries": 0}

        class _Win:
            def rect(self): return [0, 0, 800, 600]
            def activate(self): return True

        class _RunCtx(_Ctx):
            def should_stop(self): return False
            def log(self, msg, level="info"): pass

        def fake_click(ctx, win, tpl, threshold, action):
            actions.append(action)
            if action == "重新点击开始游戏":
                seen["start_retries"] += 1
            if action == "点击进入游戏":
                return seen["start_retries"] >= 3      # 回查“开始游戏”3 次后才出现“进入游戏”
            return True

        with mock.patch.object(task, "_desktop_hwnds", return_value=set()), \
             mock.patch.object(task, "_wait_new_window", return_value=_Win()), \
             mock.patch.object(task, "_interruptible_sleep", lambda *a: None), \
             mock.patch.object(task, "_click_template", fake_click), \
             mock.patch.object(task, "_click_roster_role", return_value=True), \
             mock.patch("mhxy.tasks.launch_login.launcher.launch", return_value=(True, "")):
            result = task._run_profile(_RunCtx(cfg), cfg["account_launch"]["profiles"][0],
                                      {k: object() for k in tc["templates"]}, 0.85, 5.0, 1.0,
                                      {"after": 0.05, "interval": 0.05, "max": 4},
                                      {"wait_min": 0.0, "wait_max": 0.0}, False)
        self.assertEqual(result, "ok")
        self.assertEqual(actions.count("重新点击开始游戏"), 3)

    def test_corner_geometry_and_cycle(self):
        from mhxy.core import window as win_mod
        with mock.patch("mhxy.core.window.monitor_work_area", return_value=(0, 0, 1920, 1032)):
            self.assertEqual(win_mod.corner_left_top("top_left", 800, 600), (0, 0))
            self.assertEqual(win_mod.corner_left_top("top_right", 800, 600), (1120, 0))
            self.assertEqual(win_mod.corner_left_top("bottom_right", 800, 600), (1120, 432))
            self.assertEqual(win_mod.corner_left_top("bottom_left", 800, 600), (0, 432))
            self.assertEqual(win_mod.corner_left_top("top_left", 800, 600, margin=10), (10, 10))
            # 窗口比工作区还大 → 钳回左上角，标题栏绝不被推到屏幕外
            self.assertEqual(win_mod.corner_left_top("bottom_right", 3000, 2000), (0, 0))
        # 用户拍板顺序：左上→右上→右下→左下，多于 4 个循环
        order = list(win_mod.CORNER_ORDER)
        self.assertEqual([LaunchLoginTask._corner_for(i, order) for i in range(5)],
                         ["top_left", "top_right", "bottom_right", "bottom_left", "top_left"])

    def test_place_after_login_moves_window_to_corner(self):
        task = LaunchLoginTask()
        moved = []

        class _Win:
            def move_to_corner(self, corner, margin=0):
                moved.append((corner, margin))
                return [1120, 0, 800, 600]

        logs = []

        class _RunCtx(_Ctx):
            def should_stop(self): return False
            def log(self, msg, level="info"): logs.append((level, msg))

        with mock.patch.object(task, "_interruptible_sleep", lambda *a: None), \
             mock.patch.object(task, "_resolve_game_window", return_value=_Win()):
            task._place_after_login(_RunCtx(DEFAULT_CONFIG), object(), set(), "top_right", 0.0, 0)
        self.assertEqual(moved, [("top_right", 0)])
        self.assertTrue(any("右上" in msg for _lv, msg in logs))

        # corner=None（未开启归位）→ 不移动窗口
        with mock.patch.object(task, "_interruptible_sleep", lambda *a: None), \
             mock.patch.object(task, "_resolve_game_window", return_value=_Win()):
            task._place_after_login(_RunCtx(DEFAULT_CONFIG), object(), set(), None, 0.0, 0)
        self.assertEqual(moved, [("top_right", 0)])

    def test_desktop_windows_skips_script_own_windows(self):
        """悬浮日志窗置顶且日志文字含「开始游戏」等字样，绝不能被当成候选窗口。"""
        task = LaunchLoginTask()

        class _W:
            def __init__(self, hwnd):
                self._hWnd, self.isMinimized = hwnd, False
                self.left, self.top, self.width, self.height = 100, 100, 800, 600

        wins = [_W(11), _W(22)]
        with mock.patch("mhxy.tasks.launch_login.win_mod.gw.getAllWindows", return_value=wins), \
             mock.patch("mhxy.tasks.launch_login.win_mod.is_own_window",
                        side_effect=lambda h: int(h) == 22):
            got = task._desktop_windows(_Ctx(DEFAULT_CONFIG))
        self.assertEqual([task._window_hwnd(w) for w in got], [11])

    def test_mask_rects_blanks_own_window_area(self):
        import numpy as np
        from mhxy.core import window as win_mod
        img = np.full((300, 400, 3), 255, dtype="uint8")
        win_mod.mask_rects(img, [1000, 500, 400, 300], [[1050, 550, 100, 50]])
        self.assertEqual(int(img[60, 60].sum()), 0)          # 洞内被涂黑
        self.assertEqual(int(img[200, 200].sum()), 255 * 3)  # 洞外不变

    def test_scene_uses_current_game_window_not_desktop(self):
        task = LaunchLoginTask()

        class _GameWin:
            def rect(self):
                return [100, 200, 800, 600]

        scene = object()
        with mock.patch.object(task, "_is_game_window", return_value=True), \
             mock.patch("mhxy.tasks.launch_login.win_mod.grab", return_value=object()) as grab, \
             mock.patch("mhxy.tasks.launch_login.win_mod.mask_rects") as mask_rects, \
             mock.patch("mhxy.tasks.launch_login.win_mod.ScaledScene", return_value=scene):
            self.assertIs(task._scene(_Ctx(DEFAULT_CONFIG), _GameWin()), scene)

        grab.assert_called_once_with([100, 200, 800, 600])
        mask_rects.assert_not_called()

    def test_place_after_login_uses_game_window_and_warns_when_missing(self):
        task = LaunchLoginTask()
        moved = []

        class _Win:
            def move_to_corner(self, corner, margin=0):
                moved.append(corner)
                return [1120, 0, 800, 600]

        logs = []

        class _RunCtx(_Ctx):
            def should_stop(self): return False
            def log(self, msg, level="info"): logs.append((level, msg))

        ctx = _RunCtx(DEFAULT_CONFIG)
        with mock.patch.object(task, "_interruptible_sleep", lambda *a: None), \
             mock.patch.object(task, "_resolve_game_window", return_value=_Win()):
            task._place_after_login(ctx, object(), set(), "top_right", 0.0, 0)
        self.assertEqual(moved, ["top_right"])

        logs.clear()
        with mock.patch.object(task, "_interruptible_sleep", lambda *a: None), \
             mock.patch.object(task, "_resolve_game_window", return_value=None):
            task._place_after_login(ctx, object(), set(), "bottom_left", 0.0, 0)
        self.assertEqual(moved, ["top_right"])               # 找不到游戏窗口绝不乱搬
        self.assertTrue(any(lv == "warn" for lv, _m in logs))

    def test_roster_late_arrival_triggers_rerender(self):
        """名册后到（先开脚本、后登录游戏）必须自动重渲染下拉框，否则一直显示「未读取到角色名册」。"""
        from mhxy.gui.launch_login_page import LaunchLoginPage
        calls = []

        class _Stub:
            _profile_role_values = {}
            _roles_checked_at = 0.0

            def _roles(self):
                return {"角色（10） · r1": ("r1", "角色")}

        stub = _Stub()

        def _refresh():
            calls.append(1)
            stub._profile_role_values = stub._roles()

        stub.refresh = _refresh
        LaunchLoginPage._maybe_refresh_roles(stub, force=True)
        self.assertEqual(calls, [1])           # 名册读到了 → 重渲染
        LaunchLoginPage._maybe_refresh_roles(stub, force=True)
        self.assertEqual(calls, [1])           # 名册没变 → 不重建控件（别打断用户操作）
        # App 的既有契约：游戏连接状态变化时回调这个名字
        self.assertTrue(callable(getattr(LaunchLoginPage, "_refresh_targets_labels", None)))

    def test_roster_readable_without_any_game_window(self):
        """核心场景：游戏已登录过但当前【没开着】（或只开启动器）也要能读名册，免得手打复杂角色名。"""
        from mhxy.core import accounts as A

        install = r"C:\Game"
        launcher = install + r"\Engine\Binaries\Win64\MyPCLauncher_x64r.exe"
        localdata = install + r"\LocalData"
        xml = ("<XyqPocket_LoginInfo_a>1:hid:srv:238939812:1700000000:x:复杂角色名:45"
               "</XyqPocket_LoginInfo_a>")
        want = os.path.normpath(localdata).lower()
        old_dir, old_launcher = A._game_dir, A._launcher_path
        try:
            with mock.patch.object(A.os.path, "isdir",
                                   side_effect=lambda p: os.path.normpath(str(p)).lower() == want), \
                 mock.patch.object(A, "_read_text", return_value=xml):
                A.set_game_dir("")
                A.set_launcher_path(launcher)          # 只配了启动器、没有任何窗口
                recs = A.roster(None)
                self.assertEqual(recs.get("238939812", {}).get("name"), "复杂角色名")
                self.assertEqual(recs["238939812"]["level"], "45")
                self.assertEqual(A._localdata_from_exe(launcher), localdata)   # 层级走对了

                # 启动器也没配时，退回扫进程（只认客户端进程名）
                A.set_launcher_path("")
                with mock.patch.object(A, "_proc_table", return_value=[
                        {"pid": 1, "exe": "explorer.exe"},
                        {"pid": 2, "exe": "mypclauncher_x64r.exe"}]), \
                     mock.patch("mhxy.core.window.proc_path_by_pid", return_value=launcher):
                    self.assertEqual(A._data_dir_from_processes(), localdata)
        finally:
            A.set_game_dir(old_dir)
            A.set_launcher_path(old_launcher)

    def test_add_profile_picker_excludes_added_roles(self):
        """点「新增档案」弹的下拉框里，已经加过的角色不能再出现（免得同一个号加两遍）。"""
        from mhxy.gui.launch_login_page import LaunchLoginPage as P

        class _Stub:
            _profile_role_values = {"甲（45） · r1": ("r1", "甲"),
                                    "乙（69） · r2": ("r2", "乙")}

            def _profiles(self):
                return [{"expected_role_id": "r1"}]

        self.assertEqual(list(P._available_roles(_Stub())), ["乙（69） · r2"])

    def test_role_picker_anchors_under_add_button(self):
        """角色列表必须贴在「＋ 新增档案」按钮下沿弹出，不能另开一个飘到别处的窗口。"""
        from mhxy.gui.launch_login_page import LaunchLoginPage as P

        class _Btn:
            def winfo_rootx(self): return 500
            def winfo_rooty(self): return 200
            def winfo_height(self): return 32

        self.assertEqual(P._menu_anchor(_Btn(), 1, 2), (500, 232))
        self.assertEqual(P._menu_anchor(None, 1, 2), (1, 2))     # 没按钮时退回兜底坐标

    def test_create_profile_uses_role_name_as_label(self):
        """选一个角色就建一个档案，档案名直接用角色名，不再要求手打一遍。"""
        from mhxy.gui.launch_login_page import LaunchLoginPage as P

        saved = {}

        class _Stub:
            def _account_cfg(self):
                return {"account_launch": {"profiles": []}}

            def refresh(self):
                saved["refreshed"] = True

        with mock.patch("mhxy.gui.launch_login_page.cfg_mod.save_config",
                        side_effect=lambda cfg: saved.update(cfg)):
            P._create_profile(_Stub(), "238939812", "复杂角色名")
        prof = saved["account_launch"]["profiles"][0]
        self.assertEqual(prof["label"], "复杂角色名")
        self.assertEqual(prof["expected_role_id"], "238939812")
        self.assertEqual(prof["expected_role_name"], "复杂角色名")
        self.assertTrue(saved["refreshed"])

    def test_roster_ocr_returns_target_text_center(self):
        fake_engine = mock.Mock(return_value=([
            [[[10, 20], [50, 20], [50, 40], [10, 40]], "角色", 0.99],
            [[[80, 20], [120, 20], [120, 40], [80, 40]], "其它", 0.99],
        ], 0.01))
        with mock.patch("mhxy.core.accounts._get_ocr_engine", return_value=fake_engine):
            hit = accounts.locate_roster_name(object(), {"r1": {"name": "角色"}}, "r1")
        self.assertIsNotNone(hit)
        self.assertEqual(hit[:2], (30, 30))
        with mock.patch("mhxy.core.accounts._get_ocr_engine", return_value=fake_engine):
            manual_hit = accounts.locate_roster_name(object(), {}, expected_name="角色")
        self.assertEqual(manual_hit[:2], (30, 30))

    def test_verified_launch_binding_is_runtime_only(self):
        win = _Window()
        self.assertTrue(accounts.bind_launch_session(win, "r1", "main"))
        session = accounts.launch_session(win)
        self.assertEqual(session["role_id"], "r1")
        self.assertEqual(session["profile_id"], "main")
        self.assertIn("bound_at", session)

    def test_cached_labels_do_not_trigger_ocr(self):
        win = _Window()
        with accounts._lock:
            accounts._identity[win._win._hWnd] = {"role_id": "r1", "label": "角色（45）"}
        try:
            with mock.patch("mhxy.core.accounts._compute", side_effect=AssertionError("unexpected OCR")):
                self.assertEqual(accounts.cached_labels_for([win]), ["角色（45）"])
        finally:
            with accounts._lock:
                accounts._identity.pop(win._win._hWnd, None)

    def test_explicit_refresh_keeps_existing_label_when_ocr_fails(self):
        win = _Window()
        with accounts._lock:
            accounts._identity[win._win._hWnd] = {"role_id": "r1", "label": "角色（45）"}
        try:
            with mock.patch("mhxy.core.accounts._compute", return_value={}):
                self.assertEqual(accounts.labels_for([win]), ["角色（45）"])
        finally:
            with accounts._lock:
                accounts._identity.pop(win._win._hWnd, None)

    def test_login_identity_verification_binds_expected_role(self):
        task = LaunchLoginTask()
        logs = []

        class _RunCtx:
            def should_stop(self): return False
            def log(self, msg, level="info"): logs.append((msg, level))

        class _GameWin(_Window):
            def activate(self): return True

        game_win = _GameWin()
        with mock.patch("mhxy.tasks.launch_login.accounts.labels_for"), \
             mock.patch("mhxy.tasks.launch_login.accounts.bound_role_id", return_value="r1"), \
             mock.patch("mhxy.tasks.launch_login.accounts.bind_launch_session", return_value=True) as bind:
            self.assertTrue(task._verify_game_identity(
                _RunCtx(), game_win, {"id": "main", "expected_role_id": "r1", "expected_role_name": "角色"}))
        bind.assert_called_once_with(game_win, "r1", "main")
        self.assertTrue(any(level == "hit" for _msg, level in logs))


class LaunchLoginDiagnosticsTests(unittest.TestCase):
    """识别失败不再静默：每步必须有日志、切前台失败要说明、模板失效要有文字兜底。"""

    def _cfg(self):
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        cfg["account_launch"]["profiles"] = [{
            "id": "main", "label": "主号", "enabled": True,
            "expected_role_id": "r1", "expected_role_name": "角色",
        }]
        tc = cfg["tasks"]["launch_login"]
        tc["dry_run"] = False
        tc["templates"] = {key: key + ".png" for key in tc["templates"]}
        return cfg

    class _Ctx:
        def __init__(self, cfg, logs):
            self.cfg = cfg
            self.logs = logs
            self.mouse = mock.Mock()

        def task_cfg(self, name):
            return self.cfg["tasks"][name]

        def should_stop(self):
            return False

        def log(self, msg, level="info"):
            self.logs.append((level, msg))

    class _Win:
        def __init__(self, rect=(100, 100, 800, 600), activate=True):
            self._rect, self._activate = rect, activate
            self.activations = 0

        def rect(self):
            return list(self._rect)

        def activate(self):
            self.activations += 1
            return self._activate

    class _Scene:
        """假场景：match 返回固定命中（或 None），坐标原样映射（屏幕坐标）。"""

        def __init__(self, hit=None, img=None):
            self._hit, self.img = hit, img

        def match(self, _tpl, _threshold):
            return self._hit

        def to_screen(self, x, y):
            return (x, y)

    def test_missing_button_is_logged_with_best_score(self):
        cfg = self._cfg()
        logs = []
        task = LaunchLoginTask()
        task._miss_since, task._miss_logged = {}, {}
        task._ocr_first, task._ocr_last = {}, {}
        task._ocr_after = 999.0          # 本次只验证「没认出来要留日志」，不走文字兜底
        scene = self._Scene(hit=None)
        with mock.patch.object(task, "_scene", return_value=scene), \
             mock.patch("mhxy.tasks.launch_login.vision.best_score", return_value=(0.62, None)), \
             mock.patch("mhxy.tasks.launch_login.accounts.locate_text", return_value=None):
            clicked = task._click_template(self._Ctx(cfg, logs), self._Win(), object(), 0.85, "点击开始游戏")
        self.assertFalse(clicked)
        warns = [msg for level, msg in logs if level == "warn"]
        self.assertTrue(any("未识别到按钮" in msg and "0.620" in msg for msg in warns), warns)

    def test_template_hit_but_foreground_failed_is_logged(self):
        cfg = self._cfg()
        logs = []
        task = LaunchLoginTask()
        task._miss_since, task._miss_logged = {}, {}
        scene = self._Scene(hit=(10, 10, 0.9))
        with mock.patch.object(task, "_scene", return_value=scene):
            clicked = task._click_template(self._Ctx(cfg, logs), self._Win(activate=False), object(),
                                           0.85, "点击开始游戏")
        self.assertFalse(clicked)
        self.assertTrue(any(level == "warn" and "未能切到前台" in msg for level, msg in logs), logs)

    def test_text_fallback_clicks_button_when_template_stale(self):
        """模板认不出来（官方更新了启动器外观）时，靠 OCR 认「开始游戏」文字把这一步走通。"""
        cfg = self._cfg()
        logs = []
        task = LaunchLoginTask()
        task._miss_since, task._miss_logged = {}, {}
        task._ocr_first, task._ocr_last = {}, {}
        task._ocr_after, task._ocr_interval = 0.0, 0.0
        win = self._Win(rect=(100, 100, 800, 600))
        scene = self._Scene(hit=None)
        ctx = self._Ctx(cfg, logs)
        with mock.patch.object(task, "_scene", return_value=scene), \
             mock.patch.object(task, "_official_launcher_window", return_value=win), \
             mock.patch("mhxy.tasks.launch_login.vision.best_score", return_value=(0.4, None)), \
             mock.patch("mhxy.tasks.launch_login.accounts.ocr_status", return_value="not_loaded"), \
             mock.patch("mhxy.tasks.launch_login.accounts.locate_text",
                        return_value=(500, 300, 0.95, 120, 30, "开始游戏")):
            clicked = task._click_template(ctx, win, object(), 0.85, "点击开始游戏")
        self.assertTrue(clicked)
        ctx.mouse.click.assert_called_once_with(500, 300)
        self.assertTrue(any(level == "hit" and "文字识别命中【开始游戏】" in msg for level, msg in logs), logs)

    def test_text_fallback_reports_missing_ocr_engine(self):
        """OCR 引擎起不来时要说清楚原因，别让「文字也没找到」把人误导到模板上。"""
        cfg = self._cfg()
        logs = []
        task = LaunchLoginTask()
        task._miss_since, task._miss_logged = {}, {}
        task._ocr_first, task._ocr_last = {}, {}
        task._ocr_after, task._ocr_interval = 0.0, 0.0
        with mock.patch.object(task, "_scene", return_value=self._Scene(hit=None)), \
             mock.patch.object(task, "_official_launcher_window", return_value=None), \
             mock.patch("mhxy.tasks.launch_login.accounts.ocr_status",
                        return_value="unavailable: no module"), \
             mock.patch("mhxy.tasks.launch_login.accounts.locate_text") as locate:
            clicked = task._click_template(self._Ctx(cfg, logs), self._Win(), object(), 0.85,
                                           "点击开始游戏")
        self.assertFalse(clicked)
        locate.assert_not_called()
        self.assertTrue(any(level == "warn" and "文字兜底不可用" in msg for level, msg in logs), logs)

    def test_text_fallback_rejects_hit_outside_target_window(self):
        """OCR 命中在目标窗口之外（例如公告/别的窗口里的同名字）绝不允许点击。"""
        cfg = self._cfg()
        logs = []
        task = LaunchLoginTask()
        task._miss_since, task._miss_logged = {}, {}
        task._ocr_first, task._ocr_last = {}, {}
        task._ocr_after, task._ocr_interval = 0.0, 0.0
        win = self._Win(rect=(100, 100, 300, 200))
        ctx = self._Ctx(cfg, logs)
        with mock.patch.object(task, "_scene", return_value=self._Scene(hit=None)), \
             mock.patch.object(task, "_official_launcher_window", return_value=None), \
             mock.patch("mhxy.tasks.launch_login.accounts.ocr_status", return_value="not_loaded"), \
             mock.patch("mhxy.tasks.launch_login.accounts.locate_text",
                        return_value=(900, 900, 0.95, 120, 30, "开始游戏")):
            clicked = task._click_template(ctx, win, object(), 0.85, "点击开始游戏")
        self.assertFalse(clicked)
        ctx.mouse.click.assert_not_called()

    def test_launcher_window_found_even_if_foreground_fails(self):
        """切前台失败不再丢弃窗口（原实现会静默空转到超时，日志里连这一步都没有）。"""
        cfg = self._cfg()
        logs = []
        task = LaunchLoginTask()
        ctx = self._Ctx(cfg, logs)
        win = self._Win(activate=False)
        with mock.patch.object(task, "_official_launcher_window", return_value=win):
            got = task._wait_new_window(ctx, set(), 5.0, object(), 0.85)
        self.assertIs(got, win)
        self.assertTrue(any("已检测到 MyPCLauncher_x64r.exe 启动器" in msg for _lv, msg in logs), logs)
        self.assertTrue(any(level == "warn" and "未能切到前台" in msg for level, msg in logs), logs)

    def test_official_launcher_window_skips_zero_sized_hidden_windows(self):
        """同进程下 WebView2 外壳还有 0 尺寸隐藏顶层窗；选错它会导致切前台/点击全落空。"""
        task = LaunchLoginTask()
        hidden = _FakeNative(11, 0, 0)
        real = _FakeNative(22, 1350, 900)
        ctx = _Ctx(self._cfg())
        with mock.patch("mhxy.tasks.launch_login.win_mod.gw.getAllWindows",
                        return_value=[hidden, real]), \
             mock.patch("mhxy.tasks.launch_login.win_mod.proc_image_path",
                        return_value="C:\\game\\" + launcher.LAUNCHER_EXE), \
             mock.patch("mhxy.tasks.launch_login.win_mod.is_own_window", return_value=False):
            got = task._official_launcher_window(ctx)
        self.assertIsNotNone(got)
        self.assertEqual(got.hwnd(), 22)

    def test_official_launcher_window_ignores_only_hidden_windows(self):
        task = LaunchLoginTask()
        hidden = _FakeNative(11, 0, 0)
        with mock.patch("mhxy.tasks.launch_login.win_mod.gw.getAllWindows", return_value=[hidden]), \
             mock.patch("mhxy.tasks.launch_login.win_mod.proc_image_path",
                        return_value="C:\\game\\" + launcher.LAUNCHER_EXE), \
             mock.patch("mhxy.tasks.launch_login.win_mod.is_own_window", return_value=False):
            self.assertIsNone(task._official_launcher_window(_Ctx(self._cfg())))

    def test_find_template_window_prefers_window_under_hit(self):
        task = LaunchLoginTask()
        left, right = self._Win(rect=(0, 0, 100, 100)), self._Win(rect=(100, 100, 800, 600))
        scene = self._Scene(hit=(150, 150, 0.9))
        with mock.patch.object(task, "_desktop_windows", return_value=[left, right]), \
             mock.patch.object(task, "_scene", return_value=scene):
            got = task._find_template_window(_Ctx(self._cfg()), object(), 0.85)
        self.assertIs(got, right)

    def test_ocr_fallback_delays_until_threshold(self):
        """文字兜底不能一上来就跑（OCR 贵）：先等 ocr_fallback_after_sec。"""
        cfg = self._cfg()
        logs = []
        task = LaunchLoginTask()
        task._miss_since, task._miss_logged = {}, {}
        task._ocr_first, task._ocr_last = {}, {}
        task._ocr_after, task._ocr_interval = 30.0, 30.0
        win = self._Win()
        with mock.patch.object(task, "_scene", return_value=self._Scene(hit=None)), \
             mock.patch("mhxy.tasks.launch_login.vision.best_score", return_value=(0.3, None)), \
             mock.patch("mhxy.tasks.launch_login.accounts.locate_text") as locate:
            clicked = task._click_template(self._Ctx(cfg, logs), win, object(), 0.85, "点击开始游戏")
        self.assertFalse(clicked)
        locate.assert_not_called()


class _FakeNative:
    """pygetwindow 顶层窗口的假体（只要 _hWnd/尺寸/位置就够）。"""

    def __init__(self, hwnd, width, height, left=0, top=0, minimized=False):
        self._hWnd = hwnd
        self.width, self.height = width, height
        self.left, self.top = left, top
        self.isMinimized = minimized


class LaunchCalibrationProfileTests(unittest.TestCase):
    """启动登录的标定不吃尺寸组：不建组、不占组，故「组已满」也不会再挡住标定。"""

    def test_record_profile_skips_when_pid_missing(self):
        from mhxy.gui.calibrate_dialog import CalibrateDialog
        cfg = {}
        with mock.patch("mhxy.gui.calibrate_dialog.calib.set_task_profile") as task_profile, \
             mock.patch("mhxy.gui.calibrate_dialog.calib.set_template_profile") as tpl_profile:
            self.assertFalse(CalibrateDialog._record_profile(cfg, "launch_login", "start_game", None))
        task_profile.assert_not_called()
        tpl_profile.assert_not_called()

    def test_record_profile_writes_when_pid_present(self):
        from mhxy.gui.calibrate_dialog import CalibrateDialog
        with mock.patch("mhxy.gui.calibrate_dialog.calib.set_task_profile") as task_profile, \
             mock.patch("mhxy.gui.calibrate_dialog.calib.set_template_profile") as tpl_profile:
            self.assertTrue(CalibrateDialog._record_profile({}, "sniper", "key", 2))
        task_profile.assert_called_once()
        tpl_profile.assert_called_once()

    def test_calibrate_template_saves_without_profile_tracking(self):
        """pid=None（整屏坐标标定）时仍要存下模板图与路径——这正是小窗标定要走的路。"""
        from mhxy.gui.calibrate_dialog import CalibrateDialog
        saved = {}

        class _Stub:
            task_name = "launch_login"
            cfg = {}
            tc = {"templates": {}}

            @staticmethod
            def _record_profile(cfg, task_name, key, pid):
                return CalibrateDialog._record_profile(cfg, task_name, key, pid)

            @staticmethod
            def _profile_note(pid):
                return CalibrateDialog._profile_note(pid)

            def _grab_roi(self, prompt, with_crop=False):
                import numpy as np
                return [1, 2, 3, 4], np.zeros((10, 20, 3), dtype="uint8"), None

            def _save(self):
                saved["saved"] = True

            def _refresh(self):
                pass

            def _toast(self, msg, color=None):
                saved["toast"] = msg

        with mock.patch("mhxy.gui.calibrate_dialog.vision.save_image", return_value=True), \
             mock.patch("mhxy.gui.calibrate_dialog.calib.set_template_profile") as tpl_profile:
            CalibrateDialog._calibrate_template(_Stub(), "start_game", "开始游戏")
        self.assertEqual(_Stub.tc["templates"]["start_game"], "templates/tm_start_game.png")
        self.assertTrue(saved.get("saved"))
        tpl_profile.assert_not_called()


if __name__ == "__main__":
    unittest.main()
