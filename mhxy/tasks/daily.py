# -*- coding: utf-8 -*-
"""
日常一条龙：把已有任务（宝图/运镖/秘境降妖）按用户勾选的顺序串起来，一次性跑完。

设计原则（用户拍板）：完全套用每个子任务【已经做好的流程】，本任务只做「串联」——
  · 子任务列表与顺序存 tasks.daily.steps（有序，每项 {task, enabled}），界面可勾选 + 上下调序；
  · 多开/单开、演练/实战、各自的标定与参数，全部沿用各子任务自身的配置（本任务不另设）；
  · 依次实例化每个被勾选的子任务、在同一个 ctx 上调它的 run(ctx)，跑完一个再跑下一个；
  · 每步先跑该子任务的 preflight：不通过就跳过该步（缺标定/缺窗口等），其余步照常往下跑；
  · 全程勤查 should_stop（停止/热键/鼠标甩左上角都能整条龙叫停）；整体时间上限只是安全网。

为什么不含「秒装备」：它是【无限盯市场抢货】、不会自己跑完，串进来会一直卡在那一步、
后面的任务永远轮不到，不属于一条龙这种「跑通即进入下一个」的流程（用户拍板排除）。
"""

import time

from .base import Task, register, get_task

# 可进一条龙的任务（这些都有明确「完成条件」、会自动结束）。秒装备 sniper 不在此列。
CHAINABLE = ["treasure_map", "escort", "secret_realm"]


@register
class DailyTask(Task):
    name = "daily"
    title = "日常一条龙"
    description = "勾选已有任务（宝图/运镖/秘境降妖）按顺序一次跑完；多开/单开与各自演练/实战设置完全沿用"

    # 本任务没有自己的标定（全部沿用各子任务）；calibrate_dialog 据此不渲染任何标定项。
    CALIBRATION = {"regions": [], "templates": [], "watchlist": False}

    # ------------------------------------------------------------------
    def preflight(self, ctx):
        problems = []
        if not self._enabled_steps(ctx):
            problems.append("还没勾选任何任务 —— 请在「日常一条龙」页勾选要串起来跑的任务")
        if not ctx.select_windows():
            problems.append(f"没找到/没选中目标窗口（标题含「{ctx.window.title_substr}」），"
                            "请先打开游戏并在「选择窗口」里选好")
        return (len(problems) == 0), problems

    # ------------------------------------------------------------------
    def run(self, ctx):
        steps = self._enabled_steps(ctx)
        if not steps:
            ctx.log("没有勾选任何任务，已停止。", level="error")
            return

        time_limit = self._time_limit(ctx)
        start_ts = time.time()
        deadline = start_ts + time_limit * 60 if time_limit > 0 else None

        titles = " → ".join(self._title_of(n) for n in steps)
        ctx.log(f"★ 日常一条龙启动：按顺序跑 {len(steps)} 个任务 → {titles} ★", level="warn")
        if time_limit > 0:
            ctx.log(f"整体时间上限 {time_limit:g} 分钟（仅安全网；正常会按各任务自身条件跑完）。")
        ctx.log("多开/单开与各任务的演练/实战、标定、参数，全部沿用各自任务页的设置。")

        done = 0
        for idx, name in enumerate(steps, 1):
            if ctx.should_stop():
                break
            if deadline and time.time() >= deadline:
                ctx.log(f"已达整体时间上限 {time_limit:g} 分钟，停止。", level="warn")
                break

            title = self._title_of(name)
            task_cls = get_task(name)
            if task_cls is None:
                ctx.log(f"[{idx}/{len(steps)}] 未知任务「{name}」，跳过。", level="warn")
                continue
            task = task_cls()

            # 先跑该子任务自己的 preflight：缺标定/缺窗口就跳过这一步，别拖垮整条龙
            ok, probs = task.preflight(ctx)
            if not ok:
                ctx.log(f"[{idx}/{len(steps)}] 跳过「{title}」（未就绪）：" + "；".join(probs), level="warn")
                continue

            ctx.log(f"───── [{idx}/{len(steps)}] 开始「{title}」 ─────", level="hit")
            try:
                task.run(ctx)
            except Exception as e:  # 单个子任务炸了不该带垮整条龙
                ctx.log(f"「{title}」运行异常：{e}，继续下一个。", level="error")
                continue

            if ctx.should_stop():
                ctx.log(f"「{title}」被中断，一条龙停止。", level="warn")
                break
            done += 1
            ctx.log(f"───── [{idx}/{len(steps)}] 「{title}」完成 ─────", level="hit")

        ctx.log(f"日常一条龙结束：完成 {done}/{len(steps)} 个任务，"
                f"用时 {(time.time() - start_ts) / 60:.1f} 分钟。")

    # ------------------------------------------------------------------
    # 配置读取
    # ------------------------------------------------------------------
    @staticmethod
    def _title_of(name):
        cls = get_task(name)
        return cls.title if cls else name

    def _enabled_steps(self, ctx):
        """返回【按存储顺序】、已勾选且可串联的任务名列表。"""
        tc = ctx.task_cfg(self.name)
        out = []
        for step in tc.get("steps", []):
            if not isinstance(step, dict):
                continue
            name = step.get("task")
            if step.get("enabled") and name in CHAINABLE and name not in out:
                out.append(name)
        return out

    def _time_limit(self, ctx):
        tc = ctx.task_cfg(self.name)
        try:
            return float(tc.get("loop", {}).get("time_limit_min", 0) or 0)
        except (TypeError, ValueError):
            return 0.0
