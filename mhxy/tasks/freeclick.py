# -*- coding: utf-8 -*-
"""
自由点击任务：把用户自己框选下来的若干张截图当模板，**按清单顺序循环识别、认到哪个就点哪个**。

用户拍板的语义（别再自作主张改）：
  · 顺序 = 清单顺序（GUI 里可左键按住拖动调序），**一轮之内每项最多点一次**：认得出的点掉、
    认不出的跳过，一轮过完再从第 1 项重来 —— 不是「只点第一个命中就重扫」。
  · 每点一次都重新截图：点下去的后果（弹窗/切页/列表滚动）会改变画面，后续项必须按新画面判断。
  · 界面只管清单（加图 / 重新标定 / 删除 / 拖动调序），本文件只管跑，不碰 GUI。
  · 安全默认 dry_run=true（只识别打日志、不点击），与其它任务一致。

识别与坐标：走 core/window.ScaledScene（grab_scene + match + to_screen），
即窗口尺寸与标定尺寸不一致时先把画面缩回标定尺度再匹配，阈值按缩放比动态放宽
（见 core/vision.scaled_threshold 的实测依据）——所以换分辨率也不用重标。

多开：单开=只操作「选择窗口」里选中的那个号；多开=逐号轮转（每号切前台后跑一轮清单）。
窗口上下文的构建/切前台/失效重选见 Task 基类 _resolve_contexts/_ensure_contexts/_prepare_window。
"""

from ..core import calib_profiles as calib
from ..core import vision
from ..core import window as win_mod
from .base import Task, register


@register
class FreeClickTask(Task):
    name = "freeclick"
    title = "自由点击"
    description = "按清单顺序循环识别截图并点击"

    # 标定向导用：只暴露「识别区」（留空=整窗检测，推荐）；截图清单在页面里自己管（可拖动调序）。
    CALIBRATION = {
        "regions": [
            ("scene", "识别区", "留空=整窗检测(推荐)", True),
        ],
        "templates": [],
        "watchlist": False,
    }

    @staticmethod
    def selected_items(tc):
        """返回当前选中清单的项目；兼容旧版顶层 items 配置。"""
        lists = tc.get("lists") or []
        selected_id = tc.get("selected_list")
        for entry in lists:
            if entry.get("id") == selected_id:
                return entry.get("items") or []
        if lists:
            return []
        return tc.get("items") or []

    def preflight(self, ctx):
        tc = ctx.task_cfg(self.name)
        problems = []
        items = self.selected_items(tc)
        if not items:
            problems.append("截图清单为空 —— 请先「＋ 框选加截图」")
        for it in items:
            if vision.load_template(it.get("template")) is None:
                problems.append(f"截图丢失：{it.get('template')}（{it.get('name', '?')}）")
        if not ctx.select_windows():
            problems.append(f"没找到/没选中目标窗口（标题含「{ctx.window.title_substr}」）"
                            "，请先打开游戏并在「选择窗口」里选好")
        return (len(problems) == 0), problems

    def run(self, ctx):
        tc = ctx.task_cfg(self.name)
        loop = tc.get("loop") or {}
        regions = tc.get("regions") or {}
        dry_run = tc.get("dry_run", True)
        threshold = float(loop.get("match_threshold", 0.85))
        after_click = float(loop.get("after_click_wait_sec", 0.35))
        round_interval = float(loop.get("round_interval_sec", 0.8))

        items = [(it, vision.load_template(it.get("template"))) for it in self.selected_items(tc)]
        items = [(it, tpl) for it, tpl in items if tpl is not None]
        if not items:
            ctx.log("清单里没有可用的截图，已停止。", level="error")
            return

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
                f"{len(items)} 张截图，阈值 {threshold}，检测区："
                f"{'手动框选' if regions.get('scene') else '整窗'}")
        ctx.log("演练模式（只识别不点击）" if dry_run else "★ 实战模式：命中会真的点击 ★",
                level="warn" if not dry_run else "info")

        rounds = 0
        total_hits = 0
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
                total_hits += self._click_one_round(
                    wctx, items, regions, threshold, after_click, dry_run)
                # 多开：号与号之间留个小间隔，别太机械
                if multi and len(contexts) > 1:
                    self._interruptible_sleep(ctx, self._jitter(switch_delay, ctx))

            rounds += 1
            # 一整轮（清单过完一遍）之间的间隔（带抖动）
            self._interruptible_sleep(ctx, self._jitter(round_interval, ctx))

        ctx.log(f"已停止。共循环 {rounds} 轮，命中 {total_hits} 次。")

    # ------------------------------------------------------------------
    # 单号一轮：截当前画面 → 按清单顺序逐项识别，认到就点（每项最多一次）
    # ------------------------------------------------------------------
    def _click_one_round(self, ctx, items, regions, threshold, after_click, dry_run):
        """返回本轮命中项数。被停止时立即返回。"""
        scene = self._grab_scene(ctx, regions)
        if scene is None:
            self._interruptible_sleep(ctx, 0.4)
            return 0

        hits = 0
        missed = []
        for n, (it, tpl) in enumerate(items, 1):
            if ctx.should_stop():
                return hits
            name = it.get("name", "?")
            m = scene.match(tpl, threshold)
            if m is None:
                missed.append(name)
                continue
            xy = scene.to_screen(m[0], m[1])
            hits += 1
            if dry_run:
                ctx.log(f"★ 命中【第{n}项·{name}】{m[2]:.3f} @ {xy}（演练不点击）", level="hit")
                continue
            ctx.log(f"★ 命中【第{n}项·{name}】{m[2]:.3f} → 点击 {xy}", level="hit")
            ctx.mouse.click(xy[0], xy[1])
            self._interruptible_sleep(ctx, self._jitter(after_click, ctx))
            # 点完画面多半变了，重新截一帧给后面的项用
            scene = self._grab_scene(ctx, regions)
            if scene is None:
                return hits

        self._log_missed(ctx, missed)
        return hits

    def _log_missed(self, ctx, missed):
        """本轮没认出的项汇总成一行——但只在「缺失集合变了」时打印，
        否则每轮都刷同样几行，日志会被淹掉（识别自检时尤其明显）。"""
        sig = tuple(sorted(missed))
        if sig == getattr(ctx, "_fc_missed_sig", None):
            return
        ctx._fc_missed_sig = sig
        if missed:
            ctx.log(f"本轮未出现：{'、'.join(missed)}（其余已命中）")

    # ------------------------------------------------------------------
    # 截图（走 ScaledScene：跨分辨率自动归一化，坐标一步换回屏幕）
    # ------------------------------------------------------------------
    def _calib_size(self, ctx):
        """当前【激活尺寸组】的窗口尺寸 [w,h]；没标定过返回 None（则按 1.0 尺度匹配）。
        真源是 config 顶层 calib_profiles，老配置由 active_size 内部退回 targets.base_size 镜像。"""
        try:
            return calib.active_size(ctx.cfg)
        except Exception:
            return None

    def _grab_scene(self, ctx, regions):
        rect = ctx.detection_rect(regions.get("scene"))
        if rect is None:
            return None
        return win_mod.grab_scene(rect, self._calib_size(ctx))
