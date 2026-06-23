# -*- coding: utf-8 -*-
"""
秘境降妖任务（游戏自带自动战斗，脚本只做导航 + 监控 + 关键点击）。

用户描述的真实流程（每个号一轮）：

  开「活动」(快捷键 Alt+C) → 在活动列表里找到那张活动卡片 → 点该行【右侧的「参加」按钮】
  → 弹出对话框 → 点「秘境降妖」选项
  → (可选) 若出现「选择副本」界面 → 点【屏幕左下角】的「进入」按钮
     ⚠ 几个副本的「进入」按钮长得一模一样，只能靠【位置】区分——故只在 scene 的左下角比例框里匹配。
     「确定」只在【选了副本】后才弹（不选副本没有确认键）。
  → 点「确定」(选了副本才有)→ 点「继续挑战」→ 点「挑战」→ 开始自动战斗
  → 难度关卡不再自动：实时盯「进入战斗」按钮，一出现就点它续战。
  → 直到【判定失败】(要先点掉失败结算，「离开」才点得到) 或 【时长超时】 → 点「离开」退出秘境，本轮结束。
    （秘境是打到失败/超时为止，没有正常通关弹离开；故不设「一看到离开就点」的旁路，避免误判提前退出。）

★ 多开轮转（用户要求「多号之间每一步都轮转」）：
  每个号各持一份状态(record)，主循环对每个号【各推进一小步】(非阻塞)，号与号之间逐步轮转——
  这样 号1 在自动战斗时，可以去把 号2/号3 也开起来，再回头照看 号1。鼠标光标全局唯一，故单线程轮转、
  操作某号前先 activate() 切前台。单开=列表只有一个号、同走这套轮转。

导航靠 ctx.send_hotkey(动作名)（键位在 config.hotkeys，用户可按游戏「系统设置-快捷键」核对）。

停止：①所有号都跑满 max_runs 轮 ②时间上限分钟(安全网) ③手动停止/鼠标甩左上角 failsafe。
安全默认 dry_run=true：不发快捷键/不点关键操作，只对各号当前屏幕做识别自检，便于先验证模板。
"""

import time

from ..core import vision
from ..core import window as win_mod
from .base import Task, register

# 每个号的状态机状态（非阻塞：每访问一次只推进一步）
S_OPEN_ACTIVITY = "OPEN_ACTIVITY"   # 发活动快捷键
S_FIND_CARD = "FIND_CARD"           # 活动列表里滚轮找卡片 → 点「参加」
S_SELECT = "SELECT"                 # 等对话框 → 点「秘境降妖」
S_DUNGEON = "DUNGEON"               # (可选)等「选择副本」→ 点左下角「进入」；超时无则跳过
S_CONFIRM = "CONFIRM"               # (选了副本才有)点「确定」
S_CONTINUE = "CONTINUE"             # 点「继续挑战」
S_CHALLENGE = "CHALLENGE"           # 点「挑战」→ 进战斗监控
S_BATTLE = "BATTLE"                 # 自动战斗中：盯「进入战斗」续战 + 判失败/超时
S_LEAVE = "LEAVE"                   # 失败/超时后：点「离开」收尾本轮

# 模板键（用 sr_ 前缀，避免与运镖/宝图同名模板在磁盘互相覆盖——存盘按 templates/tm_<key>.png）。
_FLAG_KEYS = ["sr_entry", "sr_join", "sr_select", "sr_dungeon_enter", "sr_confirm",
              "sr_continue", "sr_challenge", "sr_enter_battle", "sr_leave",
              "sr_fail", "sr_battle"]
# 必备：缺失则 preflight 阻断（其余为可选，缺失仅提示）。
_REQUIRED_FLAGS = ["sr_entry", "sr_join", "sr_select",
                   "sr_continue", "sr_challenge", "sr_enter_battle", "sr_leave"]


