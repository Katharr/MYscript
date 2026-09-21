# -*- coding: utf-8 -*-
"""
整理背包（通用能力的可运行封装）。

整理背包是跨任务的共享能力（像组队），核心在 core/inventory.InventoryOrganizer；本任务把它封装成
可在「通用 / 工具」页一键运行的动作：对所选窗口（单开=1 个号；多开=逐号 activate 后各自整理）依次执行
「翻包裹 + 逐物使用/丢弃/出售」。每个号的背包整理是自带内循环、一气呵成式的扫描（与滚轮查找同原则：
在同一个号上跑完再切下一个），故多开走简单的「逐号 activate→organize」循环，不需要非阻塞轮转。

配置/标定都放共享命名空间 tasks.organize_bag（区域 bag_list、动作按钮模板、物品清单 items）。
"""

from ..core import vision
from ..core.inventory import InventoryOrganizer, required_templates
from .base import Task, register

_NS = "organize_bag"


@register
class OrganizeBagTask(Task):
    name = "organize_bag"
    title = "整理背包"
    description = "翻包裹找到标定的物品，按设定逐个使用/丢弃/出售（跨任务共享能力，可单独一键运行）"

    CALIBRATION = {
        "regions": [
            ("bag_list", "背包列表区", "滚轮在此翻找物品；可留空=整窗", True),
        ],
        "templates": [
            ("discard_button", "「丢弃」按钮", "操作菜单里的「丢弃」"),
            ("more_button", "「更多」按钮", "物品详情面板里的「更多」，没有可不标"),
            ("shop_sell_button", "「商会出售」按钮", "详情/更多里的「商会出售」"),
            ("sell_full_button", "出售窗「满」按钮", "把出售数量设到最大"),
            ("sell_confirm_button", "出售窗「出售」按钮", "出售弹窗里最终确认"),
            ("stall_sell_button", "「摆摊出售」按钮", "详情/更多里的「摆摊出售」"),
            ("stall_shelf_button", "「本服上架」按钮", "摆摊出售弹窗里的「本服上架」"),
            ("confirm_button", "「确定」按钮", "丢弃弹出的确认"),
            ("sort_button", "游戏「整理」按钮", "游戏自带的「整理」，没标=不点"),
            ("bag_full_icon", "背包「满」图标", "背包满时常驻的「满」标志，自动整理靠它"),
        ],
        "watchlist": False,
    }

    def preflight(self, ctx):
        problems = []
        tc = ctx.task_cfg(_NS)
        items = [it for it in tc.get("items", []) if it.get("template")]
        if not items:
            problems.append("还没有要整理的物品 —— 请在「管理物品」里框选添加并设动作")
        for it in items:
            if vision.load_template(it.get("template")) is None:
                problems.append(f"物品『{it.get('name', '?')}』的图丢失，请重新框选")
        if not ctx.hotkeys.get("open_bag"):
            problems.append("缺快捷键 open_bag（如 alt+e）—— 请在设置里填")
        templates = tc.get("templates", {})
        used = {it.get("action", "use") for it in items}
        seen, missing = set(), []
        for act in used:
            for key, lbl in required_templates(act):
                if not templates.get(key) and key not in seen:
                    seen.add(key)
                    missing.append(f"「{lbl}」")
        if missing:
            problems.append("以下动作用到的按钮模板还没标定：" + "、".join(missing)
                            + " —— 请点「标定（整理背包）」框选")
        if not ctx.select_windows():
            problems.append("没选到任何目标窗口 —— 请先「选择窗口」")
        return (len(problems) == 0), problems

    def run(self, ctx):
        tc = ctx.task_cfg(_NS)
        dry_run = tc.get("dry_run", True)
        wins = ctx.select_windows()
        if not wins:
            ctx.log("没选到任何目标窗口，已停止。", level="error")
            return
        if not self._is_admin():
            ctx.log("⚠ 当前非管理员：游戏前台时鼠标/键盘注入可能被拦截，建议以管理员重开。", level="warn")
        multi = ctx.cfg.get("targets", {}).get("multi", False) and len(wins) > 1
        if dry_run:
            ctx.log("整理背包·演练：只识别物品+打日志，不执行任何动作。", level="warn")
        total = 0
        for i, w in enumerate(wins):
            if ctx.should_stop():
                break
            label = f"号{i + 1}" if multi else None
            child = ctx.make_child(w, label)
            # 单开/多开都先切前台并校验（约束9铁律）：activate 失败=没真正到前台，绝不在后台号瞎点，跳过。
            if not w.activate():
                child.log("切前台失败（系统拒绝焦点抢占），跳过该号。", level="warn")
                continue
            self._interruptible_sleep(child, self._jitter(0.3, child))
            # 单号整理异常（窗口中途失效、模板尺寸异常等）只跳过该号，不连累同批其余号。
            try:
                handled, _reason = InventoryOrganizer(child, tc, dry_run=dry_run).organize()
                total += handled
            except Exception as e:
                child.log(f"该号整理异常，跳过：{e}", level="error")
                continue
        ctx.log(f"★ 整理背包结束：共处理/识别 {total} 件。", level="hit")
