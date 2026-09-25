# -*- coding: utf-8 -*-
"""一键启动并登录。

启动器是全局唯一资源，故本任务刻意不使用多窗口轮转：一个档案从启动器到游戏内身份确认
完成后，才会启动下一个档案。最终游戏窗口仍可由其它任务按既有多开逻辑使用。
"""

import ctypes
import time

from ..core import accounts
from ..core import launcher
from ..core import vision
from ..core import window as win_mod
from .base import Task, register


@register
class LaunchLoginTask(Task):
    name = "launch_login"
    title = "启动登录"
    description = "串行启动并登录预设角色"
    CALIBRATION = {
        # 启动器与游戏外壳尺寸不同，整个启动登录阶段统一在虚拟桌面范围找图。
        "regions": [],
        "templates": [
            ("start_game", "开始游戏", "启动器右下角按钮"),
            ("enter_game", "进入游戏", "已登录后的进入按钮"),
            ("switch_role", "切换", "角色选择入口"),
            ("existing_role", "已有角色", "已有角色页入口"),
            ("in_game_ready", "游戏主界面", "进入游戏后的稳定 HUD 标志"),
        ],
        "watchlist": False,
    }

    _CLICK_STATES = {
        "WAIT_START": ("start_game", "WAIT_ENTER", "点击开始游戏"),
        "WAIT_ENTER": ("enter_game", "WAIT_SWITCH", "点击进入游戏"),
        "WAIT_SWITCH": ("switch_role", "WAIT_EXISTING", "点击切换角色"),
        "WAIT_EXISTING": ("existing_role", "WAIT_ROLE", "点击已有角色"),
    }

    def preflight(self, ctx):
        tc = ctx.task_cfg(self.name)
        account_cfg = ctx.cfg.get("account_launch") or {}
        profiles = [p for p in account_cfg.get("profiles", []) if p.get("enabled", True)]
        problems = []
        if not self._is_admin():
            problems.append("当前不是管理员权限；启动器和游戏的输入会被 Windows UIPI 拦截")
        if not profiles:
            problems.append("没有勾选启动档案")
        if not tc.get("dry_run", True) and not launcher.normalize_path(account_cfg.get("launcher_path")):
            problems.append("请先通过“浏览...”选择有效的启动器 EXE")
        templates = tc.get("templates") or {}
        for key in ("start_game", "enter_game", "switch_role", "existing_role", "in_game_ready"):
            path = templates.get(key)
            if not path or vision.load_template(path) is None:
                problems.append("缺少流程标定：%s" % key)
        roster = accounts.roster(win_mod.locate_all(ctx.cfg.get("window_title", "梦幻西游"),
                                                     ctx.cfg.get("window_offset", [0, 0])))
        for profile in profiles:
            name = profile.get("label") or profile.get("expected_role_name") or "未命名档案"
            if not profile.get("expected_role_id") and not profile.get("expected_role_name"):
                problems.append("%s 未选择目标角色" % name)
            role_path = profile.get("role_template")
            if not role_path or vision.load_template(role_path) is None:
                problems.append("%s 缺少角色名模板" % name)
            role_id = profile.get("expected_role_id")
            if role_id and roster and str(role_id) not in roster:
                problems.append("%s 的目标角色不在本机名册中" % name)
        return not problems, problems

    def run(self, ctx):
        tc = ctx.task_cfg(self.name)
        account_cfg = ctx.cfg.get("account_launch") or {}
        profiles = [dict(p) for p in account_cfg.get("profiles", []) if p.get("enabled", True)]
        templates = {key: vision.load_template(path) if path else None
                     for key, path in (tc.get("templates") or {}).items()}
        loop = tc.get("loop") or {}
        dry_run = bool(tc.get("dry_run", True))
        threshold = float(loop.get("match_threshold", 0.85))
        timeout = float(loop.get("state_timeout_sec", 35))
        launcher_timeout = float(loop.get("launcher_timeout_sec", 120))

        ctx.log("启动登录：%d 个档案，%s。" % (len(profiles), "演练模式" if dry_run else "实战模式"))
        for index, profile in enumerate(profiles, 1):
            if ctx.should_stop():
                break
            label = profile.get("label") or profile.get("expected_role_name") or ("档案 %d" % index)
            ctx.log("[%s] 开始处理（%d/%d）。" % (label, index, len(profiles)))
            result = self._run_profile(ctx, profile, templates, threshold, timeout,
                                       launcher_timeout, dry_run)
            if result == "ok":
                ctx.log("[%s] 登录并完成窗口角色校验。" % label, level="hit")
            elif result == "stopped":
                break
            else:
                ctx.log("[%s] 未完成：%s；继续下一个档案。" % (label, result), level="error")
        if ctx.should_stop():
            ctx.log("启动登录已停止；不会关闭已打开的游戏窗口。", level="warn")
        else:
            ctx.log("启动登录队列结束。")

    def _run_profile(self, ctx, profile, templates, threshold, timeout, launcher_timeout, dry_run):
        label = profile.get("label") or profile.get("expected_role_name") or "档案"
        if self._already_logged_in(ctx, profile, templates.get("in_game_ready"), threshold):
            ctx.log("[%s] 已检测到对应游戏窗口，跳过启动。" % label)
            return "ok"
        if dry_run:
            return "演练模式不启动程序"

        # 启动器可能是独立标题/进程，不能沿用游戏窗口白名单。记录全桌面 HWND 基线后，
        # 仅接受本次启动新出现且命中“开始游戏”模板的窗口。
        before = self._desktop_hwnds(ctx)
        launch_cfg = ctx.cfg.get("account_launch") or {}
        ok, detail = launcher.launch(launch_cfg.get("launcher_path"), launch_cfg.get("launcher_args") or [])
        if not ok:
            return detail
        ctx.log("[%s] 已请求启动器；若出现 UAC/SmartScreen，请手动确认。" % label)
        win = self._wait_new_window(ctx, before, launcher_timeout, templates.get("start_game"), threshold)
        if win is None:
            return "等待 MyPCLauncher_x64r.exe 启动器窗口超时"

        state = "WAIT_START"
        state_at = time.time()
        role_path = profile.get("role_template")
        role_tpl = vision.load_template(role_path) if role_path else None
        while not ctx.should_stop():
            if win.rect() is None:
                # 点击“开始游戏”后，启动器可能销毁原 HWND 并新建游戏外壳；按下一状态
                # 的模板接管新窗口，而不是把正常窗口转换误报为登录窗口关闭。
                replacement = self._find_template_window(
                    ctx, self._template_for_state(state, templates, role_tpl), threshold)
                if replacement is not None:
                    win = replacement
                elif time.time() - state_at > timeout:
                    return "%s 超时（登录窗口已转换但未识别到下一界面）" % state
                else:
                    self._interruptible_sleep(ctx, 0.35)
                    continue
            if state == "WAIT_READY":
                if self._match(ctx, win, templates.get("in_game_ready"), threshold):
                    state = "VERIFY_ROLE"
                    state_at = time.time()
                    continue
            elif state == "VERIFY_ROLE":
                if self._verify_role(ctx, win, profile):
                    return "ok"
            elif state == "WAIT_ROLE":
                if self._click_template(ctx, win, role_tpl, threshold, "选择预设角色"):
                    state = "WAIT_READY"
                    state_at = time.time()
                    continue
            else:
                key, next_state, action = self._CLICK_STATES[state]
                if self._click_template(ctx, win, templates.get(key), threshold, action):
                    state = next_state
                    state_at = time.time()
                    continue
            if time.time() - state_at > timeout:
                return "%s 超时" % state
            self._interruptible_sleep(ctx, 0.35)
        return "stopped"

    def _desktop_windows(self, ctx):
        """枚举可见的大型顶层窗口，不按游戏标题/进程过滤。

        官方启动器可能与最终游戏窗口使用不同 exe 或标题；但候选仍必须是本次启动后出现的
        新 HWND，且之后还要命中对应模板才会被点击，因此不会把普通窗口当游戏操作。
        """
        out = []
        title = ctx.cfg.get("window_title", "梦幻西游")
        offset = ctx.cfg.get("window_offset", [0, 0])
        try:
            raw = win_mod.gw.getAllWindows()
        except Exception:
            return out
        for desktop_win in raw:
            try:
                if desktop_win.isMinimized or desktop_win.width <= 200 or desktop_win.height <= 160:
                    continue
                if not int(desktop_win._hWnd):
                    continue
                out.append(win_mod.GameWindow(title, offset).bind(desktop_win))
            except Exception:
                continue
        return out

    @staticmethod
    def _window_hwnd(win):
        try:
            return int(win._win._hWnd)
        except Exception:
            return 0

    def _desktop_hwnds(self, ctx):
        return {self._window_hwnd(win) for win in self._desktop_windows(ctx) if self._window_hwnd(win)}

    def _official_launcher_window(self, ctx):
        """按官方启动器进程定位窗口，支持已存在或最小化的单例启动器。"""
        title = ctx.cfg.get("window_title", "梦幻西游")
        offset = ctx.cfg.get("window_offset", [0, 0])
        try:
            raw = win_mod.gw.getAllWindows()
        except Exception:
            return None
        for desktop_win in raw:
            try:
                hwnd = int(desktop_win._hWnd)
                image = win_mod.proc_image_path(hwnd)
                if image and image.rsplit("\\", 1)[-1].lower() == launcher.LAUNCHER_EXE.lower():
                    return win_mod.GameWindow(title, offset).bind(desktop_win)
            except Exception:
                continue
        return None

    def _find_template_window(self, ctx, tpl, threshold, allowed_hwnds=None):
        if tpl is None:
            return None
        for win in self._desktop_windows(ctx):
            hwnd = self._window_hwnd(win)
            if allowed_hwnds is not None and hwnd not in allowed_hwnds:
                continue
            if self._match(ctx, win, tpl, threshold):
                return win
        return None

    def _wait_new_window(self, ctx, before, timeout, start_tpl, threshold):
        started = time.time()
        deadline = started + timeout
        warned = False
        while not ctx.should_stop() and time.time() < deadline:
            # 官方启动器是单例：可能复用已有 HWND，也可能最小化后被 ShellExecute 唤起。
            # 所以先按 MyPCLauncher_x64r.exe 精确找窗口，不能只接受“新 HWND + 模板命中”。
            win = self._official_launcher_window(ctx)
            if win is not None and win.activate():
                ctx.log("已检测到 MyPCLauncher_x64r.exe 启动器。")
                return win
            # 兼容未来外壳进程变化：仅把本次新增且命中开始游戏模板的窗口作为后备候选。
            new_hwnds = self._desktop_hwnds(ctx) - before
            win = self._find_template_window(ctx, start_tpl, threshold, new_hwnds)
            if win is not None and win.activate():
                ctx.log("已通过开始游戏模板检测到启动器。")
                return win
            if not warned and time.time() - started >= 8:
                ctx.log("仍未检测到 MyPCLauncher_x64r.exe 启动器窗口。", level="warn")
                warned = True
            self._interruptible_sleep(ctx, 0.5)
        return None

    def _template_for_state(self, state, templates, role_tpl):
        if state in self._CLICK_STATES:
            return templates.get(self._CLICK_STATES[state][0])
        if state == "WAIT_ROLE":
            return role_tpl
        return templates.get("in_game_ready")

    @staticmethod
    def _screen_rect():
        """整个虚拟桌面矩形，含副屏与负坐标；启动器/游戏外壳不共用窗口尺寸。"""
        try:
            user32 = ctypes.windll.user32
            return [user32.GetSystemMetrics(76), user32.GetSystemMetrics(77),
                    user32.GetSystemMetrics(78), user32.GetSystemMetrics(79)]
        except Exception:
            return None

    def _scene(self, _ctx, _win):
        # 模板从屏幕上框选得到，直接按真实屏幕像素匹配，绝不按启动器或游戏窗口尺寸缩放。
        rect = self._screen_rect()
        return win_mod.grab_scene(rect) if rect and rect[2] > 0 and rect[3] > 0 else None

    def _match(self, ctx, win, tpl, threshold):
        if tpl is None:
            return None
        scene = self._scene(ctx, win)
        return scene.match(tpl, threshold) if scene is not None else None

    def _click_template(self, ctx, win, tpl, threshold, action):
        result = self._match(ctx, win, tpl, threshold)
        if result is None or not win.activate():
            return False
        scene = self._scene(ctx, win)
        result = scene.match(tpl, threshold) if scene is not None else None
        if result is None:
            return False
        xy = scene.to_screen(result[0], result[1])
        ctx.log("%s（%.3f）。" % (action, result[2]), level="hit")
        ctx.mouse.click(xy[0], xy[1])
        return True

    def _verify_role(self, ctx, win, profile):
        # 顶部姓名条可能被别的窗口盖住；先确认该游戏窗口在前台再做 OCR 绑定。
        if not win.activate():
            return False
        names = accounts.roster([win])
        expected = str(profile.get("expected_role_id") or "")
        if not expected:
            wanted = (profile.get("expected_role_name") or "").strip()
            choices = [rid for rid, rec in names.items() if (rec.get("name") or "").strip() == wanted]
            expected = choices[0] if len(choices) == 1 else ""
        if not expected:
            return False
        labels = accounts.labels_for([win])
        rec = names.get(expected) or {}
        expected_label = accounts.display_name(rec)
        if labels and labels[0] == expected_label:
            accounts.bind_launch_session(win, expected, profile.get("id"))
            return True
        return False

    def _already_logged_in(self, ctx, profile, ready_tpl, threshold):
        expected = str(profile.get("expected_role_id") or "")
        wanted = (profile.get("expected_role_name") or "").strip()
        for win in win_mod.locate_all(ctx.cfg.get("window_title", "梦幻西游"),
                                      ctx.cfg.get("window_offset", [0, 0])):
            # 不在被遮挡的后台截图上判断 HUD，防止把前台的相似图案误判为此窗口已登录。
            if not win.activate():
                continue
            if ready_tpl is not None and not self._match(ctx, win, ready_tpl, threshold):
                continue
            names = accounts.roster([win])
            label = accounts.labels_for([win])[0]
            if expected and expected in names and label == accounts.display_name(names[expected]):
                return True
            if wanted and label.split("（", 1)[0] == wanted:
                return True
        return False
