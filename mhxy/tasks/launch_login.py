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

    # 每步按钮上的文字：模板没命中时用它做 OCR 兜底（见 _click_by_text）。
    _STEP_TEXT = {"点击开始游戏": "开始游戏", "点击进入游戏": "进入游戏",
                  "点击切换角色": "切换", "点击已有角色": "已有角色"}
    # 文字兜底只接受「像按钮那一条」的文字块：太大=正文/标题，太小=噪点。
    _TEXT_BOX = {"min_height": 16, "max_height": 64, "min_width": 30, "max_width": 360,
                 "min_ratio": 1.2, "max_ratio": 9.0}
    _MISS_LOG_INTERVAL = 5.0            # 「本步没认出来」的日志节流（每轮都会重试，不能刷屏）

    def __init__(self):
        # 识别失败诊断 / 文字兜底的节流状态（_run_profile 每档案会重置一次）。
        self._ocr_after = 6.0
        self._ocr_interval = 6.0
        self._miss_since, self._miss_logged = {}, {}
        self._ocr_first, self._ocr_last = {}, {}

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
                ctx.log("[%s] 登录流程完成。" % label, level="hit")
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
        # 每档案重置「没认出来」与文字兜底的节流状态（见 _note_miss / _click_by_text）。
        loop_cfg = ctx.task_cfg(self.name).get("loop") or {}
        self._ocr_after = max(0.0, float(loop_cfg.get("ocr_fallback_after_sec", 6.0)))
        self._ocr_interval = max(2.0, float(loop_cfg.get("ocr_fallback_interval_sec", 6.0)))
        self._miss_since, self._miss_logged = {}, {}
        self._ocr_first, self._ocr_last = {}, {}

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
            self._dump_debug(ctx, "launcher")
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
            # 启动器点击后会新建游戏外壳。接管本次新增的游戏窗口后，后续识别必须只在
            # 该窗口内进行，不能再扫整张桌面而误命中已登录的其它号。
            if state != "WAIT_START":
                game_win = self._resolve_game_window(ctx, before)
                if game_win is not None:
                    win = game_win
            if win.rect() is None:
                # 点击“开始游戏”后，启动器可能销毁原 HWND 并新建游戏外壳；按下一状态
                # 的模板接管新窗口，而不是把正常窗口转换误报为登录窗口关闭。
                replacement = self._find_template_window(
                    ctx, self._template_for_state(state, templates), threshold)
                if replacement is not None:
                    win = replacement
                elif time.time() - state_at > timeout:
                    self._dump_debug(ctx, state)
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
                        # 选中角色后，只有在该次新游戏窗口的顶部标签通过核验，才写入窗口身份绑定。
                        game_win = self._place_after_login(ctx, win, before, corner, corner_settle, corner_margin)
                        self._verify_game_identity(ctx, game_win, profile)
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
                self._dump_debug(ctx, state)
                return "%s 超时" % state
            self._interruptible_sleep(ctx, 0.35)
        return "stopped"

    def _place_after_login(self, ctx, win, before, corner, settle_sec, margin):
        """等待并定位本次新游戏窗口，按需归位后返回该窗口。"""
        self._interruptible_sleep(ctx, settle_sec)   # 等游戏窗口建好/外壳切换稳定再归位
        target = self._resolve_game_window(ctx, before)
        if target is None:
            if corner:
                label = self._CORNER_LABELS.get(corner, corner)
                ctx.log("窗口归位到%s失败：没找到本号新出现的游戏窗口。" % label, level="warn")
            return None
        if not corner:
            return target
        label = self._CORNER_LABELS.get(corner, corner)
        rect = target.move_to_corner(corner, margin=margin)
        if rect is None:
            ctx.log("窗口归位到%s失败（窗口可能已最小化或被外壳约束）。" % label, level="warn")
        else:
            ctx.log("窗口已归位到%s：%s。" % (label, rect), level="hit")
        return target

    def _verify_game_identity(self, ctx, game_win, profile):
        """一键登录后的唯一身份刷新入口：前台 OCR 核验顶部标签并写运行期绑定。"""
        if game_win is None:
            ctx.log("登录后未找到游戏窗口，未建立角色绑定。", level="warn")
            return False
        expected_id = str(profile.get("expected_role_id") or "")
        expected_name = (profile.get("expected_role_name") or "").strip()
        for _attempt in range(10):
            if ctx.should_stop():
                break
            try:
                if game_win.activate():
                    accounts.labels_for([game_win])
                    actual_id = accounts.bound_role_id(game_win)
                    if actual_id and (not expected_id or actual_id == expected_id):
                        accounts.bind_launch_session(game_win, actual_id, profile.get("id"))
                        ctx.log("登录后窗口角色核验成功：%s。" % (expected_name or actual_id), level="hit")
                        return True
            except Exception:
                pass
            self._interruptible_sleep(ctx, 1.0)
        ctx.log("登录后窗口角色核验未通过，暂显示为号N；可点“唤出所有游戏窗口”重新检测。", level="warn")
        return False

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
        """按官方启动器进程定位窗口，支持已存在或最小化的单例启动器。

        ⚠ 必须排除「0 尺寸的隐藏顶层窗」：启动器是 WebView2 外壳，同进程下通常还有若干又小又不可见
        的顶层窗口。若随手返回其中一个，随后 activate()/rect()/点击全都会落空——现象正是
        「启动器明明开着，脚本却没反应」。故这里只认尺寸够大的窗口，可见的优先于最小化的。
        """
        title = ctx.cfg.get("window_title", "梦幻西游")
        offset = ctx.cfg.get("window_offset", [0, 0])
        try:
            raw = win_mod.gw.getAllWindows()
        except Exception:
            return None
        candidates = []
        for desktop_win in raw:
            try:
                hwnd = int(desktop_win._hWnd)
                if not hwnd or win_mod.is_own_window(hwnd):
                    continue
                image = win_mod.proc_image_path(hwnd)
                if not image or image.rsplit("\\", 1)[-1].lower() != launcher.LAUNCHER_EXE.lower():
                    continue
                width, height = int(desktop_win.width or 0), int(desktop_win.height or 0)
                if width <= 200 or height <= 160:
                    continue
                minimized = bool(getattr(desktop_win, "isMinimized", False))
                candidates.append((minimized, -(width * height), desktop_win))
            except Exception:
                continue
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item[0], item[1]))
        return win_mod.GameWindow(title, offset).bind(candidates[0][2])

    def _find_template_window(self, ctx, tpl, threshold, allowed_hwnds=None):
        """在桌面窗口里找「命中该模板」的那个窗口，返回窗口对象；找不到返回 None。

        命中点用 to_screen() 换回屏幕坐标后再与窗口矩形比对，故返回的是【真正压着这个按钮】的窗口
        （非游戏窗口的场景是整屏，命中落在别的窗口上时不会被误当成目标）。
        """
        if tpl is None:
            return None
        fallback = None
        for win in self._desktop_windows(ctx):
            hwnd = self._window_hwnd(win)
            if allowed_hwnds is not None and hwnd not in allowed_hwnds:
                continue
            scene = self._scene(ctx, win)
            hit = scene.match(tpl, threshold) if scene is not None else None
            if hit is None:
                continue
            xy = scene.to_screen(hit[0], hit[1])
            rect = win.rect()
            if rect and rect[0] <= xy[0] <= rect[0] + rect[2] and rect[1] <= xy[1] <= rect[1] + rect[3]:
                return win
            if fallback is None:
                fallback = win
        return fallback

    def _wait_new_window(self, ctx, before, timeout, start_tpl, threshold):
        started = time.time()
        deadline = started + timeout
        warned = False
        activate_warned = False
        while not ctx.should_stop() and time.time() < deadline:
            # 官方启动器是单例：可能复用已有 HWND，也可能最小化后被 ShellExecute 唤起。
            # 所以先按 MyPCLauncher_x64r.exe 精确找窗口，不能只接受“新 HWND + 模板命中”。
            win = self._official_launcher_window(ctx)
            if win is not None:
                # ⚠ 这里【不再】用 activate() 当准入门槛：切前台失败时若直接丢弃这个窗口，整条流程
                # 会一声不响地空转到超时（日志里连「开始游戏」这一步都不会出现）。改成记一次告警后
                # 照常返回，由点击步骤继续重试切前台并明确报错。
                if not win.activate() and not activate_warned:
                    ctx.log("已找到启动器窗口，但未能切到前台；点击前会继续重试。", level="warn")
                    activate_warned = True
                ctx.log("已检测到 MyPCLauncher_x64r.exe 启动器。")
                return win
            # 兼容未来外壳进程变化：优先「本次新增且命中开始游戏模板」的窗口；启动器本来就已经开着
            # （单例复用不会有新 HWND）时，再放宽到全部桌面窗口。
            new_hwnds = self._desktop_hwnds(ctx) - before
            win = (self._find_template_window(ctx, start_tpl, threshold, new_hwnds)
                   or self._find_template_window(ctx, start_tpl, threshold, None))
            if win is not None:
                if not win.activate() and not activate_warned:
                    ctx.log("已通过开始游戏模板找到启动窗口，但未能切到前台；点击前会继续重试。",
                            level="warn")
                    activate_warned = True
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

    def _is_game_window(self, ctx, win):
        """当前窗口是否已是游戏外壳，而非仍在登录启动器阶段。"""
        hwnd = self._window_hwnd(win)
        if not hwnd:
            return False
        try:
            games = win_mod.locate_all(ctx.cfg.get("window_title", "梦幻西游"),
                                       ctx.cfg.get("window_offset", [0, 0]))
        except Exception:
            return False
        return any(self._window_hwnd(game) == hwnd for game in games)

    def _scene(self, ctx, win):
        # 启动器阶段的界面可能在新建外壳前转换，仍需整屏找模板；一旦已接管游戏窗口，
        # 只截当前窗口，避免其它号或桌面窗口的同类按钮被误识别。
        game_window = self._is_game_window(ctx, win)
        rect = win.rect() if game_window else self._screen_rect()
        if not rect or rect[2] <= 0 or rect[3] <= 0:
            return None
        image = win_mod.grab(rect)
        if image is None:
            return None
        if not game_window:
            # 启动器整屏识别时挖掉脚本自身窗口，避免日志文字被匹配为流程按钮。
            # ⚠ 只挖【确实压在启动器上面】的那些自家窗口：自家窗口在启动器【下面】时，画面里那一片
            # 本来就是启动器的像素，按矩形一刀切会把启动器自己的内容涂黑——实测「小窗与启动器矩形
            # 相交 → 启动器的『开始游戏』被整块涂黑 → 永远认不出来；把窗口挪开一点就好了」正是这条。
            target = self._window_hwnd(win)
            rects = [r for hwnd, r in win_mod.own_window_frames()
                     if win_mod.is_window_above(hwnd, target)]
            win_mod.mask_rects(image, rect, rects)
        return win_mod.ScaledScene(rect, None, img=image)

    def _click_template(self, ctx, win, tpl, threshold, action):
        """识别并点击某一步的按钮。命中才点；**没命中、切不到前台都留下明确日志**。

        为什么必须留日志：这一步原本只在成功时打印，识别失败时整条流程一声不响地空转到超时，
        用户看到的只是「启动器开了却没点开始游戏、日志里也没有这一步」，完全无从下手。
        另外识别失败到一定时长后会启用【文字兜底】（见 _click_by_text）——启动器是远端 WebView2
        页面，按钮外观会随官方更新变，模板一旦失效就再没有第二条通道。
        """
        scene = self._scene(ctx, win)
        result = scene.match(tpl, threshold) if scene is not None else None
        if result is None:
            self._note_miss(ctx, action, tpl, scene, threshold)
            return self._click_by_text(ctx, win, scene, action)
        if not win.activate():
            self._note_activate_failed(ctx, action)
            return False
        scene = self._scene(ctx, win)
        result = scene.match(tpl, threshold) if scene is not None else None
        if result is None:
            return False
        xy = scene.to_screen(result[0], result[1])
        ctx.log("%s（%.3f）。" % (action, result[2]), level="hit")
        ctx.mouse.click(xy[0], xy[1])
        return True

    # ---- 识别失败时的诊断与文字兜底 ----
    def _note_miss(self, ctx, action, tpl, scene, threshold):
        """本步没认出来：节流记一次日志，并带上本次最高匹配分（判断「模板彻底失效」还是「差一点」）。"""
        now = time.time()
        first = self._miss_since.setdefault(action, now)
        if now - self._miss_logged.get(action, 0.0) < self._MISS_LOG_INTERVAL:
            return
        self._miss_logged[action] = now
        score = 0.0
        try:
            best, _where = vision.best_score(scene.img if scene is not None else None, tpl)
            score = float(best)
        except Exception:
            pass
        ctx.log("%s：未识别到按钮（已等 %.0f 秒，本次最高匹配分 %.3f，阈值 %.2f）。"
                % (action, now - first, score, float(threshold)), level="warn")

    def _note_activate_failed(self, ctx, action):
        """认出来了但切不到前台：也必须说出来，否则表现和「没认出来」一模一样。"""
        now = time.time()
        if now - self._miss_logged.get("fg:" + action, 0.0) < self._MISS_LOG_INTERVAL:
            return
        self._miss_logged["fg:" + action] = now
        ctx.log("%s：已识别到按钮，但未能切到前台（本轮不点击，继续重试）。" % action, level="warn")

    def _click_by_text(self, ctx, win, scene, action):
        """文字兜底：模板认不出来时，用 OCR 找按钮上的文字并点击（见 core/accounts.locate_text）。

        只在【本步认不出来】持续 `ocr_fallback_after_sec` 之后、且每隔 `ocr_fallback_interval_sec`
        才试一次（OCR 比模板匹配贵得多）。命中点还必须落在目标（启动器）窗口内、且文字块形状像按钮，
        避免点到公告里的同名字。切不到前台就不点——宁可继续重试，也不把点击落到别的窗口上。
        """
        text = self._STEP_TEXT.get(str(action).replace("重新点击", "点击"))
        if not text or scene is None:
            return False
        now = time.time()
        if now - self._ocr_first.setdefault(action, now) < getattr(self, "_ocr_after", 6.0):
            return False
        if now - self._ocr_last.get(action, 0.0) < getattr(self, "_ocr_interval", 6.0):
            return False
        self._ocr_last[action] = now
        try:
            status = accounts.ocr_status()
        except Exception:
            status = ""
        if status.startswith("unavailable"):
            # OCR 引擎起不来（依赖缺失等）时必须说清楚，否则「文字也没找到」会把人误导到模板上。
            if now - self._miss_logged.get("ocr:" + action, 0.0) >= self._MISS_LOG_INTERVAL:
                self._miss_logged["ocr:" + action] = now
                ctx.log("文字兜底不可用：%s。" % status, level="warn")
            return False
        box_cfg = self._TEXT_BOX
        hit = accounts.locate_text(scene.img, [text], min_height=box_cfg["min_height"],
                                   max_height=box_cfg["max_height"], min_width=box_cfg["min_width"],
                                   max_width=box_cfg["max_width"])
        if hit is None:
            ctx.log("文字识别也没找到【%s】。" % text, level="warn")
            return False
        cx, cy, score, width, height, _matched = hit
        if height <= 0 or not (box_cfg["min_ratio"] <= width / float(height) <= box_cfg["max_ratio"]):
            return False
        xy = scene.to_screen(cx, cy)
        rects = []
        for candidate in (win, self._official_launcher_window(ctx)):
            rect = candidate.rect() if candidate is not None else None
            if rect and rect[2] > 200 and rect[3] > 160:
                rects.append(rect)
        if rects and not any(r[0] <= xy[0] <= r[0] + r[2] and r[1] <= xy[1] <= r[1] + r[3]
                             for r in rects):
            ctx.log("文字识别命中【%s】但不在目标窗口内，忽略。" % text, level="warn")
            return False
        if win is not None and not win.activate():
            self._note_activate_failed(ctx, action)
            return False
        ctx.log("文字识别命中【%s】（%.3f），已点击。" % (text, score), level="hit")
        ctx.mouse.click(xy[0], xy[1])
        return True

    def _dump_debug(self, ctx, tag):
        """超时现场落盘：把整屏存进 captures/，供事后判断是模板失效、按钮被挡还是窗口没到前台。"""
        try:
            rect = self._screen_rect()
            image = win_mod.grab(rect) if rect else None
            if image is None:
                return
            name = self._save_capture(image, "launch_stuck_%s" % tag)
            ctx.log("已保存超时现场截图：captures/%s" % name, level="warn")
        except Exception:
            pass

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
