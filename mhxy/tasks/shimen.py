# -*- coding: utf-8 -*-
"""
师门任务（单角色自动完成师门十轮）。

完整流程：
  开活动 → 找师门条目 → 点参加 → 点「去完成」
  → 等待系统跑完十轮（游戏自动寻路+自动战斗）
  → 完成面板弹出 → 点确定 → 领取奖励 → 关闭面板 → 使用奖励

支持多开轮转：每个号独立状态，已完成的号跳过。
停止条件：①所有号完成 ②时间上限 ③手动停止/急停键。
安全默认 dry_run=true：只做模板识别自检。
"""

import time

from ..core import scan
from ..core import vision
from ..core import window as win_mod
from ..core import rotation
from .base import Task, register

# 状态机状态
S_OPEN_ACTIVITY = "OPEN_ACTIVITY"   # 发活动快捷键
S_FIND_CARD = "FIND_CARD"           # 滚轮找师门条目 → 点参加
S_CLICK_GO = "CLICK_GO"             # 点「去完成」
S_WAIT_COMPLETE = "WAIT_COMPLETE"   # 等待十轮完成（监控完成面板）
S_CLICK_CONFIRM = "CLICK_CONFIRM"   # 点击确定按钮
S_CLAIM_REWARD = "CLAIM_REWARD"     # 领取奖励
S_CLOSE_PANEL = "CLOSE_PANEL"       # 关闭完成面板
S_USE_REWARD = "USE_REWARD"         # 使用奖励物品


_REQUIRED_FLAGS = ["shimen_entry", "activity_join", "shimen_go",
                   "shimen_complete_panel", "shimen_confirm", "shimen_close"]

# 状态中文映射（用于报告未完成号）
_STATE_CN = {
    S_OPEN_ACTIVITY: "开活动",
    S_FIND_CARD: "找师门",
    S_CLICK_GO: "点去完成",
    S_WAIT_COMPLETE: "等十轮完成",
    S_CLICK_CONFIRM: "点确定",
    S_CLAIM_REWARD: "领奖",
    S_CLOSE_PANEL: "关面板",
    S_USE_REWARD: "使用奖励",
}


