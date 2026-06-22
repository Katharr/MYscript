# -*- coding: utf-8 -*-
"""
运镖任务（一次性、循环押镖状态机）。

游戏自带「自动战斗 + 自动寻路」全托管，脚本只做导航 + 监控 + 关键点击：

  开「活动」(快捷键 Alt+C) → 滚轮找「运镖」条目 → 点该行【右侧的「参加」按钮】(按行匹配，不是点条目本身)
  → 弹出对话框 → 点「押送普通镖银」→ 开始运镖(自动寻路+自动战斗)
  → 一趟运镖结束后，若还有次数，会【再次弹出同一个对话框】→ 再点「押送普通镖银」
  → 循环到不再弹出对话框(默认 3 趟用完) → 停止。

与「宝图」共用同一套导航思路（开活动/按行点参加/帧差判静止），但更短：
没有挖宝阶段，主循环就是「监控运镖 → 对话框复现就续点」。

导航靠 ctx.send_hotkey(动作名)（键位在 config.hotkeys，用户可按游戏「系统设置-快捷键」核对）。
滑动用鼠标滚轮。

停止：①对话框不再弹出(运镖次数用完，主)②做满 max_escorts 趟(保险)③时间上限分钟(安全网)
④手动停止/鼠标甩左上角 failsafe。
安全默认 dry_run=true：不发快捷键/不点关键操作，只对当前屏幕做识别自检，便于先验证模板。
"""

import time

from ..core import vision
from ..core import window as win_mod
from .base import Task, register

# 状态机状态
S_OPEN_ACTIVITY = "OPEN_ACTIVITY"
S_DIALOG = "DIALOG"          # 点「参加」后等首个「押送普通镖银」对话框
S_ESCORTING = "ESCORTING"    # 运镖中（自动寻路+自动战斗），监控对话框复现/全部结束
S_FINISHED = "FINISHED"

_STILL_DIFF = 8.0   # 帧差低于此视为画面静止（人物不动）的默认阈值——可被 loop.still_diff 覆盖。
#   太小→人物待机动画/周围NPC玩家走动/特效会让整屏帧差长期偏高，永远判不到「静止」→误判运镖没完成。
#   运行时日志会实时打印真实帧差，照着把阈值设到「静止时帧差」之上、「走动时帧差」之下即可。

# 模板键（用 escort_ 前缀，避免和「宝图」任务的同名模板在磁盘上互相覆盖——
#   标定存盘按 templates/tm_<key>.png 命名，只按 key 区分，不区分任务）。
_FLAG_KEYS = ["escort_entry", "escort_join", "escort_silver", "escort_confirm",
              "escort_ongoing", "escort_battle"]
_REQUIRED_FLAGS = ["escort_entry", "escort_join", "escort_silver", "escort_confirm",
                   "escort_ongoing"]


