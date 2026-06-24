# -*- coding: utf-8 -*-
"""
秒装备任务：循环刷新摆摊/市场列表，命中监控清单里的装备就立刻点它→购买→确认。

设计取舍（用户已拍板）：命中即抢，不做 OCR 比价（模板匹配认不了“任意低于X价”）。
所有点击走拟人化鼠标，间隔带抖动、偶尔走神。
"""

import time

from ..core import vision
from ..core import window as win_mod
from .base import Task, register


@register
class SniperTask(Task):
    name = "sniper"
    title = "秒装备"
    description = "盯市场列表，目标装备一出现就秒下单"

    # 标定向导用：区域项即原 REGION_ITEMS；秒装备有「装备清单」卡片，无标志模板。
    CALIBRATION = {
        "regions": [
            ("listing", "货架/列表区域", "留空=整个窗口当检测区(推荐)；想提速/避免误识可框小一点", True),
            ("category_button", "商品类别按钮", "左侧侧边栏里的类别，如「奇珍异宝」——刷新第①步点它"),
            ("product_entry", "商品条目", "右侧信息框里要进的那个商品——刷新第②步点它进货架"),
            ("buy_button", "购买按钮", "选中摊位后出现的「购买」按钮"),
            ("confirm_button", "确认购买按钮", "二次确认弹窗的按钮，没有可不标"),
        ],
        "templates": [],
        "watchlist": True,
    }

    def preflight(self, ctx):
        tc = ctx.task_cfg(self.name)
        problems = []
        regions = tc.get("regions", {})
        # listing 留空=整窗检测，不再强制标定
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
        if not ctx.select_windows():
            problems.append(f"没找到/没选中目标窗口（标题含「{ctx.window.title_substr}」）"
                            "，请先打开游戏并在「选择窗口」里选好")
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
        listing = regions.get("listing")        # 空=整窗检测
        # 把每轮要用的参数打包，传给 _snipe_one_round（避免一长串形参）
        pkg = (loop, regions, listing, templates, threshold, cooldown, snipe_speed, dry_run)

        multi = ctx.cfg.get("targets", {}).get("multi", False)
        switch_delay = ctx.cfg.get("targets", {}).get("switch_delay_sec", 0.15)

        if not self._is_admin():
            ctx.log("⚠ 当前非管理员权限：游戏窗口在前台时鼠标可能无法移动/点击（UIPI 拦截）。"
                    "请用『以管理员身份运行』重开。", level="warn")

        contexts = self._resolve_contexts(ctx, multi)
        if not contexts:
            ctx.log("没找到/没选中目标窗口，已停止。", level="error")
            return

        ctx.log(f"启动完成：{('多开轮转 ' + str(len(contexts)) + ' 个号') if multi else '单号'}，"
                f"监控 {len(templates)} 件装备，阈值 {threshold}，检测区："
                f"{'整窗' if not listing else '手动框选'}")
        ctx.log("演练模式（只识别不下单）" if dry_run else "★ 实战模式：命中会真正下单 ★",
                level="warn" if not dry_run else "info")

        rounds = 0
        while not ctx.should_stop():
            # 窗口可能被关/移动：多开时若有窗口失效就重新枚举选择
            contexts = self._ensure_contexts(ctx, contexts, multi)
            if not contexts:
                self._interruptible_sleep(ctx, 2.0)
                continue

            for wctx in contexts:
                if ctx.should_stop():
                    break
                if not self._prepare_window(wctx, multi):
                    continue
                wctx.mouse.maybe_idle()
                self._snipe_one_round(wctx, pkg, refresh_interval)
                # 多开：号与号之间留个小间隔，别太机械
                if multi and len(contexts) > 1:
                    self._interruptible_sleep(ctx, self._jitter(switch_delay, ctx))

            rounds += 1
            # 一整轮（所有号过一遍）之间留间隔（带抖动）
            self._interruptible_sleep(ctx, self._jitter(refresh_interval, ctx))

        ctx.log(f"已停止。共循环 {rounds} 轮。")

    # ---- 多开轮转：窗口上下文的构建与维护 ----
    def _resolve_contexts(self, ctx, multi):
        """按选择把目标窗口包成「要轮转的上下文」列表。
        单开→复用主 ctx 并把它绑到选中的那个窗口；多开→每个号一个子上下文(带「号N」标签)。"""
        wins = ctx.select_windows()
        if not wins:
            return []
        if multi:
            return [ctx.make_child(w, f"号{i + 1}") for i, w in enumerate(wins)]
        ctx.window = wins[0]        # 单开：直接操作选中的那个窗口（不再每轮 locate 选最大）
        return [ctx]

    def _ensure_contexts(self, ctx, contexts, multi):
        """每轮开头校验窗口是否还在；任一失效（被关/最小化）就重新枚举选择。"""
        if contexts and all(c.window.rect() is not None for c in contexts):
            return contexts
        fresh = self._resolve_contexts(ctx, multi)
        if not fresh:
            ctx.log("暂时没检测到目标窗口，等待…", level="warn")
        elif len(fresh) != len(contexts):
            ctx.log(f"目标窗口数变化：现 {len(fresh)} 个。", level="info")
        return fresh

    def _prepare_window(self, wctx, multi):
        """操作某个号前的准备：校验窗口有效，并把它切到前台，确保点击落在这个号身上。"""
        if wctx.window.rect() is None:
            return False
        if multi:
            # 多开必须切前台，避免点击穿透/点错号。切前台失败（被系统拒绝焦点抢占）就跳过该号本轮、下轮重试。
            if not wctx.window.activate():
                return False
            if wctx.should_stop():
                return False
        return True

    def _snipe_one_round(self, ctx, pkg, refresh_interval):
        """对单个号跑「一轮」：重进货架 → 等加载 → 识别 → 命中下单。"""
        loop, regions, listing, templates, threshold, cooldown, snipe_speed, dry_run = pkg
        # 刷新 = 重新进货架：点左侧类别 → 点右侧商品条目 → 等货架加载。
        # 货架页面进去后不会自动上新，必须退出重进，所以这一步每轮都做。
        if not self._enter_shelf(ctx, regions):
            self._interruptible_sleep(ctx, self._jitter(refresh_interval, ctx))
            return

        # 截检测区并匹配（listing 空=整窗）。自适应等加载：画面一静止就识别。
        list_rect = ctx.detection_rect(listing)
        if list_rect is None:
            self._interruptible_sleep(ctx, 0.5)
            return
        scene = self._wait_shelf_loaded(ctx, list_rect, loop)
        if scene is None:
            return

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

    # ---- 内部小工具（_is_admin/_jitter/_frame_diff/_click_region/_save_capture/_interruptible_sleep 已上移 Task 基类）----
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
