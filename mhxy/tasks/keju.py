# -*- coding: utf-8 -*-
"""
科举任务（自动完成科举答题）。

完整流程：
  开活动 → 找科举条目 → 点参加
  → 点击答案（只选A）→ 关闭

停止条件：①答题完成 ②时间上限 ③手动停止/急停键。
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
S_FIND_CARD = "FIND_CARD"           # 滚轮找科举条目 → 点参加
S_ANSWER = "ANSWER"                 # 点击选择题答案（只选A）
S_CLOSE_PANEL = "CLOSE_PANEL"       # 关闭面板

_REQUIRED_FLAGS = ["keju_entry", "activity_join", "keju_answer_a", "keju_close"]

# 状态中文映射
_STATE_CN = {
    S_OPEN_ACTIVITY: "开活动",
    S_FIND_CARD: "找科举",
    S_ANSWER: "答题",
    S_CLOSE_PANEL: "关面板",
}


@register
class KejuTask(Task):
    name = "keju"
    title = "科举"
    description = "自动完成科举：开活动→参加→答题→关闭"
    is_dungeon = False
    CHAINS_PER_WINDOW = True
    _FLAG_KEYS = [
        "keju_entry",
        "activity_join",
        "keju_answer_a",
        "keju_close",
    ]

    CALIBRATION = {
        "regions": [
            *Task.BASE_CALIBRATION_REGIONS,
        ],
        "templates": [
            ("keju_entry", "科举入口", "活动列表里「科举」那一条，框图标+文字（左侧），不要框参加按钮"),
            ("keju_answer_a", "答案A按钮", "选择题的第一个选项按钮（只选A）"),
            ("keju_close", "关闭按钮", "面板右上角的关闭按钮（X 或 关闭）"),
        ],
        "watchlist": False,
    }

    def preflight(self, ctx):
        problems = []
        tc = ctx.task_cfg(self.name)
        wins = ctx.select_windows()

        if not wins:
            problems.append("没找到/没选中目标窗口 —— 请先「选择窗口」选好要跑科举的号")

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

        return (len(problems) == 0), problems

    # ------------------------------------------------------------------
    def run(self, ctx):
        tc = ctx.task_cfg(self.name)
        loop = tc.get("loop", {})
        regions = tc.get("regions", {})
        dry_run = tc.get("dry_run", True)
        threshold = loop.get("match_threshold", 0.85)
        time_limit = loop.get("time_limit_min", 10) or 10

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

        ctx.log(f"开始科举任务（{len(wctxs)} 个号）…")

        # 每号独立状态
        records = []
        for wctx in wctxs:
            rec = {"ctx": wctx, "state": S_OPEN_ACTIVITY, "t_state": time.time(),
                   "answers_done": 0, "done": False, "dead_logged": False}
            records.append(rec)

        # 多开轮转
        rotation.run_rotation(self._make_rotation(
            ctx, records,
            lambda rec: self._step_once(rec["ctx"], rec, loop, regions, threshold),
            multi, switch_delay, tick, time_limit))

        # 结束汇总
        total_answers = sum(r["answers_done"] for r in records)
        if all(r["done"] for r in records):
            ctx.log("所有号都已完成科举。")
        ctx.log(f"已停止。共答题 {total_answers} 次，用时 {(time.time() - start_ts) / 60:.1f} 分钟。")

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
        self.flags = self._load_flags(tc)
        rec = {"ctx": wctx, "state": S_OPEN_ACTIVITY, "t_state": time.time(),
               "answers_done": 0, "done": False, "dead_logged": False}
        return rec, (lambda: self._step_once(wctx, rec, loop, regions, threshold))

    def _step_once(self, ctx, rec, loop, regions, threshold):
        st = rec["state"]
        if st == S_OPEN_ACTIVITY:
            self._do_open_activity(ctx, rec, loop, regions, threshold)
        elif st == S_FIND_CARD:
            self._do_find_card(ctx, rec, loop, regions, threshold)
        elif st == S_ANSWER:
            self._do_answer(ctx, rec, loop, threshold)
        elif st == S_CLOSE_PANEL:
            self._do_close_panel(ctx, rec, threshold)

    # ------------------------------------------------------------------
    # 流程步骤
    # ------------------------------------------------------------------
    def _do_open_activity(self, ctx, rec, loop, regions, threshold):
        self._ensure_activity_open(ctx, rec, loop, regions, threshold, S_FIND_CARD)

    def _do_find_card(self, ctx, rec, loop, regions, threshold):
        """滚轮找科举条目 → 点参加"""
        list_region = regions.get("activity_list")
        entry_tpl = self.flags.get("keju_entry")
        join_tpl = self.flags.get("activity_join")

        if entry_tpl is None:
            ctx.log("❌ keju_entry 模板未加载。", level="error")
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
                    ctx.log(f"⚠ 低阈值(0.70)匹配到疑似「科举」（{score:.3f}<{threshold}），建议降低阈值。", level="warn")
                else:
                    ctx.log(f"❌ 未找到「科举」（阈值 {threshold}），继续滚动…", level="info")
                return scan.SCROLL, None

            cx, cy, score = hit
            entry_xy = (rect[0] + cx, rect[1] + cy)
            ctx.log(f"✓ 找到「科举」图标（{score:.3f}），坐标 {entry_xy}，开始找「参加」按钮…", level="info")

            join = self._find_join_on_row(ctx, list_region, entry_xy, threshold, loop,
                                            "activity_join", "keju_entry")
            if join is not None:
                ctx.mouse.click(join[0], join[1])
                ctx.log(f"找到「科举」→ 点「参加」（{join[2]:.3f}）。", level="hit")
                return scan.ACCEPT, join

            ctx.log("认出「科举」但没找到「参加」（检查 activity_join 模板/阈值）。", level="warn")
            return scan.STAY, None

        ctx.log(f"🔍 开始在活动列表搜索「科举」（阈值 {threshold}）…")
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
            self._goto(rec, S_ANSWER)
            return
        if res.stopped:
            return
        ctx.log("活动列表里翻找「科举」多次未果。", level="warn")
        rec["done"] = True

    def _do_answer(self, ctx, rec, loop, threshold):
        """点击选择题答案（只选A）"""
        answer_tpl = self.flags.get("keju_answer_a")
        if answer_tpl is None:
            ctx.log("❌ keju_answer_a 模板未加载。", level="error")
            rec["done"] = True
            return

        scene_rect = self._scene_rect(ctx, ctx.task_cfg(self.name).get("regions", {}))
        cur = win_mod.grab(scene_rect)
        hit = self._match_scene(cur, scene_rect, "keju_answer_a", threshold)

        if hit is not None:
            # 向上偏移10px
            x, y, score = hit
            ctx.mouse.click(x, y - 10)
            rec["answers_done"] += 1
            ctx.log(f"点击答案A（{score:.3f}），第 {rec['answers_done']} 次点击。", level="hit")
            self._interruptible_sleep(ctx, 1.0)
            # 重置超时计时
            rec["t_state"] = time.time()
        else:
            # 找不到答案按钮，可能已答完
            if self._state_elapsed(rec) > 15:
                ctx.log("未找到答案按钮，可能已答完，关闭面板。", level="info")
                self._goto(rec, S_CLOSE_PANEL)
            else:
                self._interruptible_sleep(ctx, 0.5)

    def _do_close_panel(self, ctx, rec, threshold):
        """关闭面板"""
        scene_rect = self._scene_rect(ctx, ctx.task_cfg(self.name).get("regions", {}))
        cur = win_mod.grab(scene_rect)
        hit = self._match_scene(cur, scene_rect, "keju_close", threshold)
        if hit is not None:
            ctx.mouse.click(hit[0], hit[1])
            ctx.log(f"点击关闭按钮（{hit[2]:.3f}）。", level="hit")
            self._interruptible_sleep(ctx, 0.5)
            rec["done"] = True
        else:
            ctx.log("未找到关闭按钮，标记完成。")
            rec["done"] = True

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
