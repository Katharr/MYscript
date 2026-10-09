# -*- coding: utf-8 -*-
"""活动页共享导航：快捷键打开并等待后，按需切到「日常活动」。"""

from . import calib_profiles as calib
from . import vision
from . import window as win_mod


ACTIVITY_CALIBRATION = {
    "regions": [],
    "templates": [
        ("daily_activity", "日常活动按钮（可选，共享）", "框住活动页的「日常活动」按钮文字和边框"),
    ],
    "watchlist": False,
}


def select_daily(ctx, sleep):
    """当前前台窗口内识别并点击一次；未标定/未出现则继续原流程。

    调用方须先发 open_activity 并等页面展开；sleep 使用任务自身的可中断拟人化等待。
    标定在 tasks.activity 共享，按该模板自己的尺寸组还原识别尺度。
    仅实战导航调用，演练不调用。
    """
    if ctx.should_stop():
        return False
    tc = ctx.task_cfg("activity")
    path = tc.get("templates", {}).get("daily_activity")
    tpl = vision.load_template(path) if path else None
    if tpl is None:
        return False
    rect = ctx.window.rect()
    if rect is None:
        return False
    profile = calib.get(ctx.cfg, calib.template_profile(ctx.cfg, "activity", "daily_activity"))
    size = profile["size"] if profile else calib.active_size(ctx.cfg)
    scene = win_mod.grab_scene(rect, size)
    hit = scene.match(tpl, tc.get("loop", {}).get("match_threshold", 0.85))
    if hit is None or ctx.should_stop():
        return False
    if ctx.mouse.click(*scene.to_screen(hit[0], hit[1])) is False:
        return False
    ctx.log(f"识别到「日常活动」（{hit[2]:.3f}）→ 点击。", level="hit")
    sleep(tc.get("loop", {}).get("after_click_wait_sec", 0.35))
    return True
