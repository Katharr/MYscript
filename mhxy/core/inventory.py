# -*- coding: utf-8 -*-
"""
整理背包（可复用的「翻包裹 + 逐物执行使用/丢弃/出售」编排）。

与组队一样是【跨任务的通用能力】：任何任务流程都可 InventoryOrganizer(ctx, ob_cfg, dry_run).organize()
穿插调用（如刷完副本顺手清背包）。本模块与 GUI、与具体玩法解耦，只依赖 ctx（window/mouse/cfg/log/stop）
+ core 的 vision/scan/window；core 绝不反向依赖 tasks。

原理（截屏+模板匹配+拟人化点击，不读内存）：
  ① 快捷键 open_bag 打开包裹；
  ② 走通用 scan.scroll_search 从顶向下逐屏翻包裹（先滚到顶、帧差判到底，见 core/scan.py）；
  ③ 每屏对【用户标定的物品图】逐个模板匹配，取最靠上的一个命中物品，按它配置的动作执行：
       use(使用)/discard(丢弃)/sell(出售)——点中物品（右键/左键/双击，open_item_click 配置）弹出操作菜单
       → 点对应动作按钮模板 → 若出现确认弹窗(confirm_button)则点「确定」→ 处理完该屏即向下滚一屏继续；
  ④ 一遍 = 一次完整 top→bottom 扫描；可配 passes 多遍兜底（丢/卖后列表会变短，位置变化保证覆盖）。

每屏只处理「最靠上的一个」命中物品再滚动，避免操作菜单遮挡列表导致连锁误点；多遍+滚动后位置变化保证覆盖。
对「使用」这类不一定消失的物品，靠 passes 上限保证一定终止（不会死循环）。
dry_run：只识别并打日志（物品名+计划动作），不点任何动作、不丢不卖，作为安全自检。
"""

import time
import random

from . import vision
from . import window as win_mod
from . import scan

_ACTION_LABEL = {"use": "使用", "discard": "丢弃", "sell": "出售"}
_ACTION_TPL = {"use": "use_button", "discard": "discard_button", "sell": "sell_button"}


