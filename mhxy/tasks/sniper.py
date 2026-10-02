# -*- coding: utf-8 -*-
"""当前商品页购买：商品模板命中即买，成功提示确认计数，Alt+A 开关刷新。

每窗口独立额度；购买结果等待期间保持该窗口前台，避免多开漏掉短暂提示。
旧提示必须先消失才允许再次购买；成功后仅检测一次干扰叉叉，失败刷新重试。
"""
import time

from ..core import vision, rotation
from ..core import window as win_mod
from ..core.input import vk_of
from .base import Task, register


@register
class SniperTask(Task):
    name = "sniper"
    title = "秒装备"
    description = "当前商品页刷新购买，买够即停"
    CALIBRATION = {
        "regions": [
            ("listing", "商品识别区域", "框商品列表；留空用整窗", True),
            ("purchase", "购买按钮识别区域", "框两次购买按钮所在区域；留空用整窗", True),
            ("success", "成功提示识别区域", "框悬浮提示出现的位置；留空用整窗", True),
        ],
        "templates": [
            ("sniper_product", "商品模板", "只框商品图标或名称，不含价格"),
            ("sniper_buy", "第一次购买按钮模板", "只框第一次购买按钮文字或按钮本体"),
            ("sniper_buy_confirm", "第二次购买按钮模板", "只框第二次购买按钮文字或按钮本体"),
            ("sniper_interference_close", "干扰关闭叉叉模板", "只框干扰弹窗右上角叉叉"),
            ("sniper_success", "购买成功提示", "只框能确认购买成功的固定内容"),
        ],
        "watchlist": False,
    }

    def __init__(self):
        self.progress = ()

    def preflight(self, ctx):
        tc = ctx.task_cfg(self.name)
        problems = []
        count = tc.get("target_count", 1)
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            problems.append("购买数量必须是正整数")
        keys = ctx.hotkeys.get("open_shop", [])
        if not keys or any(vk_of(k) is None for k in keys):
            problems.append("商城快捷键未配置或无效")
        if not tc.get("regions", {}).get("purchase"):
            # 留空=整窗匹配，允许按钮位置变化。
            pass
        for key, label in (("sniper_product", "商品模板"),
                           ("sniper_buy", "第一次购买按钮模板"),
                           ("sniper_buy_confirm", "第二次购买按钮模板"),
                           ("sniper_interference_close", "干扰关闭叉叉模板"),
                           ("sniper_success", "购买成功提示")):
            path = tc.get("templates", {}).get(key)
            if not path or vision.load_template(path) is None:
                problems.append(f"『{label}』未标定或模板图丢失")
        if not ctx.select_windows():
            problems.append("没找到/没选中目标窗口")
        return not problems, problems

    def _publish(self, records, target):
        self.progress = tuple((r["label"], r["count"], target, r["state"]) for r in records)

    def run(self, ctx):
        tc = ctx.task_cfg(self.name)
        multi = ctx.cfg.get("targets", {}).get("multi", False)
        contexts = self._resolve_contexts(ctx, multi)
        if not contexts:
            ctx.log("没找到/没选中目标窗口，已停止。", level="error")
            return
        target = tc["target_count"]
        records = [{"ctx": c, "label": getattr(c, "label", "") or "当前窗口",
                    "state": "识别中", "count": 0, "done": False,
                    "dead_logged": False, "ready_at": 0, "clear_deadline": 0,
                    "dry_logged": False} for c in contexts]
        product = vision.load_template(tc["templates"]["sniper_product"])
        buy_button = vision.load_template(tc["templates"]["sniper_buy"])
        buy_confirm = vision.load_template(tc["templates"]["sniper_buy_confirm"])
        interference_close = vision.load_template(tc["templates"]["sniper_interference_close"])
        success = vision.load_template(tc["templates"]["sniper_success"])
        dry = tc.get("dry_run", True)
        shop_key = "+".join(ctx.hotkeys.get("open_shop", [])).upper()
        ctx.log(f"启动秒装备：每窗口目标 {target} 件，商城快捷键 {shop_key}。")
        ctx.log("演练模式：只识别，不点击、不刷新、不计数。" if dry else "★ 实战模式：命中会真正购买 ★",
                level="info" if dry else "warn")

        self._notify_progress = lambda: self._publish(records, target)

        def step(rec):
            try:
                self._step(rec, tc, product, buy_button, buy_confirm, interference_close, success)
            except Exception:
                rec["state"] = "异常停止"
                raise
            finally:
                self._publish(records, target)

        self._publish(records, target)
        rc = self._make_rotation(ctx, records, step, multi,
                                 ctx.cfg.get("targets", {}).get("switch_delay_sec", 0.15),
                                 tc["loop"].get("tick_interval_sec", 0.06))
        # 商品页必须保持人工选定状态，禁止轮转钩子自动打开背包干扰。
        rc.between_steps = lambda rec: None
        rotation.run_rotation(rc)
        if ctx.should_stop():
            for rec in records:
                if not rec["done"] and rec["state"] != "结果未确认":
                    rec["state"] = "已停止"
            self._publish(records, target)
            ctx.log("秒装备已停止。")
        else:
            ctx.log("所有窗口已完成购买。")

    def _match(self, ctx, region, tpl, threshold):
        rect = ctx.detection_rect(region)
        if rect is None:
            raise RuntimeError("目标窗口不可用")
        scene = win_mod.grab(rect)
        if scene is None:
            raise RuntimeError("截图失败，已停止以避免重复购买")
        return vision.match(scene, tpl, threshold), rect

    def _close_interference(self, ctx, tc, tpl, threshold, speed):
        """发现干扰弹窗就立即点叉；返回是否处理过。"""
        if tpl is None:
            return False
        hit, rect = self._match(ctx, tc["regions"].get("purchase"), tpl, threshold)
        if hit is None:
            return False
        if tc.get("dry_run", True):
            ctx.log("[演练] 匹配到干扰关闭叉叉，不点击。", level="hit")
            return False
        ctx.mouse.click(rect[0] + hit[0], rect[1] + hit[1], speed=speed)
        return True

    def _refresh_after_failure(self, rec, tc):
        """购买结果超时：关开商城回到当前商品页，下一轮继续抢。"""
        ctx = rec["ctx"]
        if not ctx.send_hotkey("open_shop"):
            raise RuntimeError("发送商城快捷键失败")
        rec["state"] = "购买失败，刷新中"
        rec["ready_at"] = time.monotonic() + self._jitter(
            tc["loop"].get("shop_close_wait_sec", 0.25), ctx)

    def _step(self, rec, tc, product, buy_button, buy_confirm, interference_close, success):
        ctx = rec["ctx"]
        if ctx.should_stop() or rec["done"]:
            return
        loop, regions = tc["loop"], tc["regions"]
        threshold = loop.get("match_threshold", 0.85)
        now = time.monotonic()
        if now < rec["ready_at"]:
            return
        state = rec["state"]
        if state == "购买失败，刷新中":
            if not ctx.send_hotkey("open_shop"):
                raise RuntimeError("发送商城快捷键失败")
            rec["state"] = "等待商品页"
            rec["ready_at"] = now + self._jitter(loop.get("shelf_load_wait_sec", 0.6), ctx)
            return
        if state == "等待关闭":
            if not ctx.send_hotkey("open_shop"):
                raise RuntimeError("发送商城快捷键失败")
            rec["state"] = "等待商品页"
            rec["ready_at"] = now + self._jitter(loop.get("shelf_load_wait_sec", 0.6), ctx)
            return
        if state == "等待商品页":
            rec["state"] = "识别中"
            return

        speed = ctx.cfg.get("humanize", {}).get("snipe_speed", 3.0)
        old_hit, _ = self._match(ctx, regions.get("success"), success, threshold)
        if old_hit is not None:
            if not rec["clear_deadline"]:
                rec["clear_deadline"] = now + loop.get("success_clear_timeout_sec", 5.0)
            rec["state"] = "等待旧提示消失"
            if now >= rec["clear_deadline"] and not tc.get("dry_run", True):
                rec["state"] = "购买失败，刷新重试"
                ctx.log("成功提示持续存在，刷新商城重试。", level="warn")
                self._refresh_after_failure(rec, tc)
            return
        rec["clear_deadline"] = 0
        rec["state"] = "识别中"
        hit, rect = self._match(ctx, regions.get("listing"), product, threshold)
        if tc.get("dry_run", True):
            rec["state"] = "演练：商品命中" if hit else "演练：未匹配"
            if hit and not rec["dry_logged"]:
                ctx.log(f"[演练] 商品模板命中，相似度 {hit[2]:.3f}；不下单。", level="hit")
            rec["dry_logged"] = hit is not None
            rec["ready_at"] = now + 0.2
            return
        if hit is None:
            ctx.mouse.maybe_idle()
            if ctx.should_stop():
                return
            if not ctx.send_hotkey("open_shop"):
                raise RuntimeError("发送商城快捷键失败")
            rec["state"] = "等待关闭"
            rec["ready_at"] = time.monotonic() + self._jitter(loop.get("shop_close_wait_sec", 0.25), ctx)
            return

        speed = ctx.cfg.get("humanize", {}).get("snipe_speed", 3.0)
        ctx.mouse.click(rect[0] + hit[0], rect[1] + hit[1], speed=speed)
        if ctx.should_stop():
            return

        # 选中商品后立刻匹配第一次购买按钮，不用固定坐标。
        speed = ctx.cfg.get("humanize", {}).get("snipe_speed", 3.0)
        first_buy, buy_rect = self._match(ctx, regions.get("purchase"), buy_button, threshold)
        if first_buy is None:
            rec["state"] = "第一次购买按钮未匹配，刷新重试"
            ctx.log("商品已选中但未匹配到第一次购买按钮，刷新重试。", level="warn")
            self._refresh_after_failure(rec, tc)
            return
        ctx.mouse.click(buy_rect[0] + first_buy[0], buy_rect[1] + first_buy[1], speed=speed)

        # 第一次购买点击后先给弹窗极短加载时间，再高频重试第二次购买按钮。
        self._interruptible_sleep(ctx, loop.get("second_buy_load_wait_sec", 0.08))
        second_deadline = time.monotonic() + loop.get("second_buy_timeout_sec", 1.0)
        second_buy = None
        confirm_rect = None
        while not ctx.should_stop() and time.monotonic() < second_deadline:
            second_buy, confirm_rect = self._match(ctx, regions.get("purchase"), buy_confirm, threshold)
            if second_buy is not None:
                break
            self._interruptible_sleep(ctx, loop.get("second_buy_retry_interval_sec", 0.02))
        if second_buy is None:
            rec["state"] = "第二次购买按钮未匹配，刷新重试"
            ctx.log("第一次购买已点击，重试后仍未匹配到第二次购买按钮，刷新重试。", level="warn")
            self._refresh_after_failure(rec, tc)
            return
        ctx.mouse.click(confirm_rect[0] + second_buy[0], confirm_rect[1] + second_buy[1], speed=speed)

        rec["state"] = "等待购买结果"
        notify = getattr(self, "_notify_progress", None)
        if notify:
            notify()
        deadline = time.monotonic() + loop.get("purchase_timeout_sec", 2.0)
        while not ctx.should_stop():
            hit, _ = self._match(ctx, regions.get("success"), success, threshold)
            if hit is not None:
                rec["count"] += 1
                ctx.log(f"购买成功：{rec['count']} / {tc['target_count']}", level="hit")
                # 每次新购买成功只检查一次；旧提示与失败轮次不触发。
                self._close_interference(ctx, tc, interference_close, threshold, speed)
                rec["done"] = rec["count"] >= tc["target_count"]
                rec["state"] = "已完成" if rec["done"] else "等待旧提示消失"
                rec["clear_deadline"] = time.monotonic() + loop.get("success_clear_timeout_sec", 5.0)
                return
            if time.monotonic() >= deadline:
                rec["state"] = "购买失败，刷新重试"
                ctx.log("3秒内未匹配到购买成功提示，视为购买失败，刷新重试。", level="warn")
                self._refresh_after_failure(rec, tc)
                return
            self._interruptible_sleep(ctx, loop.get("tick_interval_sec", 0.06))