@register
class ShimenTask(Task):
    name = "shimen"
    title = "师门"
    description = "自动完成师门十轮：开活动→参加→去完成→等系统跑完→点确定→领奖→关闭→使用"
    is_dungeon = False
    CHAINS_PER_WINDOW = True
    _FLAG_KEYS = [
        "shimen_entry",
        "activity_join",
        "shimen_go",
        "shimen_complete_panel",
        "shimen_confirm",
        "shimen_close",
        "shimen_claim",
        "reward_use",
    ]

    CALIBRATION = {
        "regions": [
            *Task.BASE_CALIBRATION_REGIONS,
        ],
        "templates": [
            *Task.BASE_CALIBRATION_TEMPLATES,
            ("shimen_entry", "师门入口", "活动列表里「师门」那一条，框图标+文字（左侧），不要框参加按钮"),
            ("shimen_go", "去完成按钮", "接任务后弹出的「去完成」按钮"),
            ("shimen_complete_panel", "完成面板", "十轮完成后弹出的奖励面板（框整个面板或独特部分）"),
            ("shimen_confirm", "确定按钮", "完成面板里的「确定」按钮"),
            ("shimen_close", "关闭按钮", "完成面板右上角的关闭按钮（X 或 关闭）"),
            ("shimen_claim", "领取奖励(可选)", "领取奖励按钮"),
        ],
        "watchlist": False,
    }

    def preflight(self, ctx):
        problems = []
        tc = ctx.task_cfg(self.name)
        targets = ctx.cfg.get("targets", {})
        wins = ctx.select_windows()

        if not wins:
            problems.append("没找到/没选中目标窗口 —— 请先「选择窗口」选好要跑师门的号")

        # 检查快捷键
        if not ctx.hotkeys.get("open_activity"):
            problems.append("打开『活动』缺快捷键（open_activity）")

        regions = tc.get("regions", {})
        templates = tc.get("templates", {})

        for rk, label in [("activity_list", "活动列表区域")]:
            if not regions.get(rk):
                problems.append(f"『{label}』未标定 —— 请在标定向导里框选")

        for tk in _REQUIRED_FLAGS:
            path = templates.get(tk)
            if not path or vision.load_template(path) is None:
                problems.append(f"模板『{tk}』缺失或加载失败 —— 请在标定向导里框选裁图")

        optional = ["shimen_claim", "reward_use"]
        for tk in optional:
            if not templates.get(tk) or vision.load_template(templates.get(tk)) is None:
                ctx.log(f"提示：可选模板『{tk}』未标定，将跳过该步骤。", level="warn")

        return (len(problems) == 0), problems

    # ------------------------------------------------------------------
    def run(self, ctx):
        tc = ctx.task_cfg(self.name)
        loop = tc.get("loop", {})
        regions = tc.get("regions", {})
        dry_run = tc.get("dry_run", True)
        threshold = loop.get("match_threshold", 0.85)
        max_runs = loop.get("max_runs", 10)
        time_limit = loop.get("time_limit_min", 30) or 30

        self.flags = self._load_flags(tc)

        start_ts = time.time()
        deadline = start_ts + time_limit * 60 if time_limit > 0 else None

        # 多开支持
        multi = ctx.cfg.get("targets", {}).get("multi", False)
        switch_delay = ctx.cfg.get("targets", {}).get("switch_delay_sec", 0.15)
        tick = loop.get("tick_interval_sec", 0.5)

        if dry_run:
            ctx.log("演练模式：只做模板识别自检，不执行真实操作。", level="warn")
            self._dry_run_selfcheck(ctx, regions, threshold)
            return

        # 解析多开上下文
        wins = ctx.select_windows()
        if not wins:
            ctx.log("没找到/没选中目标窗口，已停止。", level="error")
            return

        if multi:
            wctxs = [ctx.make_child(w, f"号{i + 1}") for i, w in enumerate(wins)]
        else:
            ctx.window = wins[0]
            wctxs = [ctx]

        ctx.log(f"开始师门任务（目标 {max_runs} 轮，{len(wctxs)} 个号）…")

        # 每号独立状态
        records = []
        for wctx in wctxs:
            rec = {"ctx": wctx, "state": S_OPEN_ACTIVITY, "t_state": time.time(),
                   "runs_done": 0, "done": False, "dead_logged": False}
            records.append(rec)

        # 多开轮转
        rotation.run_rotation(self._make_rotation(
            ctx, records,
            lambda rec: self._step_once(rec["ctx"], rec, loop, regions, threshold, max_runs),
            multi, switch_delay, tick, time_limit))

        # 结束汇总
        total_runs = sum(r["runs_done"] for r in records)
        if all(r["done"] for r in records):
            ctx.log("所有号都已完成师门。")
        ctx.log(f"已停止。共完成 {total_runs} 轮师门，用时 {(time.time() - start_ts) / 60:.1f} 分钟。")

        # 报告未完成的号
        unfinished = [r for r in records if not r["done"]]
        if unfinished:
            desc = "、".join(
                f"{(r['ctx'].label or '该号')}停在「{_STATE_CN.get(r['state'], r['state'])}」"
                f"（已 {self._state_elapsed(r):.0f}s）" for r in unfinished)
            ctx.log("未完成：" + desc + "。", level="warn")

    # ------------------------------------------------------------------
    # 日常一条龙·每窗口独立链：暴露「单窗口一份 record + 单步推进函数」
    # ------------------------------------------------------------------
    def make_chain_driver(self, wctx):
        """给定单窗口上下文，返回 (record, step_fn)。step_fn() 推进该窗口本任务状态机一步。
        与 run() 共用 _step_once，不自跑 rotation、不切前台（由一条龙总轮转统一切）。"""
        tc = wctx.task_cfg(self.name)
        loop = tc.get("loop", {})
        regions = tc.get("regions", {})
        threshold = loop.get("match_threshold", 0.85)
        max_runs = loop.get("max_runs", 10)
        self.flags = self._load_flags(tc)
        rec = {"ctx": wctx, "state": S_OPEN_ACTIVITY, "t_state": time.time(),
               "runs_done": 0, "done": False, "dead_logged": False}
        return rec, (lambda: self._step_once(wctx, rec, loop, regions, threshold, max_runs))

    # ------------------------------------------------------------------
    # 单步推进（供轮转引擎调用）
    # ------------------------------------------------------------------
    def _step_once(self, ctx, rec, loop, regions, threshold, max_runs):
        st = rec["state"]
        if st == S_OPEN_ACTIVITY:
            self._do_open_activity(ctx, rec, loop, regions, threshold)
        elif st == S_FIND_CARD:
            self._do_find_card(ctx, rec, loop, regions, threshold)
        elif st == S_CLICK_GO:
            self._do_click_go(ctx, rec, loop, threshold)
        elif st == S_WAIT_COMPLETE:
            self._do_wait_complete(ctx, rec, loop, threshold, max_runs)
        elif st == S_CLICK_CONFIRM:
            self._do_click_confirm(ctx, rec, threshold)
        elif st == S_CLAIM_REWARD:
            self._do_claim_reward(ctx, rec, threshold)
        elif st == S_CLOSE_PANEL:
            self._do_close_panel(ctx, rec, threshold)
        elif st == S_USE_REWARD:
            self._do_use_reward(ctx, rec, loop, threshold, max_runs)

    # ------------------------------------------------------------------
    # 流程步骤
    # ------------------------------------------------------------------
    def _do_open_activity(self, ctx, rec, loop, regions, threshold):
        """发活动快捷键，检测活动列表是否出现，失败重试"""
        self._focus(ctx)

        max_retries = loop.get("activity_open_retries", 3)
        retry_delay = loop.get("activity_open_delay_sec", 1.0)

        for attempt in range(1, max_retries + 1):
            ctx.log(f"发送活动快捷键（尝试 {attempt}/{max_retries}）…", level="info")
            if not ctx.send_hotkey("open_activity"):
                ctx.log("打不开活动界面（open_activity 快捷键未配置），放弃该号。", level="error")
                rec["done"] = True
                return
            self._interruptible_sleep(ctx, retry_delay)

            # 检测活动列表是否出现
            list_region = regions.get("activity_list")
            if list_region:
                rect = ctx.window.region_to_screen_rect(list_region)
                scene = win_mod.grab(rect) if rect else None
                if scene is not None:
                    for flag_key in ["shimen_entry", "activity_join"]:
                        tpl = self.flags.get(flag_key)
                        if tpl is not None:
                            hit = vision.match(scene, tpl, threshold)
                            if hit:
                                ctx.log("✓ 活动列表已打开（检测到活动卡片）。", level="info")
                                self._goto(rec, S_FIND_CARD)
                                return
                    ctx.log("活动列表已打开（截图成功，未立即找到师门卡片）。", level="info")
                    self._goto(rec, S_FIND_CARD)
                    return
            else:
                ctx.log("已发送活动快捷键（未标定活动列表区域，跳过检测）。", level="info")
                self._goto(rec, S_FIND_CARD)
                return

            if attempt < max_retries:
                ctx.log(f"活动列表未出现，{retry_delay}秒后重试…", level="warn")

        ctx.log("❌ 发送活动快捷键多次后仍未检测到活动列表，放弃该号。", level="error")
        rec["done"] = True

    def _do_find_card(self, ctx, rec, loop, regions, threshold):
        """滚轮找师门条目 → 点参加"""
        list_region = regions.get("activity_list")
        entry_tpl = self.flags.get("shimen_entry")
        join_tpl = self.flags.get("activity_join")

        if entry_tpl is None:
            ctx.log("❌ shimen_entry 模板未加载。", level="error")
            rec["done"] = True
            return
        if join_tpl is None:
            ctx.log("❌ activity_join 模板未加载。", level="error")
            rec["done"] = True
            return

        def grab_rect():
            return (ctx.window.region_to_screen_rect(list_region)
                    if list_region else ctx.window.rect())

        def probe(scene, rect):
            if scene is None:
                return scan.SCROLL, None

            hit = vision.match(scene, entry_tpl, threshold)
            if hit is None:
                hit_low = vision.match(scene, entry_tpl, 0.70)
                if hit_low:
                    cx, cy, score = hit_low
                    ctx.log(f"⚠ 低阈值(0.70)匹配到疑似「师门」（{score:.3f}<{threshold}），建议降低阈值。", level="warn")
                else:
                    ctx.log(f"❌ 未找到「师门」（阈值 {threshold}），继续滚动…", level="info")
                return scan.SCROLL, None

            cx, cy, score = hit
            entry_xy = (rect[0] + cx, rect[1] + cy)
            ctx.log(f"✓ 找到「师门」图标（{score:.3f}），坐标 {entry_xy}，开始找「参加」按钮…", level="info")

            join = self._find_join_on_row(ctx, list_region, entry_xy, threshold, loop,
                                            "activity_join", "shimen_entry")
            if join is not None:
                ctx.mouse.click(join[0], join[1])
                ctx.log(f"找到「师门」→ 点「参加」（{join[2]:.3f}）。", level="hit")
                return scan.ACCEPT, join

            ctx.log("认出「师门」但没找到「参加」（检查 activity_join 模板/阈值）。", level="warn")
            return scan.STAY, None

        ctx.log(f"🔍 开始在活动列表搜索「师门」（阈值 {threshold}）…")
        res = scan.scroll_search(
            grab_rect=grab_rect, probe=probe, mouse=ctx.mouse,
            should_stop=ctx.should_stop,
            sleep=lambda s: self._interruptible_sleep(ctx, self._jitter(s, ctx)),
            scroll_step=loop.get("scroll_step", -3),
            max_tries=max(1, loop.get("scroll_max_tries", 8)),
            settle_sec=loop.get("scroll_settle_sec", 0.35),
            reset_to_top=loop.get("scroll_reset_top", True),
            end_diff=loop.get("scroll_end_diff", 2.0),
            reset_max=loop.get("scroll_reset_max", 20),
            log=ctx.log, label="活动列表")
        if res.found:
            self._goto(rec, S_CLICK_GO)
            return
        if res.stopped:
            return
        ctx.log("活动列表里翻找「师门」多次未果。", level="warn")
        rec["done"] = True

    def _do_click_go(self, ctx, rec, loop, threshold):
        """点「去完成」按钮"""
        # 可能需要等待一下让按钮出现
        if self._state_elapsed(rec) < 2:
            self._interruptible_sleep(ctx, 0.5)
            return

        scene_rect = self._scene_rect(ctx, ctx.task_cfg(self.name).get("regions", {}))
        cur = win_mod.grab(scene_rect)
        hit = self._match_scene(cur, scene_rect, "shimen_go", threshold)
        if hit is not None:
            ctx.mouse.click(hit[0], hit[1])
            ctx.log(f"点击「去完成」（{hit[2]:.3f}）。", level="hit")
            self._goto(rec, S_WAIT_COMPLETE)
        elif self._state_elapsed(rec) > 30:
            ctx.log("等待去完成按钮超时，重试。", level="warn")
            self._goto(rec, S_FIND_CARD)

    def _do_wait_complete(self, ctx, rec, loop, threshold, max_runs):
        """等待十轮完成（监控完成面板）"""
        max_wait = 600  # 最多等10分钟

        # 已完成的号直接跳过
        if rec["runs_done"] >= max_runs:
            ctx.log(f"该号已完成 {rec['runs_done']} 轮师门，跳过。", level="info")
            rec["done"] = True
            return

        scene_rect = self._scene_rect(ctx, ctx.task_cfg(self.name).get("regions", {}))
        cur = win_mod.grab(scene_rect)
        hit = self._match_scene(cur, scene_rect, "shimen_complete_panel", threshold)
        if hit is not None:
            ctx.log(f"✓ 检测到完成面板（{hit[2]:.3f}）。", level="hit")
            rec["runs_done"] += 1
            ctx.log(f"第 {rec['runs_done']} 轮师门完成！", level="hit")
            if rec["runs_done"] >= max_runs:
                ctx.log(f"该号已完成 {rec['runs_done']} 轮师门。", level="hit")
            self._goto(rec, S_CLICK_CONFIRM)
            return

        # 超时检查
        if self._state_elapsed(rec) > max_wait:
            ctx.log("等待完成超时，跳过本轮。", level="warn")
            self._goto(rec, S_CLOSE_PANEL)
            return

        # 每30秒报告一次
        elapsed = int(self._state_elapsed(rec))
        if elapsed % 30 == 0 and elapsed > 0:
            ctx.log(f"等待中…已等待 {elapsed} 秒。")

    def _do_click_confirm(self, ctx, rec, threshold):
        """点击确定按钮"""
        scene_rect = self._scene_rect(ctx, ctx.task_cfg(self.name).get("regions", {}))
        cur = win_mod.grab(scene_rect)
        hit = self._match_scene(cur, scene_rect, "shimen_confirm", threshold)
        if hit is not None:
            ctx.mouse.click(hit[0], hit[1])
            ctx.log(f"点击「确定」（{hit[2]:.3f}）。", level="hit")
            self._interruptible_sleep(ctx, 0.5)
            self._goto(rec, S_CLAIM_REWARD)
        else:
            # 没找到确定按钮，可能不需要，直接领取
            self._goto(rec, S_CLAIM_REWARD)

    def _do_claim_reward(self, ctx, rec, threshold):
        """领取奖励（可选）"""
        claim_tpl = self.flags.get("shimen_claim")
        if claim_tpl is None:
            ctx.log("未标定「领取奖励」模板，跳过。")
            self._goto(rec, S_CLOSE_PANEL)
            return

        scene_rect = self._scene_rect(ctx, ctx.task_cfg(self.name).get("regions", {}))
        cur = win_mod.grab(scene_rect)
        hit = self._match_scene(cur, scene_rect, "shimen_claim", threshold)
        if hit is not None:
            ctx.mouse.click(hit[0], hit[1])
            ctx.log(f"点击「领取奖励」（{hit[2]:.3f}）。", level="hit")
        else:
            ctx.log("未找到「领取奖励」按钮，跳过。")
        self._goto(rec, S_CLOSE_PANEL)

    def _do_close_panel(self, ctx, rec, threshold):
        """关闭完成面板"""
        scene_rect = self._scene_rect(ctx, ctx.task_cfg(self.name).get("regions", {}))
        cur = win_mod.grab(scene_rect)
        hit = self._match_scene(cur, scene_rect, "shimen_close", threshold)
        if hit is not None:
            ctx.mouse.click(hit[0], hit[1])
            ctx.log(f"点击关闭按钮（{hit[2]:.3f}）。", level="hit")
            self._interruptible_sleep(ctx, 0.5)
            self._goto(rec, S_USE_REWARD)
        else:
            # 可能没有面板，直接进使用
            self._goto(rec, S_USE_REWARD)

    def _do_use_reward(self, ctx, rec, loop, threshold, max_runs):
        """使用奖励（多次使用，用到没有使用按钮为止）"""
        use_tpl = self.flags.get("reward_use")
        if use_tpl is None:
            ctx.log("未标定「使用按钮」模板，跳过使用奖励。")
            # 一轮完成，准备下一轮或结束
            if rec["runs_done"] >= max_runs:
                ctx.log(f"该号已完成 {rec['runs_done']} 轮师门。", level="hit")
                rec["done"] = True
            else:
                ctx.log(f"第 {rec['runs_done']} 轮师门完成，准备下一轮…")
                self._goto(rec, S_FIND_CARD)
            return

        max_attempts = 10
        for i in range(max_attempts):
            if ctx.should_stop():
                break

            scene_rect = self._scene_rect(ctx, ctx.task_cfg(self.name).get("regions", {}))
            cur = win_mod.grab(scene_rect)
            hit = self._match_scene(cur, scene_rect, "reward_use", threshold)
            if hit is not None:
                ctx.mouse.click(hit[0], hit[1])
                ctx.log(f"使用奖励（{hit[2]:.3f}）。", level="info")
                self._interruptible_sleep(ctx, 0.5)
            else:
                ctx.log("没有更多使用按钮，结束使用奖励。")
                break

        # 一轮完成，准备下一轮或结束
        if rec["runs_done"] >= max_runs:
            ctx.log(f"该号已完成 {rec['runs_done']} 轮师门。", level="hit")
            rec["done"] = True
        else:
            ctx.log(f"第 {rec['runs_done']} 轮师门完成，准备下一轮…")
            self._goto(rec, S_FIND_CARD)

    def _dry_run_selfcheck(self, ctx, regions, threshold):
        """演练模式：只做模板识别自检"""
        ctx.log("演练自检：检查模板加载状态…")
        for fk in self._FLAG_KEYS:
            tpl = self.flags.get(fk)
            if tpl is not None:
                ctx.log(f"✓ {fk} 已加载（形状 {tpl.shape}）")
            else:
                ctx.log(f"❌ {fk} 未加载", level="warn")

        scene_rect = self._scene_rect(ctx, regions)
        cur = win_mod.grab(scene_rect)
        if cur is None:
            ctx.log("❌ 截图失败", level="error")
            return

        for fk in self._FLAG_KEYS:
            tpl = self.flags.get(fk)
            if tpl is None:
                continue
            hit = vision.match(cur, tpl, threshold)
            if hit is not None:
                ctx.log(f"✓ {fk} 匹配成功（{hit[2]:.3f}），坐标 ({hit[0]}, {hit[1]})", level="hit")
            else:
                ctx.log(f"○ {fk} 未匹配（当前画面无此元素）")
        ctx.log("演练自检完成。")
