# -*- coding: utf-8 -*-
"""一键启动并登录。

启动器是全局唯一资源，故本任务刻意不使用多窗口轮转：一个档案从启动器到游戏内身份确认
完成后，才会启动下一个档案。最终游戏窗口仍可由其它任务按既有多开逻辑使用。
"""

import ctypes
import random
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
        ],
        "watchlist": False,
    }

    _CLICK_STATES = {
        "WAIT_START": ("start_game", "WAIT_ENTER", "点击开始游戏"),
        "WAIT_ENTER": ("enter_game", "WAIT_SWITCH", "点击进入游戏"),
        "WAIT_SWITCH": ("switch_role", "WAIT_EXISTING", "点击切换角色"),
        "WAIT_EXISTING": ("existing_role", "WAIT_ROLE", "点击已有角色"),
    }

    _CORNER_LABELS = {"top_left": "左上", "top_right": "右上",
                      "bottom_right": "右下", "bottom_left": "左下"}

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
        for key in ("start_game", "enter_game", "switch_role", "existing_role"):
            path = templates.get(key)
            if not path or vision.load_template(path) is None:
                problems.append("缺少流程标定：%s" % key)
        roster = accounts.roster(win_mod.locate_all(ctx.cfg.get("window_title", "梦幻西游"),
                                                     ctx.cfg.get("window_offset", [0, 0])))
        for profile in profiles:
            name = profile.get("label") or profile.get("expected_role_name") or "未命名档案"
            if not profile.get("expected_role_id") and not profile.get("expected_role_name"):
                problems.append("%s 未选择或输入目标角色" % name)
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
        timeout = float(loop.get("state_timeout_sec", 60))
        launcher_timeout = float(loop.get("launcher_timeout_sec", 120))
        # 用户拍板：任一步没成功就【反复】检测该步和上一步，而不是只补点一次。
        recheck_cfg = {
            "after": max(0.5, float(loop.get("recheck_previous_after_sec", 3.0))),
            "interval": max(1.0, float(loop.get("recheck_interval_sec", 2.5))),
            "max": max(1, int(loop.get("recheck_max", 6))),
        }
        # 用户拍板：点“切换”这一步要慢——进入已登录首页后先等 2~3 秒让界面稳定，再点。
        wait_min = max(0.0, float(loop.get("switch_wait_min_sec", 2.0)))
        switch_cfg = {
            "wait_min": wait_min,
            "wait_max": max(wait_min, float(loop.get("switch_wait_max_sec", 3.0))),
        }
        # 用户拍板：每完成一个窗口就把它摆到屏幕四角，顺序 左上→右上→右下→左下（多于 4 个循环）。
        place_corner = bool(tc.get("place_corner", True))
        corners = [str(c) for c in (loop.get("corner_order") or win_mod.CORNER_ORDER)]
        corner_margin = int(loop.get("corner_margin_px", 0))
        corner_settle = max(0.0, float(loop.get("corner_settle_sec", 2.0)))

        ctx.log("启动登录：%d 个档案，%s。" % (len(profiles), "演练模式" if dry_run else "实战模式"))
        placed = 0
        for index, profile in enumerate(profiles, 1):
            if ctx.should_stop():
                break
            label = profile.get("label") or profile.get("expected_role_name") or ("档案 %d" % index)
            corner = self._corner_for(placed, corners) if place_corner else None
            ctx.log("[%s] 开始处理（%d/%d）。" % (label, index, len(profiles)))
            result = self._run_profile(ctx, profile, templates, threshold, timeout,
                                       launcher_timeout, recheck_cfg, switch_cfg, dry_run,
                                       corner, corner_margin, corner_settle)
            if result == "ok":
                placed += 1
                ctx.log("[%s] 登录并完成窗口角色校验。" % label, level="hit")
            elif result == "stopped":
                break
            else:
                ctx.log("[%s] 未完成：%s；继续下一个档案。" % (label, result), level="error")
        if ctx.should_stop():
            ctx.log("启动登录已停止；不会关闭已打开的游戏窗口。", level="warn")
        else:
            ctx.log("启动登录队列结束。")

    @staticmethod
    def _corner_for(placed, corners):
        """第 placed 个成功启动的窗口该去哪个角（顺序循环）。"""
        return corners[placed % len(corners)] if corners else None

    def _run_profile(self, ctx, profile, templates, threshold, timeout, launcher_timeout,
                     recheck_cfg, switch_cfg, dry_run, corner=None, corner_margin=0,
                     corner_settle=2.0):
        label = profile.get("label") or profile.get("expected_role_name") or "档案"
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
        last_role_ocr_at = 0.0
        switch_ready_at = 0.0          # 进入 WAIT_SWITCH 后允许点击“切换”的最早时刻（用户要求先等 2~3 秒）
        recheck_counts = {}            # state -> 该步已回查上一步的次数（每步入一次，按状态名计数即可）
        recheck_state = state
        next_recheck_at = time.time() + recheck_cfg["after"]
        recheck_after = recheck_cfg["after"]
        recheck_interval = recheck_cfg["interval"]
        recheck_max = recheck_cfg["max"]
        wait_min = switch_cfg["wait_min"]
        wait_max = switch_cfg["wait_max"]
        while not ctx.should_stop():
            if win.rect() is None:
                # 点击“开始游戏”后，启动器可能销毁原 HWND 并新建游戏外壳；按下一状态
                # 的模板接管新窗口，而不是把正常窗口转换误报为登录窗口关闭。
                replacement = self._find_template_window(
                    ctx, self._template_for_state(state, templates), threshold)
                if replacement is not None:
                    win = replacement
                elif time.time() - state_at > timeout:
                    return "%s 超时（登录窗口已转换但未识别到下一界面）" % state
                else:
                    self._interruptible_sleep(ctx, 0.35)
                    continue
            if state != recheck_state:          # 刚切到新状态 → 重新安排第一次回查
                recheck_state = state
                next_recheck_at = time.time() + recheck_after
            elapsed = time.time() - state_at
            if state == "WAIT_SWITCH" and time.time() < switch_ready_at:
                # 用户拍板：点“切换”要慢——进入已登录首页后先等 2~3 秒让界面稳定，再点。
                self._interruptible_sleep(ctx, 0.2)
                continue
            if state == "WAIT_ROLE":
                # 角色选择页有清晰文本，直接 OCR 名册中的目标角色，不再维护脆弱的角色名截图模板。
                now = time.time()
                if now - last_role_ocr_at >= 1.0:
                    last_role_ocr_at = now
                    if self._click_roster_role(ctx, win, profile):
                        # 用户确认：选中角色即视为本号流程结束；随即把这个窗口摆到指定角落。
                        self._place_after_login(ctx, win, before, corner, corner_settle, corner_margin)
                        return "ok"
            else:
                key, next_state, action = self._CLICK_STATES[state]
                if self._click_template(ctx, win, templates.get(key), threshold, action):
                    if next_state == "WAIT_SWITCH":
                        delay = random.uniform(wait_min, wait_max)
                        switch_ready_at = time.time() + delay
                        ctx.log("已进入游戏页，等待 %.1f 秒后再点切换。" % delay)
                    state = next_state
                    state_at = time.time()
                    continue
            # 通用规则（用户拍板）：本步没识别出来，就反复回查并重点上一步，而不是只补点一次。
            previous = self._previous_template_for_state(state, templates)
            if previous is not None and time.time() >= next_recheck_at:
                count = recheck_counts.get(state, 0)
                if count < recheck_max:
                    prev_tpl, prev_action = previous
                    recheck_counts[state] = count + 1
                    next_recheck_at = time.time() + recheck_interval
                    state_at = time.time()      # 每次回查重新计时，避免还没试够就整体超时
                    ctx.log("当前步骤未识别，回查上一步：%s（第 %d/%d 次）。"
                            % (prev_action, count + 1, recheck_max), level="warn")
                    self._click_template(ctx, win, prev_tpl, threshold, "重新点击" + prev_action)
                    continue
            if elapsed > timeout:
                return "%s 超时" % state
            self._interruptible_sleep(ctx, 0.35)
        return "stopped"

    def _place_after_login(self, ctx, win, before, corner, settle_sec, margin):
        """登录完成后把本号游戏窗口摆到指定屏幕角落。

        ⚠ 目标窗口一律【按游戏客户端进程】重新定位（本次新增的那个），绝不用状态机里跟了一路的
        `win`：那可能还停在启动器上，也可能被整屏匹配误认成脚本自己的窗口。
        失败只告警、不影响登录结果。
        """
        if not corner:
            return
        self._interruptible_sleep(ctx, settle_sec)   # 等游戏窗口建好/外壳切换稳定再归位
        label = self._CORNER_LABELS.get(corner, corner)
        target = self._resolve_game_window(ctx, before)
        if target is None:
            ctx.log("窗口归位到%s失败：没找到本号新出现的游戏窗口。" % label, level="warn")
            return
        rect = target.move_to_corner(corner, margin=margin)
        if rect is None:
            ctx.log("窗口归位到%s失败（窗口可能已最小化或被外壳约束）。" % label, level="warn")
        else:
            ctx.log("窗口已归位到%s：%s。" % (label, rect), level="hit")

    def _resolve_game_window(self, ctx, before):
        """定位本号真正进入游戏的窗口：本次新增 + 属于游戏客户端进程（MyTabCtrl/MyGame）。

        locate_all 已按「游戏标题 + 游戏进程白名单」过滤，故天然排除启动器和脚本自身窗口；
        再用启动前的 HWND 基线剔除已经登录好的老号。
        """
        fresh = [w for w in win_mod.locate_all(ctx.cfg.get("window_title", "梦幻西游"),
                                               ctx.cfg.get("window_offset", [0, 0]))
                 if self._window_hwnd(w) and self._window_hwnd(w) not in before]
        return fresh[-1] if fresh else None

    def _desktop_windows(self, ctx):
        """枚举可见的大型顶层窗口，不按游戏标题/进程过滤。

        官方启动器可能与最终游戏窗口使用不同 exe 或标题；但候选仍必须是本次启动后出现的
        新 HWND，且之后还要命中对应模板才会被点击，因此不会把普通窗口当游戏操作。
        ⚠ 必须排掉脚本自己的窗口：悬浮日志窗置顶且日志文字里就含「开始游戏」等字样，会被
        模板匹配命中，从而把脚本自己的窗口当成游戏窗口（实测踩过）。
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
                hwnd = int(desktop_win._hWnd)
                if not hwnd or win_mod.is_own_window(hwnd):
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

    def _template_for_state(self, state, templates):
        if state in self._CLICK_STATES:
            return templates.get(self._CLICK_STATES[state][0])
        return None

    @staticmethod
    def _previous_template_for_state(state, templates):
        """当前步骤未出现时允许回查一次的上一步模板与动作名。"""
        previous = {
            "WAIT_ENTER": (templates.get("start_game"), "开始游戏"),
            "WAIT_SWITCH": (templates.get("enter_game"), "进入游戏"),
            "WAIT_EXISTING": (templates.get("switch_role"), "切换"),
            "WAIT_ROLE": (templates.get("existing_role"), "已有角色"),
        }
        item = previous.get(state)
        return item if item and item[0] is not None else None

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
        if not rect or rect[2] <= 0 or rect[3] <= 0:
            return None
        image = win_mod.grab(rect)
        if image is None:
            return None
        # 挖掉脚本自己的窗口：悬浮日志窗置顶，日志文字里含「开始游戏/进入游戏/切换/已有角色」，
        # 不挖掉就会被模板匹配命中，把脚本自己的窗口当成游戏窗口（实测踩过）。
        win_mod.mask_rects(image, rect, win_mod.own_window_rects())
        return win_mod.ScaledScene(rect, None, img=image)

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

    def _click_roster_role(self, ctx, win, profile):
        """在“已有角色”页 OCR 找到档案下拉框选定的角色名并点击。"""
        role_id = str(profile.get("expected_role_id") or "")
        expected_name = (profile.get("expected_role_name") or "").strip()
        if (not role_id and not expected_name) or not win.activate():
            return False
        names = accounts.roster([win])
        scene = self._scene(ctx, win)
        hit = accounts.locate_roster_name(scene.img if scene is not None else None, names, role_id,
                                          expected_name=expected_name)
        if hit is None:
            return False
        xy = scene.to_screen(hit[0], hit[1])
        ctx.log("OCR 命中角色名 %s（%.3f）。" % (profile.get("expected_role_name") or role_id, hit[2]),
                level="hit")
        ctx.mouse.click(xy[0], xy[1])
        return True
