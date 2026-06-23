# -*- coding: utf-8 -*-
"""
运镖任务（循环押镖；游戏自带自动寻路+自动战斗，脚本只做导航 + 监控 + 关键点击）。

用户描述的真实流程（每个号一批）：

  开「活动」(快捷键 Alt+C) → 滚轮找「运镖」条目 → 点该行【右侧的「参加」按钮】(按行匹配，不是点条目本身)
  → 弹出对话框 → 点「押送普通镖银」→ 点「确认」→ 开始运镖(自动寻路+自动战斗)
  → 一趟运镖结束后，若还有次数，会【再次弹出同一个对话框】→ 再点「押送普通镖银」→「确认」
  → 循环到不再弹出对话框(默认 3 趟用完) → 本号结束。

★ 多开轮转（用户要求「多号都要生效」，2026-06-23 重做）：
  原来是阻塞式状态机、只操作选中的第一个号。现仿「秘境降妖」改成非阻塞逐号轮转——
  每个号各持一份状态(record)，主循环对每个号【各推进一小步】(非阻塞)，号与号之间逐步轮转：
  号1 在自动运镖时，可以去把 号2/号3 也开起来押镖，再回头照看 号1。鼠标光标全局唯一，
  故单线程轮转、操作某号前先 activate() 切前台。单开=列表只有一个号、同走这套轮转。
  ⚠ 各号须【同尺寸】（共用标定点位）；一只鼠标的物理限制故多开节奏天然比单开慢。

导航靠 ctx.send_hotkey(动作名)（键位在 config.hotkeys，用户可按游戏「系统设置-快捷键」核对）。
滑动用鼠标滚轮。

停止：①所有号都押满 max_escorts 趟（或对话框不再弹出）②时间上限分钟(安全网)
③手动停止/鼠标甩左上角 failsafe。
安全默认 dry_run=true：不发快捷键/不点关键操作，只对各号当前屏幕做识别自检，便于先验证模板。
"""

import time

from ..core import vision
from ..core import window as win_mod
from .base import Task, register

