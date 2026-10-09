# -*- coding: utf-8 -*-
"""背包维护的显式调用边界。

仅由活动轮转和 daily 独立链在成功激活后、连续推进前调用；不接入副本屏障。
节流属于本次编排，每个窗口 Context 独立计时；不修改任务 record、配置或完成条件。
异常不重试物品动作，保留原业务状态并停止本次运行。只尽力关闭本次打开的面板，
不承诺恢复游戏原 UI、监控帧或计时。停止时禁止为了恢复而继续输入。
"""
import time
from contextlib import nullcontext

from . import vision
from . import window as win_mod
from .inventory import InventoryOrganizer
from .input_session import InputSession


class AutoInventoryMaintenance:
    def __init__(self):
        self._last_check = {}

    def check(self, ctx):
        """返回 False 表示本轮不能继续业务；其他路径返回 True。"""
        if ctx.should_stop():
            return False
        ob = ctx.task_cfg("organize_bag")
        if not ob.get("auto_organize"):
            return True
        loop = ob.get("loop", {}) or {}
        now = time.time()
        if now - self._last_check.get(ctx, 0.0) < loop.get("auto_check_interval_sec", 20):
            return True
        self._last_check[ctx] = now
        path = (ob.get("templates", {}) or {}).get("bag_full_icon")
        tpl = vision.load_template(path) if path else None
        if tpl is None:
            return True
        rect = ctx.window.rect()
        if rect is None:
            return False
        scene = win_mod.grab(rect)
        if scene is None or vision.match(scene, tpl, loop.get("match_threshold", 0.85)) is None:
            return True
        if ctx.should_stop() or not ctx.window.activate():
            return False
        ctx.log("检测到背包已满 → 自动整理背包。", level="hit")
        session = getattr(ctx, "input_session", None)
        policy = session.policy("organize_bag") if isinstance(session, InputSession) else nullcontext()
        recovered = True
        try:
            with policy:
                InventoryOrganizer(ctx, ob, dry_run=ob.get("dry_run", True)).organize()
        except Exception as exc:
            ctx.log(f"自动整理背包异常：{exc}；面板恢复不确定，停止任务（不重试物品动作）。", level="error")
            ctx.stop()
            recovered = False
        finally:
            self._last_check[ctx] = time.time()
            if isinstance(session, InputSession):
                session.invalidate()  # 整理画面不能作为原业务的观察证据
        if ctx.should_stop():
            return False
        # 整理可能耗时或失去前台；重新校验，失败让出、不推进原任务。
        return bool(ctx.window.activate()) and recovered
