# -*- coding: utf-8 -*-
"""活动页共享导航：快捷键打开并等待后，按需切到「日常活动」。"""

from . import calibration
from . import vision
from . import window as win_mod


ACTIVITY_CALIBRATION = {
    "regions": [],
    "templates": [
        ("daily_activity", "日常活动按钮（可选，共享）", "框住活动页的「日常活动」按钮文字和边框"),
    ],
    "watchlist": False,
}


def select_daily(ctx, sleep, task_name="activity"):
    """当前前台窗口内识别并点击一次；未标定/未出现则继续原流程。

    调用方须先发 open_activity 并等页面展开；sleep 使用任务自身的可中断拟人化等待。
    按 task_name 解析本活动覆盖、通用标定及旧 tasks.activity 共享记录，
    始终按最终模板自己的尺寸组还原识别尺度。
    仅实战导航调用，演练不调用。
    """
    if ctx.should_stop():
        return False
    tc = ctx.task_cfg(task_name)
    path = tc.get("templates", {}).get("daily_activity")
    if not path:
        return False
    tpl = vision.load_template(path)
    if tpl is None:
        item = calibration.resolve_item(ctx.cfg, task_name, "templates", "daily_activity")
        source = "通用标定" if item["source"] == "common" else "本活动标定"
        ctx.log(f"日常活动按钮模板加载失败（{source}：{path}），已跳过日常活动导航。", level="warn")
        return False
    rect = ctx.window.rect()
    if rect is None:
        return False
    size = calibration.size_for(ctx.cfg, task_name, "templates", "daily_activity")
    # 导航节奏和阈值仍由已有活动页共享配置管理，活动参数只覆盖模板资产。
    nav_loop = ctx.task_cfg("activity").get("loop", {})
    scene = win_mod.grab_scene(rect, size)
    hit = scene.match(tpl, nav_loop.get("match_threshold", 0.85))
    if hit is None or ctx.should_stop():
        return False
    if ctx.mouse.click(*scene.to_screen(hit[0], hit[1])) is False:
        return False
    ctx.log(f"识别到「日常活动」（{hit[2]:.3f}）→ 点击。", level="hit")
    sleep(nav_loop.get("after_click_wait_sec", 0.35))
    return True
