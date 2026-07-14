# -*- coding: utf-8 -*-
"""
使用活力任务（自动使用活力打工）。

完整流程：
  Alt+W 打开人物属性 → 点击「使用活力」→ 点击「打工」18次 → 关闭面板

停止条件：①打工18次 ②手动停止/急停键。
安全默认 dry_run=true：只做模板识别自检。
"""

import time

from ..core import vision
from ..core import window as win_mod
from .base import Task, register

_REQUIRED_FLAGS = ["vitality_btn", "vitality_work", "vitality_close"]


@register
class VitalityTask(Task):
    name = "vitality"
    title = "使用活力"
    description = "自动使用活力打工：打开人物属性→使用活力→打工18次→关闭"
    is_dungeon = False
    CHAINS_PER_WINDOW = False
    _FLAG_KEYS = [
        "vitality_btn",
        "vitality_work",
        "vitality_close",
    ]

    CALIBRATION = {
        "no_game_window": False,
        "regions": [
            ("scene", "主识别区", "留空=整个窗口当识别区(推荐)", True),
        ],
        "templates": [
            ("vitality_btn", "使用活力按钮", "人物属性面板里的「使用活力」按钮"),
            ("vitality_work", "打工按钮", "活力面板里的「打工」按钮"),
            ("vitality_close", "关闭按钮", "面板右上角的关闭按钮（X 或 关闭）"),
        ],
        "watchlist": False,
    }

    def preflight(self, ctx):
        problems = []
        tc = ctx.task_cfg(self.name)
        wins = ctx.select_windows()

        if not wins:
            problems.append("没找到/没选中目标窗口 —— 请先「选择窗口」选好要使用活力的号")

        # 检查快捷键
        if not ctx.hotkeys.get("open_character"):
            problems.append("打开『人物属性』缺快捷键（open_character）")

        templates = tc.get("templates", {})
        for tk in _REQUIRED_FLAGS:
            path = templates.get(tk)
            if not path or vision.load_template(path) is None:
                problems.append(f"模板『{tk}』缺失或加载失败 —— 请在标定向导里框选裁图")

        return (len(problems) == 0), problems

    # ------------------------------------------------------------------
    def run(self, ctx):
        tc = ctx.task_cfg(self.name)
        dry_run = tc.get("dry_run", True)
        threshold = tc.get("loop", {}).get("match_threshold", 0.85)
        work_count = tc.get("work_count", 18)

        self.flags = self._load_flags(tc)

        wins = ctx.select_windows()
        if not wins:
            ctx.log("没找到/没选中目标窗口，已停止。", level="error")
            return

        if dry_run:
            ctx.log("演练模式：只做模板识别自检，不执行真实操作。", level="warn")
            for i, win in enumerate(wins):
                ctx.log(f"--- 号{i + 1} ---")
                ctx.window = win
                self._dry_run_selfcheck(ctx, threshold)
            return

        ctx.log(f"开始使用活力任务（{len(wins)} 个号，每号打工 {work_count} 次）…")

        for i, win in enumerate(wins):
            if ctx.should_stop():
                break

            ctx.window = win
            ctx.log(f"--- 号{i + 1} ---")

            # 先激活窗口到前台
            self._focus(ctx)
            self._interruptible_sleep(ctx, 0.5)

            # 步骤1：Alt+W 打开人物属性
            ctx.log("发送 Alt+W 打开人物属性…")
            if not ctx.send_hotkey("open_character"):
                ctx.log(f"号{i + 1} 打不开人物属性，跳过。", level="warn")
                continue
            self._interruptible_sleep(ctx, 1.0)

            # 步骤2：点击使用活力
            if not self._click_template(ctx, "vitality_btn", threshold, "使用活力"):
                ctx.log(f"号{i + 1} 未找到「使用活力」按钮，跳过。", level="warn")
                # 关闭可能打开的面板
                self._click_template(ctx, "vitality_close", threshold, "关闭")
                self._interruptible_sleep(ctx, 0.3)
                continue

            self._interruptible_sleep(ctx, 0.5)

            # 步骤3：点击打工 N 次
            done_count = 0
            for j in range(work_count):
                if ctx.should_stop():
                    break
                if not self._click_template(ctx, "vitality_work", threshold, "打工"):
                    ctx.log(f"号{i + 1} 第 {j + 1} 次打工失败，可能活力不足。", level="warn")
                    break
                done_count += 1
                ctx.log(f"号{i + 1} 第 {j + 1}/{work_count} 次打工完成。", level="info")
                self._interruptible_sleep(ctx, 0.3)

            self._interruptible_sleep(ctx, 0.5)

            # 步骤4：点击关闭按钮（两次）
            for k in range(2):
                if ctx.should_stop():
                    break
                self._click_template(ctx, "vitality_close", threshold, "关闭")
                self._interruptible_sleep(ctx, 0.3)

            ctx.log(f"号{i + 1} 使用活力完成（打工 {done_count} 次）。", level="hit")
            self._interruptible_sleep(ctx, 0.5)

        ctx.log("所有号使用活力任务完成。", level="hit")

    # ------------------------------------------------------------------
    # 日常一条龙·每窗口独立链：暴露「单窗口一份 record + 单步推进函数」
    # ------------------------------------------------------------------
    def make_chain_driver(self, wctx):
        """给定单窗口上下文，返回 (record, step_fn)。step_fn() 推进该窗口本任务状态机一步。
        vitality 是简单任务：一轮 run 就完成，所以 step_fn 直接跑 run()。"""
        tc = wctx.task_cfg(self.name)
        self.flags = self._load_flags(tc)
        rec = {"ctx": wctx, "done": False, "dead_logged": False}
        def step():
            if rec["done"]:
                return
            self._run_single(wctx, tc)
            rec["done"] = True
        return rec, step

    def _run_single(self, ctx, tc):
        """单号执行使用活力"""
        dry_run = tc.get("dry_run", True)
        threshold = tc.get("loop", {}).get("match_threshold", 0.85)
        work_count = tc.get("work_count", 18)

        if dry_run:
            return

        self._focus(ctx)
        self._interruptible_sleep(ctx, 0.5)

        ctx.log("发送 Alt+W 打开人物属性…")
        if not ctx.send_hotkey("open_character"):
            ctx.log("打不开人物属性，跳过。", level="warn")
            return
        self._interruptible_sleep(ctx, 1.0)

        if not self._click_template(ctx, "vitality_btn", threshold, "使用活力"):
            ctx.log("未找到「使用活力」按钮，跳过。", level="warn")
            self._click_template(ctx, "vitality_close", threshold, "关闭")
            return
        self._interruptible_sleep(ctx, 0.5)

        for j in range(work_count):
            if ctx.should_stop():
                break
            if not self._click_template(ctx, "vitality_work", threshold, "打工"):
                break
            self._interruptible_sleep(ctx, 0.3)

        self._interruptible_sleep(ctx, 0.5)
        for k in range(2):
            if ctx.should_stop():
                break
            self._click_template(ctx, "vitality_close", threshold, "关闭")
            self._interruptible_sleep(ctx, 0.3)

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------
    def _click_template(self, ctx, flag_key, threshold, label):
        """查找并点击模板"""
        tpl = self.flags.get(flag_key)
        if tpl is None:
            ctx.log(f"❌ {flag_key} 模板未加载。", level="error")
            return False

        scene_rect = ctx.window.rect()
        scene = win_mod.grab(scene_rect)
        if scene is None:
            ctx.log("❌ 截图失败。", level="error")
            return False

        hit = vision.match(scene, tpl, threshold)
        if hit is None:
            ctx.log(f"❌ 未找到「{label}」。", level="warn")
            return False

        x, y, score = hit
        screen_x = scene_rect[0] + x
        screen_y = scene_rect[1] + y
        ctx.mouse.click(screen_x, screen_y)
        ctx.log(f"点击「{label}」（{score:.3f}）。", level="hit")
        return True

    def _dry_run_selfcheck(self, ctx, threshold):
        """演练模式：只做模板识别自检"""
        ctx.log("演练自检：检查模板加载状态…")
        for fk in self._FLAG_KEYS:
            tpl = self.flags.get(fk)
            if tpl is not None:
                ctx.log(f"✓ {fk} 已加载（形状 {tpl.shape}）")
            else:
                ctx.log(f"❌ {fk} 未加载", level="warn")

        scene_rect = ctx.window.rect()
        scene = win_mod.grab(scene_rect)
        if scene is None:
            ctx.log("❌ 截图失败", level="error")
            return

        for fk in self._FLAG_KEYS:
            tpl = self.flags.get(fk)
            if tpl is None:
                continue
            hit = vision.match(scene, tpl, threshold)
            if hit is not None:
                ctx.log(f"✓ {fk} 匹配成功（{hit[2]:.3f}），坐标 ({hit[0]}, {hit[1]})", level="hit")
            else:
                ctx.log(f"○ {fk} 未匹配（当前画面无此元素）")
        ctx.log("演练自检完成。")
