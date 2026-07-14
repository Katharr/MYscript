# -*- coding: utf-8 -*-
"""
趣闻鉴赏任务（自动完成趣闻鉴赏点赞）。

完整流程：
  开活动 → 找趣闻鉴赏条目 → 点参加
  → 在帖子列表找未点赞按钮 → 点赞
  → 向下滚动 → 继续点赞 → 直到点满5个赞
  → 关闭帖子列表

支持多开轮转：每个号独立状态，已完成的号跳过。
停止条件：①点满5个赞 ②时间上限 ③手动停止/急停键。
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
S_FIND_CARD = "FIND_CARD"           # 滚轮找趣闻鉴赏条目 → 点参加
S_FIND_LIKE = "FIND_LIKE"           # 在帖子列表找未点赞按钮
S_CLICK_LIKE = "CLICK_LIKE"         # 点击点赞
S_CLOSE_PANEL = "CLOSE_PANEL"       # 关闭帖子列表

_REQUIRED_FLAGS = ["quwen_entry", "activity_join", "quwen_like", "quwen_close"]

# 状态中文映射
_STATE_CN = {
    S_OPEN_ACTIVITY: "开活动",
    S_FIND_CARD: "找趣闻鉴赏",
    S_FIND_LIKE: "找点赞按钮",
    S_CLICK_LIKE: "点点赞",
    S_CLOSE_PANEL: "关面板",
}


@register
class QuwenTask(Task):
    name = "quwen"
    title = "趣闻鉴赏"
    description = "自动完成趣闻鉴赏：开活动→参加→点赞5次→关闭"
    is_dungeon = False
    CHAINS_PER_WINDOW = True
    _FLAG_KEYS = [
        "quwen_entry",
        "activity_join",
        "quwen_like",
        "quwen_liked",
        "quwen_close",
    ]

    CALIBRATION = {
        "regions": [
            *Task.BASE_CALIBRATION_REGIONS,
            ("post_list", "帖子列表区域", "趣闻鉴赏的帖子列表区域，用于滚动查找未点赞按钮"),
        ],
        "templates": [
            *Task.BASE_CALIBRATION_TEMPLATES,
            ("quwen_entry", "趣闻鉴赏入口", "活动列表里「趣闻鉴赏」那一条，框图标+文字（左侧），不要框参加按钮"),
            ("quwen_like", "点赞按钮(未点赞)", "帖子列表里的点赞按钮（未点赞状态，通常是空心或灰色）"),
            ("quwen_liked", "已点赞按钮(可选)", "帖子列表里的点赞按钮（已点赞状态，通常是实心或红色）"),
            ("quwen_close", "关闭按钮", "帖子列表右上角的关闭按钮（X 或 关闭）"),
        ],
        "watchlist": False,
    }

    def preflight(self, ctx):
        problems = []
        tc = ctx.task_cfg(self.name)
        wins = ctx.select_windows()

        if not wins:
            problems.append("没找到/没选中目标窗口 —— 请先「选择窗口」选好要跑趣闻鉴赏的号")

        # 检查快捷键
        if not ctx.hotkeys.get("open_activity"):
            problems.append("打开『活动』缺快捷键（open_activity）")

        regions = tc.get("regions", {})
        templates = tc.get("templates", {})

        for rk, label in [("activity_list", "活动列表区域"), ("post_list", "帖子列表区域")]:
            if not regions.get(rk):
                problems.append(f"『{label}』未标定 —— 请在标定向导里框选")

        for tk in _REQUIRED_FLAGS:
            path = templates.get(tk)
            if not path or vision.load_template(path) is None:
                problems.append(f"模板『{tk}』缺失或加载失败 —— 请在标定向导里框选裁图")

        optional = ["quwen_liked"]
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
        max_likes = loop.get("max_likes", 5)
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

        ctx.log(f"开始趣闻鉴赏任务（目标 {max_likes} 个赞，{len(wctxs)} 个号）…")

        # 每号独立状态
        records = []
        for wctx in wctxs:
            rec = {"ctx": wctx, "state": S_OPEN_ACTIVITY, "t_state": time.time(),
                   "likes_done": 0, "done": False, "dead_logged": False}
            records.append(rec)

        # 多开轮转
        rotation.run_rotation(self._make_rotation(
            ctx, records,
            lambda rec: self._step_once(rec["ctx"], rec, loop, regions, threshold, max_likes),
            multi, switch_delay, tick, time_limit))

        # 结束汇总
        total_likes = sum(r["likes_done"] for r in records)
        if all(r["done"] for r in records):
            ctx.log("所有号都已完成趣闻鉴赏。")
        ctx.log(f"已停止。共点赞 {total_likes} 次，用时 {(time.time() - start_ts) / 60:.1f} 分钟。")

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
        与 run() 共用 _new_record/_step_once，不自跑 rotation、不切前台（由一条龙总轮转统一切）。"""
        tc = wctx.task_cfg(self.name)
        loop = tc.get("loop", {})
        regions = tc.get("regions", {})
        threshold = loop.get("match_threshold", 0.85)
        max_likes = loop.get("max_likes", 5)
        self.flags = self._load_flags(tc)
        rec = {"ctx": wctx, "state": S_OPEN_ACTIVITY, "t_state": time.time(),
               "likes_done": 0, "done": False, "dead_logged": False}
        return rec, (lambda: self._step_once(wctx, rec, loop, regions, threshold, max_likes))

    # ------------------------------------------------------------------
    # 单步推进（供轮转引擎调用）
    # ------------------------------------------------------------------
    def _step_once(self, ctx, rec, loop, regions, threshold, max_likes):
        st = rec["state"]
        if st == S_OPEN_ACTIVITY:
            self._do_open_activity(ctx, rec, loop, regions, threshold)
        elif st == S_FIND_CARD:
            self._do_find_card(ctx, rec, loop, regions, threshold)
        elif st == S_FIND_LIKE:
            self._do_find_like(ctx, rec, loop, regions, threshold, max_likes)
        elif st == S_CLICK_LIKE:
            self._do_click_like(ctx, rec, threshold, max_likes)
        elif st == S_CLOSE_PANEL:
            self._do_close_panel(ctx, rec, threshold)

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
                    for flag_key in ["quwen_entry", "activity_join"]:
                        tpl = self.flags.get(flag_key)
                        if tpl is not None:
                            hit = vision.match(scene, tpl, threshold)
                            if hit:
                                ctx.log("✓ 活动列表已打开（检测到活动卡片）。", level="info")
                                self._goto(rec, S_FIND_CARD)
                                return
                    ctx.log("活动列表已打开（截图成功，未立即找到趣闻鉴赏卡片）。", level="info")
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
        """滚轮找趣闻鉴赏条目 → 点参加"""
        list_region = regions.get("activity_list")
        entry_tpl = self.flags.get("quwen_entry")
        join_tpl = self.flags.get("activity_join")

        if entry_tpl is None:
            ctx.log("❌ quwen_entry 模板未加载。", level="error")
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
                    ctx.log(f"⚠ 低阈值(0.70)匹配到疑似「趣闻鉴赏」（{score:.3f}<{threshold}），建议降低阈值。", level="warn")
                else:
                    ctx.log(f"❌ 未找到「趣闻鉴赏」（阈值 {threshold}），继续滚动…", level="info")
                return scan.SCROLL, None

            cx, cy, score = hit
            entry_xy = (rect[0] + cx, rect[1] + cy)
            ctx.log(f"✓ 找到「趣闻鉴赏」图标（{score:.3f}），坐标 {entry_xy}，开始找「参加」按钮…", level="info")

            join = self._find_join_on_row(ctx, list_region, entry_xy, threshold, loop,
                                            "activity_join", "quwen_entry")
            if join is not None:
                ctx.mouse.click(join[0], join[1])
                ctx.log(f"找到「趣闻鉴赏」→ 点「参加」（{join[2]:.3f}）。", level="hit")
                return scan.ACCEPT, join

            ctx.log("认出「趣闻鉴赏」但没找到「参加」（检查 activity_join 模板/阈值）。", level="warn")
            return scan.STAY, None

        ctx.log(f"🔍 开始在活动列表搜索「趣闻鉴赏」（阈值 {threshold}）…")
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
            self._goto(rec, S_FIND_LIKE)
            return
        if res.stopped:
            return
        ctx.log("活动列表里翻找「趣闻鉴赏」多次未果。", level="warn")
        rec["done"] = True

    def _do_find_like(self, ctx, rec, loop, regions, threshold, max_likes):
        """在帖子列表找未点赞按钮"""
        post_region = regions.get("post_list")
        like_tpl = self.flags.get("quwen_like")
        liked_tpl = self.flags.get("quwen_liked")  # 已点赞模板（可选）

        if like_tpl is None:
            ctx.log("❌ quwen_like 模板未加载。", level="error")
            rec["done"] = True
            return

        def grab_rect():
            return (ctx.window.region_to_screen_rect(post_region)
                    if post_region else ctx.window.rect())

        def probe(scene, rect):
            if scene is None:
                return scan.SCROLL, None

            hit = vision.match(scene, like_tpl, threshold)
            if hit is None:
                hit_low = vision.match(scene, like_tpl, 0.70)
                if hit_low:
                    cx, cy, score = hit_low
                    ctx.log(f"⚠ 低阈值(0.70)匹配到疑似点赞按钮（{score:.3f}<{threshold}），建议降低阈值。", level="warn")
                    # 检查是否已点赞
                    if liked_tpl is not None:
                        liked_hit = vision.match(scene, liked_tpl, threshold)
                        if liked_hit and self._is_nearby((cx, cy), (liked_hit[0], liked_hit[1]), 30):
                            ctx.log("该按钮已点赞，跳过。", level="info")
                            return scan.SCROLL, None
                    return scan.ACCEPT, (rect[0] + cx, rect[1] + cy, score)
                else:
                    ctx.log(f"❌ 未找到点赞按钮（阈值 {threshold}），继续滚动…", level="info")
                return scan.SCROLL, None

            cx, cy, score = hit

            # 检查是否已点赞：如果在附近找到已点赞模板，说明这个按钮是已点赞状态
            if liked_tpl is not None:
                liked_hit = vision.match(scene, liked_tpl, threshold)
                if liked_hit and self._is_nearby((cx, cy), (liked_hit[0], liked_hit[1]), 30):
                    ctx.log(f"点赞按钮已点赞（{score:.3f}），跳过。", level="info")
                    return scan.SCROLL, None

            ctx.log(f"✓ 找到未点赞按钮（{score:.3f}），坐标 ({rect[0] + cx}, {rect[1] + cy})", level="info")
            return scan.ACCEPT, (rect[0] + cx, rect[1] + cy, score)

        ctx.log(f"🔍 开始在帖子列表搜索未点赞按钮…")
        res = scan.scroll_search(
            grab_rect=grab_rect, probe=probe, mouse=ctx.mouse,
            should_stop=ctx.should_stop,
            sleep=lambda s: self._interruptible_sleep(ctx, self._jitter(s, ctx)),
            scroll_step=loop.get("scroll_step", -5),
            max_tries=max(1, loop.get("scroll_max_tries", 8)),
            settle_sec=loop.get("scroll_settle_sec", 0.35),
            reset_to_top=loop.get("scroll_reset_top", False),
            end_diff=loop.get("scroll_end_diff", 2.0),
            reset_max=loop.get("scroll_reset_max", 20),
            log=ctx.log, label="帖子列表")
        if res.found:
            # 保存点赞按钮坐标到 record
            rec["like_pos"] = res.payload
            self._goto(rec, S_CLICK_LIKE)
            return
        if res.stopped:
            return
        ctx.log("帖子列表里翻找点赞按钮多次未果。", level="warn")
        # 没找到点赞按钮，可能都已点赞，关闭面板
        self._goto(rec, S_CLOSE_PANEL)

    def _do_click_like(self, ctx, rec, threshold, max_likes):
        """点击点赞"""
        like_pos = rec.get("like_pos")
        if like_pos is None:
            self._goto(rec, S_FIND_LIKE)
            return

        x, y, score = like_pos
        ctx.mouse.click(x, y)
        rec["likes_done"] += 1
        ctx.log(f"点击点赞（{score:.3f}），第 {rec['likes_done']}/{max_likes} 个赞。", level="hit")
        self._interruptible_sleep(ctx, 0.5)

        if rec["likes_done"] >= max_likes:
            ctx.log(f"该号已点满 {max_likes} 个赞！", level="hit")
            self._goto(rec, S_CLOSE_PANEL)
        else:
            # 继续找下一个点赞按钮
            self._goto(rec, S_FIND_LIKE)

    def _do_close_panel(self, ctx, rec, threshold):
        """关闭帖子列表"""
        scene_rect = self._scene_rect(ctx, ctx.task_cfg(self.name).get("regions", {}))
        cur = win_mod.grab(scene_rect)
        hit = self._match_scene(cur, scene_rect, "quwen_close", threshold)
        if hit is not None:
            ctx.mouse.click(hit[0], hit[1])
            ctx.log(f"点击关闭按钮（{hit[2]:.3f}）。", level="hit")
            self._interruptible_sleep(ctx, 0.5)
            rec["done"] = True
        else:
            # 可能没有面板，直接标记完成
            ctx.log("未找到关闭按钮，标记完成。")
            rec["done"] = True

    @staticmethod
    def _is_nearby(pos1, pos2, distance=30):
        """判断两个坐标是否在指定距离内"""
        x1, y1 = pos1[0], pos1[1]
        x2, y2 = pos2[0], pos2[1]
        return ((x1 - x2) ** 2 + (y1 - y2) ** 2) ** 0.5 <= distance

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