@register
class EscortTask(Task):
    name = "escort"
    title = "运镖"
    description = "自动开活动→参加运镖→押送普通镖银→循环押满次数（战斗/寻路交给游戏自动）"

    CALIBRATION = {
        "regions": [
            ("scene", "主识别区", "整窗或大半屏——对话框/战斗等所有标志都在这里找，框大一点"),
            ("activity_list", "活动列表区域", "「活动」界面里那片列表，滚轮在此翻找「运镖」条目"),
        ],
        "templates": [
            ("escort_entry", "运镖入口", "活动列表里「运镖」那一条，框图标+文字、要独特"),
            ("escort_join", "参加按钮", "活动列表里「运镖」那一行右侧的「参加」按钮，框按钮本身、要独特"),
            ("escort_silver", "「押送普通镖银」按钮", "弹出对话框里要点的那个「押送普通镖银」按钮"),
            ("escort_confirm", "「确认」按钮", "点完「押送普通镖银」后再弹出的确认按钮，框按钮本身、要独特"),
            ("escort_ongoing", "「运镖中」标志", "运镖途中一直挂在屏幕上的标志（如镖银图标/运镖任务追踪条），"
                                              "只要它在就说明还在运镖、不会停。框它独特的部分"),
            ("escort_battle", "战斗界面标志(可选)", "战斗独有的画面元素，用于避免战斗期被误判为运镖结束"),
        ],
        "watchlist": False,
    }

    # ------------------------------------------------------------------
    def preflight(self, ctx):
        tc = ctx.task_cfg(self.name)
        problems = []
        regions = tc.get("regions", {})
        templates = tc.get("templates", {})

        for rk, label in [("scene", "主识别区"), ("activity_list", "活动列表区域")]:
            if not regions.get(rk):
                problems.append(f"『{label}』未标定 —— 请先做标定")

        for tk in _REQUIRED_FLAGS:
            path = templates.get(tk)
            if not path or vision.load_template(path) is None:
                problems.append(f"模板『{tk}』缺失或加载失败 —— 请在标定向导里框选裁图")

        if not ctx.hotkeys.get("open_activity"):
            problems.append("打开『活动』缺快捷键：请在 config.hotkeys.open_activity 填上（如 alt+c）")

        if not ctx.window.locate():
            problems.append(f"没找到游戏窗口（标题含「{ctx.window.title_substr}」），请先打开游戏")

        # 可选模板缺失只提示
        if not templates.get("escort_battle") or vision.load_template(templates.get("escort_battle")) is None:
            ctx.log("提示：可选模板『escort_battle』未标定，将降级靠帧差+超时推进（可靠性略降）。", level="warn")

        return (len(problems) == 0), problems

    # ------------------------------------------------------------------
    def run(self, ctx):
        tc = ctx.task_cfg(self.name)
        loop = tc["loop"]
        regions = tc["regions"]
        dry_run = tc.get("dry_run", True)
        threshold = loop["match_threshold"]
        self.flags = self._load_flags(tc)
        self.max_escorts = max(1, int(loop.get("max_escorts", 3)))

        time_limit = loop.get("time_limit_min", 0) or 0
        start_ts = time.time()
        deadline = start_ts + time_limit * 60 if time_limit > 0 else None

        if not self._is_admin():
            ctx.log("⚠ 当前非管理员权限：游戏在前台时鼠标/键盘注入可能被 UIPI 拦截。"
                    "请用『以管理员身份运行』重开。", level="warn")

        if dry_run:
            ctx.log("演练模式：不会真正参加运镖，仅对当前屏幕循环做『各标志识别自检』。"
                    "手动打开对应界面，看日志能否认出 运镖入口/参加按钮/押送普通镖银/战斗。", level="warn")
            self._dry_run_selfcheck(ctx, regions, threshold, deadline)
            return

        ctx.log(f"★ 实战模式：会真开活动、真参加运镖、循环押满 {self.max_escorts} 趟 ★", level="warn")
        if time_limit > 0:
            ctx.log(f"时间上限 {time_limit} 分钟（到点自停）。主终止条件是对话框不再弹出（次数用完）。")

        self._escorts = 0
        state = S_OPEN_ACTIVITY
        state_enter = time.time()
        recover = 0

        while not ctx.should_stop():
            if deadline and time.time() >= deadline:
                ctx.log(f"已达时间上限 {time_limit} 分钟，停止。")
                break
            if state == S_FINISHED:
                break
            if not ctx.window.locate():
                ctx.log("游戏窗口不见了，2 秒后重试…", level="warn")
                self._interruptible_sleep(ctx, 2.0)
                continue

            nxt = self._dispatch(ctx, state, loop, regions, threshold)

            if nxt == "STOP":
                break
            if nxt == "STUCK":
                recover += 1
                ctx.log(f"状态 {state} 卡住（第 {recover}/{loop.get('max_stuck_recover',3)} 次），尝试恢复…",
                        level="warn")
                self._save_capture(self._grab_scene(ctx, regions), f"stuck_{state}")
                if recover >= loop.get("max_stuck_recover", 3):
                    ctx.log("多次卡死仍无进展，主动停止。", level="error")
                    break
                # 卡死兜底：只在「还没开始任何一趟运镖」时回开活动重试；已经在押镖途中卡死则直接收尾，
                # 避免活动已参加、按钮变「已参加」后重开活动连环卡死。
                if self._escorts == 0:
                    self._recover(ctx)
                    state, state_enter = S_OPEN_ACTIVITY, time.time()
                    continue
                ctx.log("已在押镖途中卡死，按已完成处理并停止。", level="warn")
                break

            if nxt == S_ESCORTING:
                recover = 0
            if nxt != state:
                state, state_enter = nxt, time.time()

        ctx.log(f"已停止。共完成 {getattr(self,'_escorts',0)} 趟运镖，"
                f"用时 {(time.time()-start_ts)/60:.1f} 分钟。")

    # ------------------------------------------------------------------
    def _dispatch(self, ctx, state, loop, regions, threshold):
        if state == S_OPEN_ACTIVITY:
            return self._st_open_activity(ctx, loop, regions, threshold)
        if state == S_DIALOG:
            return self._st_dialog(ctx, loop, regions, threshold)
        if state == S_ESCORTING:
            return self._st_escorting(ctx, loop, regions, threshold)
        return S_FINISHED

    # ------------------------------------------------------------------
    def _focus(self, ctx):
        """把游戏窗口切到前台——键盘快捷键(SendInput)只发给有焦点的窗口，发键前必须先激活。"""
        try:
            ctx.window.activate()
        except Exception:
            pass

    def _recover(self, ctx):
        """卡死兜底恢复（仅 STUCK 且尚未开始运镖时）：聚焦游戏 + 按一次 Esc 清掉异常弹窗。"""
        self._focus(ctx)
        ctx.log("卡死恢复：按一次 Esc 关掉可能的异常弹窗…")
        if ctx.send_hotkey("close_panel"):
            self._interruptible_sleep(ctx, self._jitter(0.25, ctx))

    def _st_open_activity(self, ctx, loop, regions, threshold):
        # 打开活动：发 open_activity 快捷键（默认 Alt+C；发键前先激活游戏）
        self._focus(ctx)
        if not ctx.send_hotkey("open_activity"):
            ctx.log("打不开活动界面（open_activity 快捷键未配置）。", level="error")
            return "STUCK"
        ctx.log("已打开活动，滚轮翻找「运镖」…")
        self._interruptible_sleep(ctx, self._jitter(0.6, ctx))

        hit = self._scroll_find(ctx, regions.get("activity_list"),
                                self.flags.get("escort_entry"), loop, threshold, "运镖入口")
        if hit is None:
            ctx.log("活动列表里没找到「运镖」入口。", level="warn")
            return "STUCK"
        ctx.log(f"找到「运镖」条目（相似度 {hit[2]:.3f}），在该行右侧找「参加」按钮…")

        # 点该行【右侧的「参加」按钮】，不是点条目本身
        join = self._find_join_on_row(ctx, regions.get("activity_list"), (hit[0], hit[1]), threshold)
        if join is None:
            ctx.log("找到了「运镖」但没认出右侧的「参加」按钮（检查 escort_join 模板/阈值）。", level="warn")
            return "STUCK"
        ctx.mouse.click(join[0], join[1])
        ctx.log(f"点击「参加」（相似度 {join[2]:.3f}），等待弹出对话框。", level="hit")
        return S_DIALOG

    def _find_join_on_row(self, ctx, list_region, entry_screen_xy, threshold):
        """在「运镖」条目所在【行】的右侧条带里匹配「参加」按钮(escort_join)。
        命中返回 (screen_x, screen_y, score)，否则 None。
        按行+只取条目右侧，能抗滚动、抗一屏多个「参加」按钮。"""
        join_tpl = self.flags.get("escort_join")
        entry_tpl = self.flags.get("escort_entry")
        if join_tpl is None:
            ctx.log("找「参加」失败：escort_join 模板未标定。", level="warn")
            return None
        rect = (ctx.window.region_to_screen_rect(list_region)
                if list_region else ctx.window.rect())
        if rect is None:
            return None
        scene = win_mod.grab(rect)
        if scene is None:
            return None
        rx, ry = rect[0], rect[1]
        ex, ey = entry_screen_xy
        # 行条带高度：取条目模板高 ×2，下限 40px；纵向以条目中心为中线
        row_h = entry_tpl.shape[0] if entry_tpl is not None else 40
        band = max(40, int(row_h * 2))
        sh, sw = scene.shape[:2]
        # 换算到 scene 局部坐标：纵向取条带、横向从条目中心到列表右缘（只看右侧）
        ey_local = int(ey - ry)
        ex_local = int(ex - rx)
        y0 = max(0, ey_local - band // 2)
        y1 = min(sh, ey_local + band // 2)
        x0 = max(0, ex_local)
        x1 = sw
        if y1 - y0 < 1 or x1 - x0 < 1:
            return None
        crop = scene[y0:y1, x0:x1]
        m = vision.match(crop, join_tpl, threshold)
        if m is None:
            return None
        cx, cy, score = m
        return (rx + x0 + cx, ry + y0 + cy, score)

    def _st_dialog(self, ctx, loop, regions, threshold):
        """点「参加」后等对话框出现，点首个「押送普通镖银」，开始第 1 趟运镖。"""
        ctx.log("等待传送+寻路到镖头、对话框出现…")
        timeout = loop.get("dialog_timeout_sec", 60)
        t0 = time.time()
        tpl = self.flags.get("escort_silver")
        while not ctx.should_stop():
            if time.time() - t0 > timeout:
                ctx.log("等「押送普通镖银」对话框超时。", level="warn")
                return "STUCK"
            scene_rect = self._scene_rect(ctx, regions)
            cur = win_mod.grab(scene_rect)
            hit = vision.match(cur, tpl, threshold) if tpl is not None else None
            if hit is not None:
                cx, cy, score = hit
                ctx.mouse.click(scene_rect[0] + cx, scene_rect[1] + cy)
                self._escorts = 1
                ctx.log(f"点「押送普通镖银」（相似度 {score:.3f}），等确认框…", level="hit")
                self._interruptible_sleep(ctx, self._jitter(0.4, ctx))
                self._click_confirm(ctx, loop, regions, threshold)
                ctx.log("开始第 1 趟运镖。", level="hit")
                return S_ESCORTING
            self._interruptible_sleep(ctx, 0.4)
        return "STOP"

    def _click_confirm(self, ctx, loop, regions, threshold):
        """点「押送普通镖银」后弹出的「确认」按钮。等到并点它，返回 True；
        超时仍没出现则容错返回 False（继续往下走，不卡死）。"""
        tpl = self.flags.get("escort_confirm")
        if tpl is None:
            return False
        timeout = loop.get("confirm_timeout_sec", 10)
        t0 = time.time()
        while not ctx.should_stop():
            if time.time() - t0 > timeout:
                ctx.log("等运镖「确认」按钮超时（可能本次无需确认），继续。", level="warn")
                return False
            scene_rect = self._scene_rect(ctx, regions)
            cur = win_mod.grab(scene_rect)
            hit = vision.match(cur, tpl, threshold) if cur is not None else None
            if hit is not None:
                cx, cy, score = hit
                ctx.mouse.click(scene_rect[0] + cx, scene_rect[1] + cy)
                ctx.log(f"点「确认」（相似度 {score:.3f}）。", level="hit")
                return True
            self._interruptible_sleep(ctx, 0.3)
        return False

    def _st_escorting(self, ctx, loop, regions, threshold):
        """监控运镖。靠「运镖中」标志(escort_ongoing)判断在不在运镖途中——
          · 对话框「押送普通镖银」再次弹出 → 这趟跑完、还有次数，续点开始下一趟（次数+1）；
          · 只要「运镖中」标志在 或 在战斗中 → 绝不判结束（刷新计时）；
          · 「运镖中」标志这趟出现过、现在消失了、且没有新对话框、且已押满设定趟数
            → 持续 done_idle_sec 秒后判定本批运镖全部结束。
        这样既不会在「点完确认刚开始、人物还没动」时误停，也不会在两趟之间的空档误停。"""
        ctx.log(f"第 {self._escorts}/{self.max_escorts} 趟运镖中（寻路/战斗交给游戏），监控运镖中/对话框…")
        done_grace = loop.get("done_idle_sec", 6.0)
        per_trip_timeout = loop.get("escort_timeout_sec", 600)
        tpl = self.flags.get("escort_silver")
        t0 = time.time()              # 最近一次「明确在运镖/战斗/起步」的时间，用于超时兜底
        seen_ongoing = False          # 这趟是否出现过「运镖中」标志（出现过才允许靠它消失判结束）
        gone_since = None             # 「运镖中」标志消失起点
        last_diag = 0.0
        while not ctx.should_stop():
            scene_rect = self._scene_rect(ctx, regions)
            cur = win_mod.grab(scene_rect)

            # 对话框又弹出来了 → 这一趟跑完、还有次数，续点开始下一趟
            hit = vision.match(cur, tpl, threshold) if tpl is not None else None
            if hit is not None:
                if self._escorts >= self.max_escorts:
                    # 已押满设定趟数仍弹框（异常/识别抖动）→ 不再续点，直接收尾
                    ctx.log(f"已完成设定的 {self.max_escorts} 趟，停止（不再续押）。", level="hit")
                    return S_FINISHED
                cx, cy, score = hit
                ctx.mouse.click(scene_rect[0] + cx, scene_rect[1] + cy)
                self._escorts += 1
                ctx.log(f"上一趟结束，对话框再次弹出 → 点「押送普通镖银」开始第 {self._escorts} 趟"
                        f"（相似度 {score:.3f}），等确认框…", level="hit")
                self._interruptible_sleep(ctx, self._jitter(0.4, ctx))
                self._click_confirm(ctx, loop, regions, threshold)
                # 新一趟：重置该趟的运镖中/结束计时
                t0, seen_ongoing, gone_since = time.time(), False, None
                self._interruptible_sleep(ctx, self._jitter(1.0, ctx))
                continue

            ongoing = self._present(cur, "escort_ongoing", threshold)
            in_battle = self._present(cur, "escort_battle", threshold)

            if ongoing or in_battle:
                # 明确在运镖途中/战斗中 → 绝不停，刷新计时
                if ongoing:
                    seen_ongoing = True
                gone_since = None
                t0 = time.time()
            else:
                # 既没在运镖也没在战斗、也没对话框
                if seen_ongoing and self._escorts >= self.max_escorts:
                    # 这趟运镖中标志出现过又消失了 + 已是最后一趟 + 没有新对话框 → 准备收尾
                    if gone_since is None:
                        gone_since = time.time()
                    elif time.time() - gone_since >= done_grace:
                        ctx.log("「运镖中」标志已消失且无更多对话框 → 本批运镖全部结束。", level="hit")
                        return S_FINISHED
                # 否则：要么还没开始（运镖中标志还没出现），要么还有次数要等下一个对话框 → 继续等

            last_diag = self._log_escort_diag(ctx, ongoing, in_battle, seen_ongoing,
                                              gone_since, done_grace, last_diag)
            if time.time() - t0 > per_trip_timeout:
                ctx.log("长时间既无『运镖中』也无对话框，按本批结束处理。", level="warn")
                return S_FINISHED
            self._interruptible_sleep(ctx, 0.3)
        return "STOP"

    def _log_escort_diag(self, ctx, ongoing, in_battle, seen_ongoing,
                         gone_since, done_grace, last_diag):
        """每 ~5s 打印一次运镖状态诊断。返回新的 last_diag 时间。"""
        now = time.time()
        if now - (last_diag or 0.0) < 5.0:
            return last_diag
        if ongoing:
            st = "运镖中"
        elif in_battle:
            st = "战斗中"
        elif not seen_ongoing:
            st = "等运镖开始/过场"
        else:
            held = (now - gone_since) if gone_since else 0.0
            st = f"运镖中标志已消失 {held:.1f}/{done_grace}s（等满即判结束）"
        ctx.log(f"监控…{st}（已完成 {self._escorts}/{self.max_escorts} 趟）")
        return now

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------
    def _load_flags(self, tc):
        templates = tc.get("templates", {})
        return {k: vision.load_template(templates.get(k)) if templates.get(k) else None
                for k in _FLAG_KEYS}

    def _scene_rect(self, ctx, regions):
        region = regions.get("scene")
        return ctx.window.region_to_screen_rect(region) if region else ctx.window.rect()

    def _grab_scene(self, ctx, regions):
        rect = self._scene_rect(ctx, regions)
        return win_mod.grab(rect) if rect else None

    def _present(self, scene, flag_key, threshold):
        tpl = self.flags.get(flag_key)
        if scene is None or tpl is None:
            return False
        return vision.match(scene, tpl, threshold) is not None

    def _scroll_find(self, ctx, list_region, tpl, loop, threshold, label):
        """在 list_region 内滚轮翻找 tpl。命中返回 (screen_x, screen_y, score)；
        翻完 scroll_max_tries 屏仍无→返回 None。"""
        if tpl is None:
            ctx.log(f"找 {label} 失败：模板未标定。", level="warn")
            return None
        tries = max(1, loop.get("scroll_max_tries", 8))
        step = loop.get("scroll_step", -3)
        for i in range(tries):
            if ctx.should_stop():
                return None
            rect = (ctx.window.region_to_screen_rect(list_region)
                    if list_region else ctx.window.rect())
            if rect is None:
                return None
            scene = self._wait_still(ctx, rect, loop.get("still_min_sec", 0.3),
                                     loop.get("still_wait_sec", 2.0))
            if scene is None:
                return None
            hit = vision.match(scene, tpl, threshold)
            if hit is not None:
                cx, cy, score = hit
                return (rect[0] + cx, rect[1] + cy, score)
            # 没找到→在列表中心向下滚一屏再找
            cx_c, cy_c = rect[0] + rect[2] // 2, rect[1] + rect[3] // 2
            ctx.mouse.scroll(step, cx_c, cy_c)
        return None

    def _dry_run_selfcheck(self, ctx, regions, threshold, deadline):
        """演练：周期性对当前屏幕识别各标志，报告命中，便于用户验证模板/阈值。"""
        keys = [("escort_entry", "运镖入口"), ("escort_join", "参加按钮"),
                ("escort_silver", "押送普通镖银"), ("escort_confirm", "确认"),
                ("escort_ongoing", "运镖中"), ("escort_battle", "战斗")]
        while not ctx.should_stop():
            if deadline and time.time() >= deadline:
                ctx.log("演练时间上限到，停止。")
                break
            if not ctx.window.locate():
                self._interruptible_sleep(ctx, 1.5)
                continue
            scene = self._grab_scene(ctx, regions)
            found = []
            for key, label in keys:
                tpl = self.flags.get(key)
                if tpl is None:
                    continue
                hit = vision.match(scene, tpl, threshold) if scene is not None else None
                if hit is not None:
                    found.append(f"{label}({hit[2]:.2f})")
            if found:
                ctx.log("识别到：" + "、".join(found), level="hit")
            else:
                ctx.log("当前屏幕未识别到任何已标定标志（请打开对应界面再看）。")
            self._interruptible_sleep(ctx, self._jitter(1.5, ctx))