@register
class SecretRealmTask(Task):
    name = "secret_realm"
    title = "秘境降妖"
    description = "自动开活动→参加→秘境降妖→选副本/确定/继续挑战/挑战→盯进入战斗续战，失败/超时自动离开（支持多开轮转）"

    CALIBRATION = {
        "regions": [
            ("scene", "主识别区", "留空=整个窗口当识别区(推荐)；对话框/各按钮/失败等标志都在这里找", True),
            ("activity_list", "活动列表区域", "「活动」界面里那片列表，滚轮在此翻找秘境降妖那张卡片"),
        ],
        "templates": [
            ("sr_entry", "活动卡片入口", "活动列表里要点「参加」的那张卡片，框图标+文字、要独特"),
            ("sr_join", "参加按钮", "那张卡片右侧的「参加」按钮，框按钮本身、要独特"),
            ("sr_select", "「秘境降妖」选项", "点参加后弹出的对话框里那个「秘境降妖」选项/按钮"),
            ("sr_dungeon_enter", "「进入」按钮(选副本，可选)", "若有「选择副本」界面，框左下角那个「进入」按钮。"
                                                       "几个进入长得一样，运行时只在左下角比例框里找；没有选副本环节可不标"),
            ("sr_confirm", "「确定」按钮(选副本后才有，可选)", "选完副本后弹出的「确定」按钮；不选副本就没有这一步，可不标"),
            ("sr_continue", "「继续挑战」按钮", "确定后出现的「继续挑战」按钮"),
            ("sr_challenge", "「挑战」按钮", "「继续挑战」后出现的「挑战」按钮，点它开始自动战斗"),
            ("sr_enter_battle", "「进入战斗」按钮", "难度关卡处不再自动、需要手动点的「进入战斗」按钮（监控期一出现就点）"),
            ("sr_leave", "「离开」按钮", "失败/超时后点的「离开」按钮，点它退出秘境"),
            ("sr_fail", "「失败」标志(可选)", "战斗失败时屏幕上的「失败」字样/弹窗，判定该退出（先点它结算，离开才点得到）"),
            ("sr_battle", "战斗界面标志(可选)", "战斗独有的画面元素，仅用于日志诊断"),
        ],
        "watchlist": False,
    }

    # ------------------------------------------------------------------
    def preflight(self, ctx):
        tc = ctx.task_cfg(self.name)
        problems = []
        regions = tc.get("regions", {})
        templates = tc.get("templates", {})

        # scene 留空=整窗检测，不强制标定；活动列表区仍需标（滚轮翻找卡片）
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
        for tk, label in [("sr_dungeon_enter", "选副本-进入"), ("sr_confirm", "确定(选副本后才有)"),
                          ("sr_fail", "失败"), ("sr_battle", "战斗")]:
            if not templates.get(tk) or vision.load_template(templates.get(tk)) is None:
                ctx.log(f"提示：可选模板『{tk}』({label})未标定，将降级处理（可靠性略降）。", level="warn")

        return (len(problems) == 0), problems

    # ------------------------------------------------------------------
    def run(self, ctx):
        tc = ctx.task_cfg(self.name)
        loop = tc["loop"]
        regions = tc["regions"]
        dry_run = tc.get("dry_run", True)
        threshold = loop["match_threshold"]
        self.flags = self._load_flags(tc)
        self.max_runs = max(1, int(loop.get("max_runs", 1)))

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
                    + ("多号逐个扫描。" if multi else ""), level="warn")
            self._dry_run_selfcheck(ctx, contexts, multi, regions, threshold, switch_delay, deadline)
            return

        ctx.log(f"★ 实战模式：{('多开轮转 ' + str(len(contexts)) + ' 个号' if multi else '单号')}，"
                f"每号连跑 {self.max_runs} 轮，号与号之间逐步轮转 ★", level="warn")
        if time_limit > 0:
            ctx.log(f"时间上限 {time_limit} 分钟（到点自停）。每号每轮终止条件是 失败 或 时长超时。")

        records = [self._new_record(c) for c in contexts]
        while not ctx.should_stop():
            if deadline and time.time() >= deadline:
                ctx.log(f"已达时间上限 {time_limit} 分钟，停止。")
                break
            if all(r["done"] for r in records):
                ctx.log("所有号都已跑满轮数/结束。")
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

        total = sum(r["runs"] for r in records)
        ctx.log(f"已停止。共完成 {total} 轮秘境，用时 {(time.time() - start_ts) / 60:.1f} 分钟。")

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
                "t_battle": 0.0, "t_diag": 0.0, "scrolls": 0,
                "picked_dungeon": False, "entered_battle": False,
                "runs": 0, "recover": 0, "done": False, "dead_logged": False}

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
        elif st == S_SELECT:
            self._do_select(ctx, rec, loop, regions, threshold)
        elif st == S_DUNGEON:
            self._do_dungeon(ctx, rec, loop, regions, threshold)
        elif st == S_CONFIRM:
            self._do_confirm(ctx, rec, loop, regions, threshold)
        elif st == S_CONTINUE:
            self._do_continue(ctx, rec, loop, regions, threshold)
        elif st == S_CHALLENGE:
            self._do_challenge(ctx, rec, loop, regions, threshold)
        elif st == S_BATTLE:
            self._do_battle(ctx, rec, loop, regions, threshold)
        elif st == S_LEAVE:
            self._do_leave(ctx, rec, loop, regions, threshold)

    # ---- 开活动 ----
    def _do_open_activity(self, ctx, rec, loop, regions, threshold):
        self._focus(ctx)
        if not ctx.send_hotkey("open_activity"):
            ctx.log("打不开活动界面（open_activity 快捷键未配置），放弃该号。", level="error")
            rec["done"] = True
            return
        ctx.log("已打开活动，翻找秘境降妖卡片…")
        self._interruptible_sleep(ctx, self._jitter(0.6, ctx))
        rec["scrolls"] = 0
        self._goto(rec, S_FIND_CARD)

    # ---- 找卡片 → 点「参加」（每访问一次：找不到就滚一屏，超 scroll_max_tries 屏则恢复）----
    def _do_find_card(self, ctx, rec, loop, regions, threshold):
        list_region = regions.get("activity_list")
        rect = (ctx.window.region_to_screen_rect(list_region)
                if list_region else ctx.window.rect())
        if rect is None:
            return
        scene = win_mod.grab(rect)
        hit = vision.match(scene, self.flags.get("sr_entry"), threshold) if scene is not None else None
        if hit is not None:
            cx, cy, score = hit
            entry_xy = (rect[0] + cx, rect[1] + cy)
            join = self._find_join_on_row(ctx, list_region, entry_xy, threshold, loop)
            if join is not None:
                ctx.mouse.click(join[0], join[1])
                ctx.log(f"找到卡片（{score:.3f}）→ 点「参加」（{join[2]:.3f}），等对话框。", level="hit")
                self._goto(rec, S_SELECT)
                return
            ctx.log("认出卡片但没找到右侧「参加」（检查 sr_join 模板/阈值）。", level="warn")
        else:
            # 没找到：在列表中心向下滚一屏（settle 交给轮转间隔），下次再找
            cx_c, cy_c = rect[0] + rect[2] // 2, rect[1] + rect[3] // 2
            ctx.mouse.scroll(loop.get("scroll_step", -3), cx_c, cy_c)
        rec["scrolls"] += 1
        if rec["scrolls"] > max(1, loop.get("scroll_max_tries", 8)):
            ctx.log("翻找秘境降妖卡片多次未果。", level="warn")
            self._recover_window(ctx, rec, loop, regions)

    # ---- 等对话框 → 点「秘境降妖」（必备，超时则恢复）----
    def _do_select(self, ctx, rec, loop, regions, threshold):
        if self._try_click(ctx, rec, regions, threshold, "sr_select", "秘境降妖", S_DUNGEON):
            return
        if self._state_elapsed(rec) > loop.get("dialog_timeout_sec", 30):
            ctx.log("等「秘境降妖」对话框超时。", level="warn")
            self._recover_window(ctx, rec, loop, regions)

    # ---- (可选)选副本：点左下角「进入」；短超时内没出现就跳过 ----
    def _do_dungeon(self, ctx, rec, loop, regions, threshold):
        tpl = self.flags.get("sr_dungeon_enter")
        if tpl is None:
            rec["picked_dungeon"] = False
            self._goto(rec, S_CONTINUE)
            return
        box = loop.get("dungeon_enter_box", [0.0, 0.5, 0.55, 1.0])
        hit = self._match_subregion(ctx, regions, tpl, threshold,
                                    (box[0], box[2]), (box[1], box[3]))
        if hit is not None:
            ctx.mouse.click(hit[0], hit[1])
            ctx.log(f"选择副本：点左下角「进入」（{hit[2]:.3f}）。", level="hit")
            rec["picked_dungeon"] = True
            self._interruptible_sleep(ctx, self._jitter(0.5, ctx))
            self._goto(rec, S_CONFIRM)
            return
        if self._state_elapsed(rec) > loop.get("dungeon_select_wait_sec", 6):
            ctx.log("未出现「选择副本-进入」，跳过（本次无需选副本）。")
            rec["picked_dungeon"] = False
            self._goto(rec, S_CONTINUE)

    # ---- 「确定」：只在选了副本后才会出现；点不到就超时跳过（容错）----
    def _do_confirm(self, ctx, rec, loop, regions, threshold):
        if not rec["picked_dungeon"]:
            # 没选副本就没有确认键，直接进继续挑战（理论上不会进到这分支，双保险）
            self._goto(rec, S_CONTINUE)
            return
        if self._try_click(ctx, rec, regions, threshold, "sr_confirm", "确定", S_CONTINUE):
            return
        if self._state_elapsed(rec) > loop.get("step_timeout_sec", 20):
            ctx.log("等「确定」超时，继续。", level="warn")
            self._goto(rec, S_CONTINUE)

    # ---- 「继续挑战」（容错：超时则进挑战）----
    def _do_continue(self, ctx, rec, loop, regions, threshold):
        if self._try_click(ctx, rec, regions, threshold, "sr_continue", "继续挑战", S_CHALLENGE):
            return
        if self._state_elapsed(rec) > loop.get("step_timeout_sec", 20):
            ctx.log("等「继续挑战」超时，继续。", level="warn")
            self._goto(rec, S_CHALLENGE)

    # ---- 「挑战」（容错：超时也进战斗监控，靠 进入战斗/失败 兜底）----
    def _do_challenge(self, ctx, rec, loop, regions, threshold):
        scene_rect = self._scene_rect(ctx, regions)
        cur = win_mod.grab(scene_rect)
        hit = self._match_scene(cur, scene_rect, "sr_challenge", threshold)
        if hit is not None:
            ctx.mouse.click(hit[0], hit[1])
            ctx.log(f"点「挑战」（{hit[2]:.3f}）→ 进入秘境，开始自动战斗监控。", level="hit")
            self._enter_battle(rec)
            return
        if self._state_elapsed(rec) > loop.get("step_timeout_sec", 20):
            ctx.log("没点到「挑战」，仍进入战斗监控（靠 进入战斗/失败 兜底）。", level="warn")
            self._enter_battle(rec)

    def _enter_battle(self, rec):
        rec["entered_battle"] = True
        rec["t_battle"] = time.time()
        self._goto(rec, S_BATTLE)

    # ---- 战斗监控：每访问一次扫一遍——判失败/超时去离开；有「进入战斗」就续点 ----
    def _do_battle(self, ctx, rec, loop, regions, threshold):
        overall = loop.get("battle_timeout_sec", 1800)
        if time.time() - rec["t_battle"] > overall:
            ctx.log(f"已超过 {overall:.0f}s 仍未结束 → 判定超时，去点「离开」。", level="warn")
            self._goto(rec, S_LEAVE)
            return

        scene_rect = self._scene_rect(ctx, regions)
        cur = win_mod.grab(scene_rect)

        # 判定失败：先点掉「失败」结算，「离开」按钮才点得到（直接点离开会点空）
        fail = self._match_scene(cur, scene_rect, "sr_fail", threshold)
        if fail is not None:
            ctx.log(f"判定失败（{fail[2]:.3f}）→ 先点掉「失败」结算，转去点「离开」。", level="hit")
            ctx.mouse.click(fail[0], fail[1])
            self._interruptible_sleep(ctx, self._jitter(0.5, ctx))
            self._goto(rec, S_LEAVE)
            return

        # 难度关卡：出现「进入战斗」就点它续战
        eb = self._match_scene(cur, scene_rect, "sr_enter_battle", threshold)
        if eb is not None:
            ctx.mouse.click(eb[0], eb[1])
            ctx.log(f"难度关卡：点「进入战斗」（{eb[2]:.3f}）继续。", level="hit")
            self._interruptible_sleep(ctx, self._jitter(0.6, ctx))
            return

        # 否则：自动战斗中/过场——节流打印诊断
        now = time.time()
        if now - rec["t_diag"] >= 10.0:
            st = "战斗中" if self._present(cur, "sr_battle", threshold) else "自动推进/过场"
            ctx.log(f"监控…{st}（已 {now - rec['t_battle']:.0f}/{overall:.0f}s）")
            rec["t_diag"] = now

    # ---- 失败/超时后：点「离开」收尾本轮（超时容错按结束处理）----
    def _do_leave(self, ctx, rec, loop, regions, threshold):
        scene_rect = self._scene_rect(ctx, regions)
        cur = win_mod.grab(scene_rect)
        hit = self._match_scene(cur, scene_rect, "sr_leave", threshold)
        if hit is not None:
            ctx.mouse.click(hit[0], hit[1])
            ctx.log(f"点「离开」（{hit[2]:.3f}）退出秘境。", level="hit")
            self._interruptible_sleep(ctx, self._jitter(0.4, ctx))
            self._finish_run(ctx, rec)
            return
        if self._state_elapsed(rec) > loop.get("step_timeout_sec", 20):
            ctx.log("等「离开」按钮超时，按本轮结束处理。", level="warn")
            self._finish_run(ctx, rec)

    def _finish_run(self, ctx, rec):
        """一轮秘境收尾：计数 + 决定该号结束还是再跑一轮。"""
        rec["runs"] += 1
        ctx.log(f"完成第 {rec['runs']}/{self.max_runs} 轮秘境。", level="hit")
        self._interruptible_sleep(ctx, self._jitter(1.0, ctx))
        if rec["runs"] >= self.max_runs:
            rec["done"] = True
            ctx.log("该号已跑满设定轮数。")
            return
        # 新一轮：重置该轮状态，回开活动
        rec["picked_dungeon"] = False
        rec["entered_battle"] = False
        rec["recover"] = 0
        ctx.log("还有剩余轮数，重新开活动再来一轮…")
        self._goto(rec, S_OPEN_ACTIVITY)

    def _recover_window(self, ctx, rec, loop, regions):
        """某号卡死兜底：截图存证 + 计数；超上限放弃该号。
        仅在【尚未进入战斗】时回开活动重试；已进秘境后卡死直接放弃该号（避免在秘境内反复重开活动）。"""
        rec["recover"] += 1
        ctx.log(f"卡住（第 {rec['recover']}/{loop.get('max_stuck_recover', 3)} 次），尝试恢复…", level="warn")
        self._save_capture(self._grab_scene(ctx, regions), f"stuck_{rec['state']}")
        if rec["recover"] >= loop.get("max_stuck_recover", 3):
            ctx.log("多次卡死仍无进展，放弃该号。", level="error")
            rec["done"] = True
            return
        if not rec["entered_battle"]:
            self._focus(ctx)
            if ctx.send_hotkey("close_panel"):
                self._interruptible_sleep(ctx, self._jitter(0.25, ctx))
            self._goto(rec, S_OPEN_ACTIVITY)
        else:
            ctx.log("已在秘境战斗中卡死，放弃该号。", level="warn")
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

    def _try_click(self, ctx, rec, regions, threshold, flag_key, label, next_state):
        """在 scene 里找某按钮，命中就点它并切到 next_state，返回是否点到（非阻塞，一次扫描）。"""
        scene_rect = self._scene_rect(ctx, regions)
        cur = win_mod.grab(scene_rect)
        hit = self._match_scene(cur, scene_rect, flag_key, threshold)
        if hit is not None:
            ctx.mouse.click(hit[0], hit[1])
            ctx.log(f"点「{label}」（{hit[2]:.3f}）。", level="hit")
            self._interruptible_sleep(ctx, self._jitter(0.3, ctx))
            self._goto(rec, next_state)
            return True
        return False

    def _find_join_on_row(self, ctx, list_region, entry_screen_xy, threshold, loop):
        """在卡片所在【那张卡片】的右侧条带里匹配「参加」按钮(sr_join)。命中返回 (x,y,score)，否则 None。
        按行+只取条目右侧、且限制在条目所属卡片列内，抗滚动、抗「一排多张卡片」时扫进右邻卡片（默认两张一排）。"""
        join_tpl = self.flags.get("sr_join")
        entry_tpl = self.flags.get("sr_entry")
        if join_tpl is None:
            ctx.log("找「参加」失败：sr_join 模板未标定。", level="warn")
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
        row_h = entry_tpl.shape[0] if entry_tpl is not None else 40
        band = max(40, int(row_h * 2))
        sh, sw = scene.shape[:2]
        ey_local = int(ey - ry)
        ex_local = int(ex - rx)
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

    def _match_subregion(self, ctx, regions, tpl, threshold, x_frac, y_frac):
        """只在 scene 的比例子区域内匹配 tpl（用于「进入」这类需按位置区分的同款按钮）。
        x_frac/y_frac 为 (起,止) 的 0~1 比例。命中返回屏幕 (x,y,score)，否则 None。"""
        rect = self._scene_rect(ctx, regions)
        if rect is None or tpl is None:
            return None
        scene = win_mod.grab(rect)
        if scene is None:
            return None
        sh, sw = scene.shape[:2]
        x0 = max(0, int(sw * x_frac[0]))
        x1 = min(sw, int(sw * x_frac[1]))
        y0 = max(0, int(sh * y_frac[0]))
        y1 = min(sh, int(sh * y_frac[1]))
        if x1 - x0 < 1 or y1 - y0 < 1:
            return None
        crop = scene[y0:y1, x0:x1]
        m = vision.match(crop, tpl, threshold)
        if m is None:
            return None
        cx, cy, score = m
        return (rect[0] + x0 + cx, rect[1] + y0 + cy, score)

    def _dry_run_selfcheck(self, ctx, contexts, multi, regions, threshold, switch_delay, deadline):
        """演练：周期性对【每个号】当前屏幕识别各标志，报告命中，便于验证模板/阈值。"""
        keys = [("sr_entry", "活动卡片"), ("sr_join", "参加"), ("sr_select", "秘境降妖"),
                ("sr_dungeon_enter", "选副本-进入"), ("sr_confirm", "确定"),
                ("sr_continue", "继续挑战"), ("sr_challenge", "挑战"),
                ("sr_enter_battle", "进入战斗"), ("sr_leave", "离开"),
                ("sr_fail", "失败"), ("sr_battle", "战斗")]
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
