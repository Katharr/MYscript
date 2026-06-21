# -*- coding: utf-8 -*-
"""
秒装备任务：循环刷新摆摊/市场列表，命中监控清单里的装备就立刻点它→购买→确认。

设计取舍（用户已拍板）：命中即抢，不做 OCR 比价（模板匹配认不了“任意低于X价”）。
所有点击走拟人化鼠标，间隔带抖动、偶尔走神。
"""

import time
import ctypes
import datetime

import numpy as np

from ..core import vision
from ..core import window as win_mod
from ..core.config import CAPTURES_DIR
from .base import Task, register


@register
class SniperTask(Task):
    name = "sniper"
    title = "秒装备"
    description = "盯市场列表，目标装备一出现就秒下单"

    def preflight(self, ctx):
        tc = ctx.task_cfg(self.name)
        problems = []
        regions = tc.get("regions", {})
        if not regions.get("listing"):
            problems.append("『货架/列表区域』未标定 —— 请先做标定")
        if not regions.get("category_button"):
            problems.append("『商品类别按钮』未标定 —— 刷新要靠它进货架")
        if not regions.get("product_entry"):
            problems.append("『商品条目』未标定 —— 刷新要靠它进货架")
        watchlist = tc.get("watchlist", [])
        if not watchlist:
            problems.append("监控清单为空 —— 请先添加要抢的装备")
        for it in watchlist:
            if vision.load_template(it["template"]) is None:
                problems.append(f"模板图丢失：{it['template']}（{it.get('name','?')}）")
        if not ctx.window.locate():
            problems.append(f"没找到游戏窗口（标题含「{ctx.window.title_substr}」），请先打开游戏")
        return (len(problems) == 0), problems

    def run(self, ctx):
        tc = ctx.task_cfg(self.name)
        loop = tc["loop"]
        regions = tc["regions"]
        dry_run = tc.get("dry_run", True)

        templates = [(it, vision.load_template(it["template"])) for it in tc["watchlist"]]
        templates = [(it, tpl) for it, tpl in templates if tpl is not None]

        threshold = loop["match_threshold"]
        refresh_interval = loop["refresh_interval_sec"]
        cooldown = loop["after_buy_cooldown_sec"]
        # 命中后「下单那一下」用更激进的速度倍率（只在抢的瞬间生效，巡航点击仍保持常速拟人化）
        snipe_speed = ctx.cfg.get("humanize", {}).get("snipe_speed", 3.0)
        listing = regions["listing"]
        mouse = ctx.mouse

        if not self._is_admin():
            ctx.log("⚠ 当前非管理员权限：游戏窗口在前台时鼠标可能无法移动/点击（UIPI 拦截）。"
                    "请用『以管理员身份运行』重开。", level="warn")

        ctx.log(f"启动完成：监控 {len(templates)} 件装备，阈值 {threshold}，每轮重进货架间隔 ~{refresh_interval}s")
        ctx.log("演练模式（只识别不下单）" if dry_run else "★ 实战模式：命中会真正下单 ★",
                level="warn" if not dry_run else "info")

        rounds = 0

        while not ctx.should_stop():
            if not ctx.window.locate():
                ctx.log("游戏窗口不见了，2 秒后重试…", level="warn")
                self._interruptible_sleep(ctx, 2.0)
                continue

            mouse.maybe_idle()

            # 刷新 = 重新进货架：点左侧类别 → 点右侧商品条目 → 等货架加载。
            # 货架页面进去后不会自动上新，必须退出重进，所以这一步每轮都做。
            if not self._enter_shelf(ctx, regions):
                self._interruptible_sleep(ctx, self._jitter(refresh_interval, ctx))
                continue

            # 截货架区域并匹配。自适应等加载：画面一静止就识别，不傻等满 shelf_load_wait_sec。
            list_rect = ctx.window.region_to_screen_rect(listing)
            if list_rect is None:
                self._interruptible_sleep(ctx, 0.5)
                continue
            scene = self._wait_shelf_loaded(ctx, list_rect, loop)
            if scene is None:
                continue

            for it, tpl in templates:
                if ctx.should_stop():
                    break
                hit = vision.match(scene, tpl, threshold)
                if hit is None:
                    continue
                cx, cy, score = hit
                screen_xy = (list_rect[0] + cx, list_rect[1] + cy)
                ctx.log(f"★ 命中【{it['name']}】相似度 {score:.3f} @ {screen_xy}", level="hit")
                shot = self._save_capture(scene, it["name"])
                ctx.log(f"  已存命中截图 captures/{shot}")

                if dry_run:
                    ctx.log("  [演练] 不下单。确认无误后到设置里切换为实战。")
                else:
                    self._buy_sequence(ctx, regions, screen_xy, snipe_speed)
                    ctx.log("  已执行购买动作序列（极速）。")
                    self._interruptible_sleep(ctx, cooldown)
                break  # 一轮处理一件即可

            rounds += 1
            # 两轮之间留间隔（带抖动），避免点得太快太机械
            self._interruptible_sleep(ctx, self._jitter(refresh_interval, ctx))

        ctx.log(f"已停止。共循环 {rounds} 轮。")

    # ---- 内部小工具 ----
    def _is_admin(self):
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return True  # 非 Windows 或查询失败时不打扰

    def _jitter(self, base, ctx):
        import random
        r = ctx.cfg.get("humanize", {}).get("interval_jitter", 0.4)
        return max(0.05, base * (1 + random.uniform(-r, r)))

    def _enter_shelf(self, ctx, regions):
        """刷新动作：点左侧类别 → 点右侧商品条目（进货架）。
        等加载交给 _wait_shelf_loaded 自适应处理，这里只负责点击。
        任一步缺标定或被停止则返回 False（主循环会跳过本轮识别）。"""
        cat = regions.get("category_button")
        prod = regions.get("product_entry")
        if not cat or not prod:
            ctx.log("类别/商品条目未标定，无法进货架刷新。", level="warn")
            return False
        if not self._click_region(ctx, cat):       # 选左侧类别（如「奇珍异宝」）
            return False
        ctx.mouse.sleep(0.25, 0.5)                 # 等右侧信息框切到该类别
        if ctx.should_stop():
            return False
        if not self._click_region(ctx, prod):      # 选右侧商品 → 进入它的货架
            return False
        return not ctx.should_stop()

    def _wait_shelf_loaded(self, ctx, list_rect, loop):
        """自适应等货架加载：先等一个最短时间，再每隔一小段截图比上一帧，
        画面一旦静止（两帧几乎无差异）就认为加载完、立即返回该帧用于识别；
        始终不超过 shelf_load_wait_sec（上限/超时）。被停止则返回 None。"""
        min_w = self._jitter(loop.get("shelf_load_min_sec", 0.25), ctx)
        max_w = max(min_w, loop.get("shelf_load_wait_sec", 1.2))
        STABLE_DIFF = 1.5        # 两帧平均像素差低于此即视为画面静止（加载完成）
        POLL = 0.06              # 轮询间隔

        self._interruptible_sleep(ctx, min_w)
        if ctx.should_stop():
            return None
        prev = win_mod.grab(list_rect)
        deadline = time.time() + max(0.0, max_w - min_w)
        while time.time() < deadline:
            if ctx.should_stop():
                return None
            time.sleep(POLL)
            cur = win_mod.grab(list_rect)
            if cur is None:
                return prev
            if self._frame_diff(prev, cur) < STABLE_DIFF:
                return cur       # 画面静止 → 加载完成，立即识别
            prev = cur
        return prev

    @staticmethod
    def _frame_diff(a, b):
        """两帧平均像素绝对差。形状不一致返回大值（视为仍在变化）。"""
        if a is None or b is None or a.shape != b.shape:
            return 999.0
        return float(np.abs(a.astype(np.int16) - b.astype(np.int16)).mean())

    def _click_region(self, ctx, region, speed=None):
        if not region:
            return False
        center = ctx.window.region_center_screen(region)
        if center is None:
            return False
        ctx.mouse.click(center[0], center[1], speed=speed)
        return True

    def _buy_sequence(self, ctx, regions, hit_xy, speed=None):
        """命中后的下单序列。speed 传入『极速』倍率，让这一连串点击尽量快——抢货成败就在这里。"""
        ctx.mouse.click(hit_xy[0], hit_xy[1], speed=speed)      # 点中装备
        self._snipe_sleep(0.18, speed)
        self._click_region(ctx, regions.get("buy_button"), speed=speed)     # 购买
        self._snipe_sleep(0.18, speed)
        self._click_region(ctx, regions.get("confirm_button"), speed=speed) # 确认（可空）

    @staticmethod
    def _snipe_sleep(base, speed):
        """下单中间的极短等待，按极速倍率压缩（仍留一点随机抖动，避免完全等距）。"""
        import random
        spd = max(0.2, float(speed)) if speed else 1.0
        s = base / spd
        time.sleep(max(0.0, s * (1 + random.uniform(-0.2, 0.2))))

    def _save_capture(self, scene, name):
        fname = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_") + str(name) + ".png"
        vision.save_image(str(CAPTURES_DIR / fname), scene)
        return fname

    def _interruptible_sleep(self, ctx, seconds):
        """可被停止打断的等待。"""
        end = time.time() + seconds
        while time.time() < end:
            if ctx.should_stop():
                return
            time.sleep(min(0.05, max(0.0, end - time.time())))
