# -*- coding: utf-8 -*-
"""
领取活跃奖励任务（自动领取活动列表顶端的5个活跃奖励）。

完整流程：
  开活动 → 在活动列表顶端点击5个奖励（共用一个标定，分别点击一次）

停止条件：①领取5个奖励 ②手动停止/急停键。
安全默认 dry_run=true：只做模板识别自检。
"""

import time

from ..core import vision
from ..core import window as win_mod
from .base import Task, register

_REQUIRED_FLAGS = ["reward_entry"]


@register
class RewardTask(Task):
    name = "reward"
    title = "领取活跃奖励"
    description = "自动领取活动列表顶端的5个活跃奖励"
    is_dungeon = False
    CHAINS_PER_WINDOW = True
    _FLAG_KEYS = [
        "reward_entry",
    ]

    CALIBRATION = {
        "regions": [
            ("scene", "主识别区", "留空=整个窗口当识别区(推荐)", True),
            ("activity_list", "活动列表区域", "「活动」界面里那片列表，奖励在顶端"),
        ],
        "templates": [
            ("reward_entry", "奖励按钮", "活动列表顶端的奖励图标（5个奖励共用一个标定，从上到下点击）"),
        ],
        "watchlist": False,
    }

    def preflight(self, ctx):
        problems = []
        tc = ctx.task_cfg(self.name)
        wins = ctx.select_windows()

        if not wins:
            problems.append("没找到/没选中目标窗口 —— 请先「选择窗口」选好要领取奖励的号")

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
        dry_run = tc.get("dry_run", True)
        threshold = tc.get("loop", {}).get("match_threshold", 0.85)
        reward_count = tc.get("reward_count", 5)

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

        ctx.log(f"开始领取活跃奖励任务（{len(wins)} 个号，每号领取 {reward_count} 个奖励）…")

        for i, win in enumerate(wins):
            if ctx.should_stop():
                break

            ctx.window = win
            ctx.log(f"--- 号{i + 1} ---")

            # 先激活窗口到前台
            self._focus(ctx)
            self._interruptible_sleep(ctx, 0.5)

            # 步骤1：开活动
            ctx.log("发送活动快捷键…")
            if not ctx.send_hotkey("open_activity"):
                ctx.log(f"号{i + 1} 打不开活动界面，跳过。", level="warn")
                continue
            self._interruptible_sleep(ctx, 1.0)

            # 步骤2：点击5个奖励
            self._click_rewards(ctx, threshold, reward_count)

            ctx.log(f"号{i + 1} 领取活跃奖励完成。", level="hit")
            self._interruptible_sleep(ctx, 0.5)

        ctx.log("所有号领取活跃奖励任务完成。", level="hit")

    # ------------------------------------------------------------------
    # 日常一条龙·每窗口独立链：暴露「单窗口一份 record + 单步推进函数」
    # ------------------------------------------------------------------
    def make_chain_driver(self, wctx):
        """给定单窗口上下文，返回 (record, step_fn)。step_fn() 推进该窗口本任务状态机一步。
        reward 是简单任务：一轮 run 就完成，所以 step_fn 直接跑 run()。"""
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
        """单号执行领取奖励"""
        dry_run = tc.get("dry_run", True)
        threshold = tc.get("loop", {}).get("match_threshold", 0.85)
        reward_count = tc.get("reward_count", 5)

        if dry_run:
            return

        self._focus(ctx)
        self._interruptible_sleep(ctx, 0.5)

        ctx.log("发送活动快捷键…")
        if not ctx.send_hotkey("open_activity"):
            ctx.log("打不开活动界面，跳过。", level="warn")
            return
        self._interruptible_sleep(ctx, 1.0)

        self._click_rewards(ctx, threshold, reward_count)

    def _click_rewards(self, ctx, threshold, reward_count):
        """点击活动列表顶端的奖励按钮"""
        reward_tpl = self.flags.get("reward_entry")
        if reward_tpl is None:
            ctx.log("❌ reward_entry 模板未加载。", level="error")
            return

        tc = ctx.task_cfg(self.name)
        regions = tc.get("regions", {})
        list_region = regions.get("activity_list")

        # 获取活动列表区域
        if list_region:
            rect = ctx.window.region_to_screen_rect(list_region)
        else:
            rect = ctx.window.rect()

        if rect is None:
            ctx.log("❌ 无法获取活动列表区域坐标。", level="error")
            return

        # 截取活动列表
        scene = win_mod.grab(rect)
        if scene is None:
            ctx.log("❌ 活动列表截图失败。", level="error")
            return

        # 循环查找所有奖励按钮（每次找到后遮盖该区域避免重复匹配）
        hits = []
        scene_copy = scene.copy()
        tpl_h, tpl_w = reward_tpl.shape[:2]

        for _ in range(reward_count):
            hit = vision.match(scene_copy, reward_tpl, threshold)
            if hit is None:
                break
            cx, cy, score = hit
            hits.append((cx, cy, score))
            # 遮盖已匹配区域，避免重复匹配
            y1 = max(0, cy - tpl_h // 2)
            y2 = min(scene_copy.shape[0], cy + tpl_h // 2)
            x1 = max(0, cx - tpl_w // 2)
            x2 = min(scene_copy.shape[1], cx + tpl_w // 2)
            scene_copy[y1:y2, x1:x2] = 0

        if not hits:
            ctx.log("❌ 未找到奖励按钮。", level="warn")
            return

        # 按y坐标排序（从上到下）
        hits.sort(key=lambda h: h[1])

        ctx.log(f"找到 {len(hits)} 个奖励按钮，开始领取…", level="info")

        rx, ry = rect[0], rect[1]
        for j, (cx, cy, score) in enumerate(hits):
            if ctx.should_stop():
                break

            screen_x = rx + cx
            screen_y = ry + cy - 5  # 向上偏移5px
            ctx.mouse.click(screen_x, screen_y)
            ctx.log(f"点击第 {j + 1} 个奖励（{score:.3f}），坐标 ({screen_x}, {screen_y})", level="hit")
            self._interruptible_sleep(ctx, 0.5)

    def _dry_run_selfcheck(self, ctx, threshold):
        """演练模式：只做模板识别自检"""
        ctx.log("演练自检：检查模板加载状态…")
        for fk in self._FLAG_KEYS:
            tpl = self.flags.get(fk)
            if tpl is not None:
                ctx.log(f"✓ {fk} 已加载（形状 {tpl.shape}）")
            else:
                ctx.log(f"❌ {fk} 未加载", level="warn")

        tc = ctx.task_cfg(self.name)
        regions = tc.get("regions", {})
        list_region = regions.get("activity_list")
        if list_region:
            rect = ctx.window.region_to_screen_rect(list_region)
        else:
            rect = ctx.window.rect()

        if rect is None:
            ctx.log("❌ 无法获取活动列表区域", level="error")
            return

        scene = win_mod.grab(rect)
        if scene is None:
            ctx.log("❌ 截图失败", level="error")
            return

        for fk in self._FLAG_KEYS:
            tpl = self.flags.get(fk)
            if tpl is None:
                continue
            # 循环查找匹配
            hits = []
            scene_copy = scene.copy()
            tpl_h, tpl_w = tpl.shape[:2]
            for _ in range(5):
                hit = vision.match(scene_copy, tpl, threshold)
                if hit is None:
                    break
                cx, cy, score = hit
                hits.append((cx, cy, score))
                y1 = max(0, cy - tpl_h // 2)
                y2 = min(scene_copy.shape[0], cy + tpl_h // 2)
                x1 = max(0, cx - tpl_w // 2)
                x2 = min(scene_copy.shape[1], cx + tpl_w // 2)
                scene_copy[y1:y2, x1:x2] = 0
            if hits:
                ctx.log(f"✓ {fk} 匹配到 {len(hits)} 个", level="hit")
                for j, (x, y, score) in enumerate(hits):
                    ctx.log(f"  [{j + 1}] 坐标 ({x}, {y})，得分 {score:.3f}")
            else:
                ctx.log(f"○ {fk} 未匹配（当前画面无此元素）")
        ctx.log("演练自检完成。")