# 每个号的状态机状态（非阻塞：每访问一次只推进一步）
S_OPEN_ACTIVITY = "OPEN_ACTIVITY"   # 发活动快捷键
S_FIND_CARD = "FIND_CARD"           # 活动列表里滚轮找「运镖」条目 → 点「参加」
S_DIALOG = "DIALOG"                 # 等首个「押送普通镖银」对话框 → 点它
S_CONFIRM = "CONFIRM"               # 点完押送后等「确认」按钮（容错超时）
S_ESCORTING = "ESCORTING"           # 运镖中：监控运镖中标志/对话框复现，续点下一趟或收尾

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
    description = "自动开活动→参加运镖→押送普通镖银→循环押满次数（战斗/寻路交给游戏自动，支持多开轮转）"

    CALIBRATION = {
        "regions": [
            ("scene", "主识别区", "留空=整个窗口当识别区(推荐)；对话框/战斗等标志都在这里找", True),
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

        # scene 留空=整窗检测，不再强制标定；活动列表区仍需标（滚轮翻找运镖条目）
        for rk, label in [("activity_list", "活动列表区域")]:
            if not regions.get(rk):
                problems.append(f"『{label}』未标定 —— 请先做标定")

        for tk in _REQUIRED_FLAGS:
            path = templates.get(tk)
            if not path or vision.load_template(path) is None:
                problems.append(f"模板『{tk}』缺失或加载失败 —— 请在标定向导里框选裁图")

        if not ctx.hotkeys.get("open_activity"):
            problems.append("打开『活动』缺快捷键：请在 config.hotkeys.open_activity 填上（如 alt+c）")

        if not ctx.select_windows():
            problems.append(f"没找到/没选中目标窗口（标题含「{ctx.window.title_substr}」）"
                            "，请先打开游戏并在「选择窗口」里选好")

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

        multi = ctx.cfg.get("targets", {}).get("multi", False)
        switch_delay = ctx.cfg.get("targets", {}).get("switch_delay_sec", 0.15)
        tick = loop.get("tick_interval_sec", 0.5)

        time_limit = loop.get("time_limit_min", 0) or 0
        start_ts = time.time()
        deadline = start_ts + time_limit * 60 if time_limit > 0 else None

        if not self._is_admin():
            ctx.log("⚠ 当前非管理员权限：游戏在前台时鼠标/键盘注入可能被 UIPI 拦截。"
                    "请用『以管理员身份运行』重开。", level="warn")

        contexts = self._resolve_contexts(ctx, multi)
        if not contexts:
            ctx.log("没找到/没选中目标窗口，已停止。", level="error")
            return

        if dry_run:
            ctx.log("演练模式：只对各号当前屏幕做『各标志识别自检』，不发快捷键/不点关键操作。"
                    + ("多号逐个扫描。" if multi else "")
                    + "手动打开对应界面，看日志能否认出 运镖入口/参加按钮/押送普通镖银/战斗。", level="warn")
            self._dry_run_selfcheck(ctx, contexts, multi, regions, threshold, switch_delay, deadline)
            return

        ctx.log(f"★ 实战模式：{('多开轮转 ' + str(len(contexts)) + ' 个号' if multi else '单号')}，"
                f"每号循环押满 {self.max_escorts} 趟，号与号之间逐步轮转 ★", level="warn")
        if time_limit > 0:
            ctx.log(f"时间上限 {time_limit} 分钟（到点自停）。每号主终止条件是对话框不再弹出（次数用完）。")

        records = [self._new_record(c) for c in contexts]
        while not ctx.should_stop():
            if deadline and time.time() >= deadline:
                ctx.log(f"已达时间上限 {time_limit} 分钟，停止。")
                break
            if all(r["done"] for r in records):
                ctx.log("所有号都已押满次数/结束。")
                break

            active = [r for r in records if not r["done"]]
            for rec in records:
                if ctx.should_stop():
                    break
                if rec["done"]:
                    continue
                wctx = rec["ctx"]
                if wctx.window.rect() is None:
                    if not rec["dead_logged"]:
                        wctx.log("目标窗口不见了，跳过该号（其余号继续）。", level="warn")
                        rec["dead_logged"] = True
                    continue
                rec["dead_logged"] = False
                # 操作某号前先切前台，确保点击/快捷键落在这个号身上（多开必须；单开也无害）
                if multi:
                    wctx.window.activate()
                    if wctx.should_stop():
                        break
                # 推进这个号一小步
                self._step_once(wctx, rec, loop, regions, threshold)
                # 号与号之间留个小间隔，别太机械
                if multi and len(active) > 1:
                    self._interruptible_sleep(ctx, self._jitter(switch_delay, ctx))

            # 一整轮（所有号各推进一步）之间留间隔（带抖动）
            self._interruptible_sleep(ctx, self._jitter(tick, ctx))

        total = sum(r["escorts"] for r in records)
        ctx.log(f"已停止。共完成 {total} 趟运镖，用时 {(time.time() - start_ts) / 60:.1f} 分钟。")

    # ------------------------------------------------------------------
    # 多开轮转：上下文与每号状态记录
    # ------------------------------------------------------------------
    def _resolve_contexts(self, ctx, multi):
        """按选择把目标窗口包成「要轮转的上下文」列表。
        单开→复用主 ctx 并绑到选中的那个窗口；多开→每号一个子上下文(带「号N」标签，日志自动加前缀)。"""
        wins = ctx.select_windows()
        if not wins:
            return []
        if multi:
            return [ctx.make_child(w, f"号{i + 1}") for i, w in enumerate(wins)]
        ctx.window = wins[0]
        return [ctx]

    @staticmethod
    def _new_record(wctx):
        """每个号一份独立状态。轮转时按 state 各推进一步，互不干扰。"""
        return {"ctx": wctx, "state": S_OPEN_ACTIVITY, "t_state": 0.0,
                "escorts": 0,            # 本号已开始/进行中的趟数（首趟点押送银即 1）
                "seen_ongoing": False,   # 本趟是否出现过「运镖中」标志（出现过才允许靠它消失判结束）
                "gone_since": None,      # 「运镖中」标志消失起点
                "t_trip": 0.0,          # 最近一次「明确在运镖/战斗/起步」的时间，用于单趟超时兜底
                "t_diag": 0.0, "scrolls": 0, "recover": 0,
                "done": False, "dead_logged": False}

    @staticmethod
    def _goto(rec, state):
        rec["state"] = state
        rec["t_state"] = time.time()

    @staticmethod
    def _state_elapsed(rec):
        return time.time() - rec["t_state"]

    # ------------------------------------------------------------------
    # 单步推进：按这个号的 state 做【一小步】非阻塞动作，然后立刻返回（好轮转到下一个号）
    # ------------------------------------------------------------------
    def _step_once(self, ctx, rec, loop, regions, threshold):
        st = rec["state"]
        if st == S_OPEN_ACTIVITY:
            self._do_open_activity(ctx, rec, loop, regions, threshold)
        elif st == S_FIND_CARD:
            self._do_find_card(ctx, rec, loop, regions, threshold)
        elif st == S_DIALOG:
            self._do_dialog(ctx, rec, loop, regions, threshold)
        elif st == S_CONFIRM:
            self._do_confirm(ctx, rec, loop, regions, threshold)
        elif st == S_ESCORTING:
            self._do_escorting(ctx, rec, loop, regions, threshold)

    # ---- 开活动 ----
    def _do_open_activity(self, ctx, rec, loop, regions, threshold):
        self._focus(ctx)
        if not ctx.send_hotkey("open_activity"):
            ctx.log("打不开活动界面（open_activity 快捷键未配置），放弃该号。", level="error")
            rec["done"] = True
            return
        ctx.log("已打开活动，滚轮翻找「运镖」…")
        self._interruptible_sleep(ctx, self._jitter(0.6, ctx))
        rec["scrolls"] = 0
        self._goto(rec, S_FIND_CARD)

    # ---- 找「运镖」条目 → 点「参加」（每访问一次：找不到就滚一屏，超 scroll_max_tries 屏则恢复）----
    def _do_find_card(self, ctx, rec, loop, regions, threshold):
        list_region = regions.get("activity_list")
        rect = (ctx.window.region_to_screen_rect(list_region)
                if list_region else ctx.window.rect())
        if rect is None:
            return
        scene = win_mod.grab(rect)
        hit = vision.match(scene, self.flags.get("escort_entry"), threshold) if scene is not None else None
        if hit is not None:
            cx, cy, score = hit
            entry_xy = (rect[0] + cx, rect[1] + cy)
            join = self._find_join_on_row(ctx, list_region, entry_xy, threshold, loop)
            if join is not None:
                ctx.mouse.click(join[0], join[1])
                ctx.log(f"找到「运镖」（{score:.3f}）→ 点「参加」（{join[2]:.3f}），等对话框。", level="hit")
                self._goto(rec, S_DIALOG)
                return
            ctx.log("认出「运镖」但没找到右侧「参加」（检查 escort_join 模板/阈值）。", level="warn")
        else:
            # 没找到：在列表中心向下滚一屏（settle 交给轮转间隔），下次再找
            cx_c, cy_c = rect[0] + rect[2] // 2, rect[1] + rect[3] // 2
            ctx.mouse.scroll(loop.get("scroll_step", -3), cx_c, cy_c)
        rec["scrolls"] += 1
        if rec["scrolls"] > max(1, loop.get("scroll_max_tries", 8)):
            ctx.log("活动列表里翻找「运镖」入口多次未果。", level="warn")
            self._recover_window(ctx, rec, loop, regions)

    # ---- 等首个「押送普通镖银」对话框 → 点它，开始第 1 趟 ----
    def _do_dialog(self, ctx, rec, loop, regions, threshold):
        scene_rect = self._scene_rect(ctx, regions)
        cur = win_mod.grab(scene_rect)
        hit = self._match_scene(cur, scene_rect, "escort_silver", threshold)
        if hit is not None:
            ctx.mouse.click(hit[0], hit[1])
            rec["escorts"] += 1
            ctx.log(f"点「押送普通镖银」（{hit[2]:.3f}）开始第 {rec['escorts']} 趟，等确认框…", level="hit")
            self._interruptible_sleep(ctx, self._jitter(0.4, ctx))
            self._goto(rec, S_CONFIRM)
            return
        if self._state_elapsed(rec) > loop.get("dialog_timeout_sec", 60):
            ctx.log("等「押送普通镖银」对话框超时。", level="warn")
            self._recover_window(ctx, rec, loop, regions)

    # ---- 点完押送后等「确认」按钮（超时容错继续）----
    def _do_confirm(self, ctx, rec, loop, regions, threshold):
        scene_rect = self._scene_rect(ctx, regions)
        cur = win_mod.grab(scene_rect)
        hit = self._match_scene(cur, scene_rect, "escort_confirm", threshold)
        if hit is not None:
            ctx.mouse.click(hit[0], hit[1])
            ctx.log(f"点「确认」（{hit[2]:.3f}），开始监控本趟运镖。", level="hit")
            self._interruptible_sleep(ctx, self._jitter(0.4, ctx))
            self._start_trip_monitor(rec)
            return
        if self._state_elapsed(rec) > loop.get("confirm_timeout_sec", 10):
            ctx.log("等运镖「确认」按钮超时（可能本次无需确认），开始监控本趟。", level="warn")
            self._start_trip_monitor(rec)

    def _start_trip_monitor(self, rec):
        """进入运镖监控前，重置该趟的「运镖中/结束」计时。"""
        rec["seen_ongoing"] = False
        rec["gone_since"] = None
        rec["t_trip"] = time.time()
        self._goto(rec, S_ESCORTING)

    # ---- 运镖监控（每访问一次扫一遍，非阻塞）----
    def _do_escorting(self, ctx, rec, loop, regions, threshold):
        """监控运镖。靠「运镖中」标志(escort_ongoing)判断在不在运镖途中——
          · 对话框「押送普通镖银」再次弹出 → 这趟跑完、还有次数，续点开始下一趟（次数+1）；
          · 只要「运镖中」标志在 或 在战斗中 → 绝不判结束（刷新计时）；
          · 「运镖中」标志这趟出现过、现在消失了、且没有新对话框、且已押满设定趟数
            → 持续 done_idle_sec 秒后判定本号运镖全部结束。
        既不会在「点完确认刚开始、人物还没动」时误停，也不会在两趟之间的空档误停。"""
        done_grace = loop.get("done_idle_sec", 6.0)
        per_trip_timeout = loop.get("escort_timeout_sec", 600)
        scene_rect = self._scene_rect(ctx, regions)
        cur = win_mod.grab(scene_rect)

        # 对话框又弹出来了 → 这一趟跑完、还有次数，续点开始下一趟
        hit = self._match_scene(cur, scene_rect, "escort_silver", threshold)
        if hit is not None:
            if rec["escorts"] >= self.max_escorts:
                # 已押满设定趟数仍弹框（异常/识别抖动）→ 不再续点，直接收尾
                ctx.log(f"已完成设定的 {self.max_escorts} 趟，收尾该号（不再续押）。", level="hit")
                self._finish_escort(ctx, rec)
                return
            ctx.mouse.click(hit[0], hit[1])
            rec["escorts"] += 1
            ctx.log(f"上一趟结束，对话框再次弹出 → 点「押送普通镖银」开始第 {rec['escorts']} 趟"
                    f"（{hit[2]:.3f}），等确认框…", level="hit")
            self._interruptible_sleep(ctx, self._jitter(0.4, ctx))
            self._goto(rec, S_CONFIRM)
            return

        ongoing = self._present(cur, "escort_ongoing", threshold)
        in_battle = self._present(cur, "escort_battle", threshold)

        if ongoing or in_battle:
            # 明确在运镖途中/战斗中 → 绝不停，刷新计时
            if ongoing:
                rec["seen_ongoing"] = True
            rec["gone_since"] = None
            rec["t_trip"] = time.time()
        else:
            # 既没在运镖也没在战斗、也没对话框
            if rec["seen_ongoing"] and rec["escorts"] >= self.max_escorts:
                # 这趟运镖中标志出现过又消失了 + 已是最后一趟 + 没有新对话框 → 准备收尾
                if rec["gone_since"] is None:
                    rec["gone_since"] = time.time()
                elif time.time() - rec["gone_since"] >= done_grace:
                    ctx.log("「运镖中」标志已消失且无更多对话框 → 本号运镖全部结束。", level="hit")
                    self._finish_escort(ctx, rec)
                    return
            # 否则：要么还没开始（运镖中标志还没出现），要么还有次数要等下一个对话框 → 继续等

        self._diag_escorting(ctx, rec, ongoing, in_battle, done_grace)
        if time.time() - rec["t_trip"] > per_trip_timeout:
            ctx.log("长时间既无『运镖中』也无对话框，按本号结束处理。", level="warn")
            self._finish_escort(ctx, rec)

    def _diag_escorting(self, ctx, rec, ongoing, in_battle, done_grace):
        """每 ~5s 打印一次该号运镖状态诊断。"""
        now = time.time()
        if now - rec["t_diag"] < 5.0:
            return
        if ongoing:
            st = "运镖中"
        elif in_battle:
            st = "战斗中"
        elif not rec["seen_ongoing"]:
            st = "等运镖开始/过场"
        else:
            held = (now - rec["gone_since"]) if rec["gone_since"] else 0.0
            st = f"运镖中标志已消失 {held:.1f}/{done_grace}s（等满即判结束）"
        ctx.log(f"监控…{st}（已完成 {rec['escorts']}/{self.max_escorts} 趟）")
        rec["t_diag"] = now

    def _finish_escort(self, ctx, rec):
        """本号押镖收尾：标记该号 done（押满次数/对话框不再弹）。"""
        rec["done"] = True
        ctx.log(f"该号已完成 {rec['escorts']} 趟运镖。")

    # ---- 卡死兜底恢复 ----
    def _recover_window(self, ctx, rec, loop, regions):
        """某号卡死兜底：截图存证 + 计数；超上限放弃该号。
        仅在【还没开始任何一趟运镖】时聚焦+Esc 清弹窗后回开活动重试；
        已经在押镖途中卡死则直接按已完成处理、放弃该号——避免活动已参加、
        按钮变「已参加」后重开活动连环卡死。"""
        rec["recover"] += 1
        ctx.log(f"卡住（第 {rec['recover']}/{loop.get('max_stuck_recover', 3)} 次），尝试恢复…", level="warn")
        self._save_capture(self._grab_scene(ctx, regions), f"stuck_{rec['state']}")
        if rec["recover"] >= loop.get("max_stuck_recover", 3):
            ctx.log("多次卡死仍无进展，放弃该号。", level="error")
            rec["done"] = True
            return
        if rec["escorts"] == 0:
            self._focus(ctx)
            ctx.log("卡死恢复：按一次 Esc 关掉可能的异常弹窗…")
            if ctx.send_hotkey("close_panel"):
                self._interruptible_sleep(ctx, self._jitter(0.25, ctx))
            self._goto(rec, S_OPEN_ACTIVITY)
        else:
            ctx.log("已在押镖途中卡死，按已完成处理、放弃该号。", level="warn")
            rec["done"] = True

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------
    def _focus(self, ctx):
        """把游戏窗口切到前台——键盘快捷键(SendInput)只发给有焦点的窗口，发键前必须先激活。"""
        try:
            ctx.window.activate()
        except Exception:
            pass

    def _find_join_on_row(self, ctx, list_region, entry_screen_xy, threshold, loop):
        """在「运镖」条目所在【那张卡片】的右侧条带里匹配「参加」按钮(escort_join)。
        命中返回 (screen_x, screen_y, score)，否则 None。
        按行+只取条目右侧、且限制在条目所属卡片列内，能抗滚动、抗「一排多张卡片」时
        扫进右邻卡片点到它的「参加」按钮（活动列表默认两张卡片一排）。"""
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
        # 活动列表是「每排多张卡片」(默认两张一排)：参加按钮只在【条目所属那张卡片】内。
        # 若一路扫到列表右缘(x1=sw)，右邻卡片的「参加」按钮会被一并扫进来、甚至胜出，
        # 导致点到右边卡片的参加。故把列表按列等分，定位条目所在列，x1 收到该列右边界。
        cols = max(1, int(loop.get("activity_columns", 2)))
        col_w = sw / cols
        col_idx = min(cols - 1, max(0, int(ex_local // col_w)))
        col_right = int(round((col_idx + 1) * col_w))
        y0 = max(0, ey_local - band // 2)
        y1 = min(sh, ey_local + band // 2)
        x0 = max(0, ex_local)
        x1 = min(sw, col_right)
        if y1 - y0 < 1 or x1 - x0 < 1:
            return None
        crop = scene[y0:y1, x0:x1]
        m = vision.match(crop, join_tpl, threshold)
        if m is None:
            return None
        cx, cy, score = m
        return (rx + x0 + cx, ry + y0 + cy, score)

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

    def _match_scene(self, cur, scene_rect, flag_key, threshold):
        """在整张 scene 里匹配 flag_key，命中返回屏幕绝对 (x,y,score)，否则 None。"""
        tpl = self.flags.get(flag_key)
        if cur is None or tpl is None or scene_rect is None:
            return None
        m = vision.match(cur, tpl, threshold)
        if m is None:
            return None
        return (scene_rect[0] + m[0], scene_rect[1] + m[1], m[2])

    def _dry_run_selfcheck(self, ctx, contexts, multi, regions, threshold, switch_delay, deadline):
        """演练：周期性对【每个号】当前屏幕识别各标志，报告命中，便于用户验证模板/阈值。"""
        keys = [("escort_entry", "运镖入口"), ("escort_join", "参加按钮"),
                ("escort_silver", "押送普通镖银"), ("escort_confirm", "确认"),
                ("escort_ongoing", "运镖中"), ("escort_battle", "战斗")]
        while not ctx.should_stop():
            if deadline and time.time() >= deadline:
                ctx.log("演练时间上限到，停止。")
                break
            for wctx in contexts:
                if ctx.should_stop():
                    break
                if wctx.window.rect() is None:
                    continue
                if multi:
                    wctx.window.activate()
                scene = self._grab_scene(wctx, regions)
                found = []
                for key, label in keys:
                    tpl = self.flags.get(key)
                    if tpl is None:
                        continue
                    hit = vision.match(scene, tpl, threshold) if scene is not None else None
                    if hit is not None:
                        found.append(f"{label}({hit[2]:.2f})")
                if found:
                    wctx.log("识别到：" + "、".join(found), level="hit")
                else:
                    wctx.log("当前屏幕未识别到任何已标定标志（请打开对应界面再看）。")
                if multi and len(contexts) > 1:
                    self._interruptible_sleep(ctx, self._jitter(switch_delay, ctx))
            self._interruptible_sleep(ctx, self._jitter(1.5, ctx))