class InventoryOrganizer:
    """整理背包编排器。给 ctx（已切到前台的单窗口上下文）、tasks.organize_bag 配置块、dry_run。
    调 organize() 在该号背包上一气呵成跑完（与滚轮查找同原则）。返回 (handled, reason)。"""

    def __init__(self, ctx, ob_cfg, dry_run=False):
        self.ctx = ctx
        self.cfg = ctx.cfg
        self.ob = ob_cfg or {}
        self.dry_run = dry_run
        self.loop = self.ob.get("loop", {})
        self.regions = self.ob.get("regions", {})
        self.threshold = self.loop.get("match_threshold", 0.85)
        items = [it for it in self.ob.get("items", []) if it.get("template")]
        self._item_tpls = [(it, vision.load_template(it.get("template"))) for it in items]
        self._item_tpls = [(it, t) for it, t in self._item_tpls if t is not None]
        self._btn = {k: (vision.load_template(self.ob.get("templates", {}).get(k))
                         if self.ob.get("templates", {}).get(k) else None)
                     for k in ("use_button", "discard_button", "sell_button", "confirm_button")}

    def _jitter(self, base):
        r = self.cfg.get("humanize", {}).get("interval_jitter", 0.4)
        return max(0.05, base * (1 + random.uniform(-r, r)))

    def _sleep(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            if self.ctx.should_stop():
                return
            time.sleep(min(0.05, max(0.0, end - time.time())))

    def _region_rect(self):
        region = self.regions.get("bag_list")
        return self.ctx.window.region_to_screen_rect(region) if region else self.ctx.window.rect()

    def organize(self):
        ctx = self.ctx
        if not self._item_tpls:
            ctx.log("整理背包：没有可整理的物品（请先在「管理物品」框选添加），跳过。", level="warn")
            return 0, "no_items"
        if not ctx.send_hotkey("open_bag"):
            ctx.log("整理背包：打不开背包（open_bag 未配置），跳过。", level="error")
            return 0, "no_hotkey"
        self._sleep(self._jitter(0.6))

        passes = max(1, int(self.loop.get("passes", 1)))
        handled = 0
        for p in range(passes):
            if ctx.should_stop():
                break
            ctx.log(f"整理背包：第 {p + 1}/{passes} 遍扫描…")
            n = self._sweep_once()
            handled += n
            if n == 0:
                ctx.log("整理背包：本遍未发现可处理物品，提前结束。")
                break
        ctx.send_hotkey("close_panel")
        if self.dry_run:
            ctx.log(f"整理背包·演练完成：共识别到 {handled} 次可处理物品（未执行任何动作）。", level="hit")
        else:
            ctx.log(f"整理背包完成：共处理 {handled} 件物品。", level="hit")
        return handled, "done"

    def _sweep_once(self):
        """一次完整 top→bottom 扫描；每屏处理最靠上的一个命中物品（probe 恒返回 SCROLL → 扫到底）。"""
        count = {"n": 0}

        def probe(scene, rect):
            if scene is None:
                return scan.SCROLL, None
            best = None   # (y_local, it, (sx, sy, score))
            for it, tpl in self._item_tpls:
                m = vision.match(scene, tpl, self.threshold)
                if m is None:
                    continue
                if best is None or m[1] < best[0]:
                    best = (m[1], it, (rect[0] + m[0], rect[1] + m[1], m[2]))
            if best is None:
                return scan.SCROLL, None
            _, it, (sx, sy, score) = best
            action = it.get("action", "use")
            label = _ACTION_LABEL.get(action, action)
            if self.dry_run:
                self.ctx.log(f"识别到物品『{it.get('name', '?')}』（{score:.2f}）→ 计划动作：{label}（演练不执行）。",
                             level="hit")
            else:
                self._do_action(it, action, label, sx, sy, score)
            count["n"] += 1
            return scan.SCROLL, None

        scan.scroll_search(
            grab_rect=self._region_rect,
            probe=probe,
            mouse=self.ctx.mouse,
            should_stop=self.ctx.should_stop,
            sleep=lambda s: self._sleep(self._jitter(s)),
            scroll_step=self.loop.get("scroll_step", -3),
            max_tries=max(1, self.loop.get("scroll_max_tries", 30)),
            settle_sec=self.loop.get("scroll_settle_sec", 0.35),
            reset_to_top=self.loop.get("scroll_reset_top", True),
            end_diff=self.loop.get("scroll_end_diff", 2.0),
            reset_max=self.loop.get("scroll_reset_max", 20),
            log=self.ctx.log,
            label="背包",
        )
        return count["n"]

    def _do_action(self, it, action, label, sx, sy, score):
        ctx = self.ctx
        how = self.loop.get("open_item_click", "right")
        if how == "left":
            ctx.mouse.click(sx, sy)
        elif how == "double":
            ctx.mouse.double_click(sx, sy)
        else:
            ctx.mouse.right_click(sx, sy)
        ctx.log(f"物品『{it.get('name', '?')}』（{score:.2f}）→ {label}：已打开操作菜单。", level="hit")
        self._sleep(self._jitter(self.loop.get("action_settle_sec", 0.4)))
        btn = self._find_full(_ACTION_TPL.get(action))
        if btn is None:
            ctx.log(f"没找到「{label}」按钮模板，跳过该物品（请检查标定）。", level="warn")
            ctx.send_hotkey("close_panel")
            return
        ctx.mouse.click(btn[0], btn[1])
        self._sleep(self._jitter(0.4))
        # 丢弃/出售是破坏性动作，弹「确定」就点；「使用」不自动点确认（避免误确认未知二次弹窗）。
        if action in ("discard", "sell"):
            self._confirm_if_present()
        # 收尾统一关掉可能残留的菜单/确认框，回到干净列表态再继续滚动：
        # 否则点击会落在残留模态上被吞/点歪，残留窗口也会拉高帧差、干扰 scroll_search 的「帧差判到底」。
        ctx.send_hotkey("close_panel")
        self._sleep(self._jitter(0.3))

    def _confirm_if_present(self):
        if self._btn.get("confirm_button") is None:
            return
        deadline = time.time() + self.loop.get("confirm_timeout_sec", 6)
        while time.time() < deadline and not self.ctx.should_stop():
            hit = self._find_full("confirm_button")
            if hit is not None:
                self.ctx.mouse.click(hit[0], hit[1])
                self.ctx.log(f"点「确定」确认（{hit[2]:.2f}）。", level="hit")
                return
            self._sleep(self._jitter(0.3))
        # 确认框确实弹了但没匹配到「确定」：告警，靠 _do_action 收尾的 close_panel 关掉模态，不留模态继续滚。
        self.ctx.log("等「确定」确认超时（没匹配到确认按钮），将关菜单跳过该物品（请检查 confirm_button 标定）。",
                     level="warn")

    def _find_full(self, tpl_key):
        """整窗找按钮模板（菜单位置不定）。命中返回屏幕 (x,y,score)，否则 None。"""
        tpl = self._btn.get(tpl_key)
        rect = self.ctx.window.rect()
        if tpl is None or rect is None:
            return None
        scene = win_mod.grab(rect)
        if scene is None:
            return None
        m = vision.match(scene, tpl, self.threshold)
        if m is None:
            return None
        return (rect[0] + m[0], rect[1] + m[1], m[2])
