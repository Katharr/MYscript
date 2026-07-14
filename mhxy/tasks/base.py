# -*- coding: utf-8 -*-
"""
任务基类与注册表。

约定：每个任务继承 Task，实现 run(ctx)，并在 run 的循环里频繁检查 ctx.should_stop()。
任务通过 ctx.log() 输出日志、ctx.window/ctx.mouse 操作游戏，绝不直接引用 GUI。

Task 基类还提供一组「与玩法无关」的纯工具方法（可被停止的等待、帧差判静止、点区域、
存截图、抖动、管理员检测），供所有任务复用，避免每个任务各抄一份。
"""

import time
import ctypes
import datetime
import random

import numpy as np

from ..core import vision
from ..core import window as win_mod
from ..core import rotation
from ..core.config import CAPTURES_DIR

_REGISTRY = {}


def register(cls):
    """类装饰器：把任务登记进注册表。"""
    _REGISTRY[cls.name] = cls
    return cls


def get_task(name):
    return _REGISTRY.get(name)


def all_tasks():
    """按注册顺序返回任务类列表。"""
    return list(_REGISTRY.values())


def dungeon_tasks():
    """按注册顺序返回所有「副本」任务类（is_dungeon=True）。
    刷副本页据此自动列出可选副本——新增副本只需在任务类上标 is_dungeon=True 即自动出现，
    GUI 不用改。以后做「连续刷多个副本」时也以此为候选清单。"""
    return [c for c in _REGISTRY.values() if getattr(c, "is_dungeon", False)]


class Task:
    name = "base"          # 唯一标识（英文，作为 config.tasks 的键）
    title = "基础任务"      # 界面显示名
    description = ""        # 一句话说明
    is_dungeon = False      # True=可在「刷副本」页被当作一个副本选中运行（见 dungeon_tasks）
    # True=支持「日常一条龙·多开每窗口独立链」：实现 make_chain_driver(wctx) 暴露
    #   「每窗口一份 record + 单步推进函数」，与本任务自己的 run()/轮转共用同一套状态机。
    #   需跨窗口协作的任务（如组队副本）保持 False，由一条龙当「集体屏障」处理。
    CHAINS_PER_WINDOW = False

    # 标定向导（calibrate_dialog）按此 spec 驱动渲染。子类覆盖：
    #   {"regions":  [(key, 显示名, 说明), ...],     # 框选区域，写入 tc["regions"][key]
    #    "templates":[(key, 显示名, 说明), ...],     # 框选裁图存模板，写入 tc["templates"][key]
    #    "watchlist": bool}                          # 是否显示「装备清单」卡片（秒装备专用）
    CALIBRATION = {"regions": [], "templates": [], "watchlist": False}

    def run(self, ctx):
        """任务主体。会在后台线程里执行；需自行在循环中检查 ctx.should_stop()。"""
        raise NotImplementedError

    def preflight(self, ctx):
        """启动前自检。返回 (ok: bool, problems: list[str])。默认通过。"""
        return True, []

    # ------------------------------------------------------------------
    # 与玩法无关的共享工具（秒装备/刷副本等都用）
    # ------------------------------------------------------------------
    def _is_admin(self):
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return True  # 非 Windows 或查询失败时不打扰

    def _acquire_target_window(self, ctx):
        """基础特性：把 ctx.window 指向「选择窗口」里选中的目标窗口，并保持有效。

        - 已绑定且窗口仍有效 → 直接返回 True（快路径，不重复枚举）。
        - 否则按 targets 配置重新选择并绑定（单开=选中那个号；多开暂取第一个，
          多号顺序跑后续支持）。找不到任何目标窗口返回 False。

        供有状态任务（运镖/宝图）替代原来每轮 `ctx.window.locate()`（那会自动选最大、
        无法指定号），让它们也走「选择窗口」这条基础路径。"""
        if ctx.window.rect() is not None:
            return True
        wins = ctx.select_windows()
        if not wins:
            return False
        ctx.window = wins[0]
        return ctx.window.rect() is not None

    def _jitter(self, base, ctx):
        r = ctx.cfg.get("humanize", {}).get("interval_jitter", 0.4)
        return max(0.05, base * (1 + random.uniform(-r, r)))

    def _interruptible_sleep(self, ctx, seconds):
        """可被停止打断的等待。"""
        end = time.time() + seconds
        while time.time() < end:
            if ctx.should_stop():
                return
            time.sleep(min(0.05, max(0.0, end - time.time())))

    def _make_rotation(self, ctx, records, step_fn, multi, switch_delay, tick, time_limit=0):
        """把「逐号 activate 切前台 + 非阻塞状态机推进」这套多开轮转包成 rotation.RotationConfig。

        运镖/宝图/秘境共用此辅助：
          · 窗口消失 → skip 不置 done（窗口可能恢复、下轮重试，节流告警）；
          · activate 失败 → 跳过该号本轮、节流告警；
          · time_limit>0 时作为总超时，并打各任务「到点自停」文案（非「降级」）。
        各任务只需传一个 step_fn(rec)（闭包绑定自己的 loop/regions/threshold）。record 须含
        ctx/state/done/dead_logged 字段（推进器用默认 get_ctx/get_state/is_done 读取）。
        让出判据见 core/rotation.py：step 后 state 没变=在等待就让出，监控态盯屏天然不空转。"""
        def on_window_gone(rec):
            if not rec["dead_logged"]:
                rec["ctx"].log("目标窗口不见了，跳过该号（其余号继续）。", level="warn")
                rec["dead_logged"] = True
            return "skip"

        def on_activate_fail(rec):
            if not rec.get("fg_warned"):
                rec["ctx"].log("未能切到前台（系统拒绝焦点抢占），本轮跳过、下轮重试。", level="warn")
                rec["fg_warned"] = True

        def step_once(rec):
            rec["fg_warned"] = False        # 能进来=activate 成功+窗口在 → 重置节流标志
            rec["dead_logged"] = False
            step_fn(rec)

        def between_steps(rec):
            # 每号切前台后、推进前：检测背包满则自动整理（开关在 tasks.organize_bag.auto_organize）。
            # 所有走 _make_rotation 的任务（运镖/宝图/秘境/副本）由此统一获得「背包满自动整理」。
            try:
                rec["ctx"].maybe_auto_organize()
            except Exception as e:
                rec["ctx"].log(f"自动整理背包检测异常（已忽略，继续任务）：{e}", level="warn")

        return rotation.RotationConfig(
            records=records, step_once=step_once,
            should_stop=ctx.should_stop, log=ctx.log,
            on_window_gone=on_window_gone, on_activate_fail=on_activate_fail,
            between_steps=between_steps,
            multi=multi, switch_delay=switch_delay, tick=tick,
            overall_timeout=(time_limit * 60 if time_limit > 0 else 0),
            timeout_msg=(f"已达时间上限 {time_limit} 分钟，停止。" if time_limit > 0 else None),
            jitter_ratio=ctx.cfg.get("humanize", {}).get("interval_jitter", 0.4))

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

    def _save_capture(self, scene, name):
        fname = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_") + str(name) + ".png"
        vision.save_image(str(CAPTURES_DIR / fname), scene)
        return fname

    def _wait_still(self, ctx, rect, min_sec=0.3, max_sec=2.0, stable_diff=1.5, poll=0.06):
        """自适应等画面静止：先等 min_sec，再每隔 poll 截一帧比上一帧，
        两帧平均像素差 < stable_diff 即认为静止、立即返回该帧；
        超过 max_sec 仍在变化则返回最后一帧。被停止返回 None。"""
        self._interruptible_sleep(ctx, min_sec)
        if ctx.should_stop():
            return None
        prev = win_mod.grab(rect)
        deadline = time.time() + max(0.0, max_sec - min_sec)
        while time.time() < deadline:
            if ctx.should_stop():
                return None
            time.sleep(poll)
            cur = win_mod.grab(rect)
            if cur is None:
                return prev
            if self._frame_diff(prev, cur) < stable_diff:
                return cur
            prev = cur
        return prev

    # ------------------------------------------------------------------
    # 各任务共用的识别/点击/轮转工具（秒装备/运镖/宝图/秘境/副本都同构，原各抄一份，现集中于此）
    # 依赖 self.flags（各任务 run() 里 self.flags = self._load_flags(tc) 赋值）。
    # ------------------------------------------------------------------
    # 各任务模板键清单：子类在类上定义 _FLAG_KEYS，_load_flags 据此批量加载。
    _FLAG_KEYS = []

    # 公共标定向导项（区域/模板元信息）。子类 CALIBRATION 直接引用这些列表再追加本任务专有项，
    # 避免每个任务把「主识别区/活动列表区域」等公共项各抄一遍。
    # 注意：大多数模板【键名】是 task 专有的（如 escort_entry），绝不能统一改名——
    # 标定存盘按 templates/tm_<key>.png，改名会让用户已有的标定文件失效。
    # 但「参加」按钮在活动列表里视觉完全一致，抽取为 activity_join 通用一次标定。
    # 「使用」按钮是游戏奖励弹窗的通用按钮（如宝图下一张、消耗品获取通知等）。
    BASE_CALIBRATION_REGIONS = [
        ("scene", "主识别区", "留空=整个窗口当识别区(推荐)；对话框/战斗等标志都在这里找", True),
        ("activity_list", "活动列表区域", "「活动」界面里那片列表，滚轮在此翻找活动条目"),
    ]
    BASE_CALIBRATION_TEMPLATES = [
        ("activity_join", "参加按钮", "活动列表里条目右侧的「参加」按钮，框按钮本身、要独特。各活动共用"),
        ("reward_use", "「使用」按钮", "游戏奖励弹窗里的「使用」按钮（如宝图下一张、消耗品获取通知等）。各任务共用"),
    ]

    def _focus(self, ctx):
        """把游戏窗口切到前台——键盘快捷键(SendInput)只发给有焦点的窗口，发键前必须先激活。"""
        try:
            ctx.window.activate()
        except Exception:
            pass

    def _load_flags(self, tc):
        """按本任务 _FLAG_KEYS 批量加载模板，返回 {key: ndarray|None}。"""
        templates = tc.get("templates", {})
        return {k: vision.load_template(templates.get(k)) if templates.get(k) else None
                for k in self._FLAG_KEYS}

    def _scene_rect(self, ctx, regions):
        region = regions.get("scene")
        return ctx.window.region_to_screen_rect(region) if region else ctx.window.rect()

    def _grab_scene(self, ctx, regions):
        rect = self._scene_rect(ctx, regions)
        return win_mod.grab(rect) if rect else None

    def _present(self, scene, flag_key, threshold):
        tpl = self.flags.get(flag_key)
        if scene is None or tpl is None:
            return False
        return vision.match(scene, tpl, threshold) is not None

    def _match_scene(self, cur, scene_rect, flag_key, threshold):
        """在整张 scene 里匹配 flag_key，命中返回屏幕绝对 (x,y,score)，否则 None。"""
        tpl = self.flags.get(flag_key)
        if cur is None or tpl is None or scene_rect is None:
            return None
        m = vision.match(cur, tpl, threshold)
        if m is None:
            return None
        return (scene_rect[0] + m[0], scene_rect[1] + m[1], m[2])

    def _match_subregion(self, ctx, regions, tpl, threshold, x_frac, y_frac):
        """只在 scene 的比例子区域内匹配 tpl（用于「进入」这类需按位置区分的同款按钮）。
        x_frac/y_frac 为 (起,止) 的 0~1 比例。命中返回屏幕 (x,y,score)，否则 None。"""
        rect = self._scene_rect(ctx, regions)
        if rect is None or tpl is None:
            return None
        scene = win_mod.grab(rect)
        if scene is None:
            return None
        sh, sw = scene.shape[:2]
        x0 = max(0, int(sw * x_frac[0]))
        x1 = min(sw, int(sw * x_frac[1]))
        y0 = max(0, int(sh * y_frac[0]))
        y1 = min(sh, int(sh * y_frac[1]))
        if x1 - x0 < 1 or y1 - y0 < 1:
            return None
        crop = scene[y0:y1, x0:x1]
        m = vision.match(crop, tpl, threshold)
        if m is None:
            return None
        cx, cy, score = m
        return (rect[0] + x0 + cx, rect[1] + y0 + cy, score)

    def _find_join_on_row(self, ctx, list_region, entry_screen_xy, threshold, loop,
                          join_key, entry_key):
        """在条目所在【那张卡片】的右侧条带里匹配「参加」按钮(join_key)。
        命中返回 (screen_x, screen_y, score)，否则 None。
        按行+只取条目右侧、且限制在条目所属卡片列内，抗滚动、抗「一排多张卡片」时
        扫进右邻卡片（活动列表默认两张一排，见 CLAUDE.md 活动列表卡片布局约束）。"""
        join_tpl = self.flags.get(join_key)
        entry_tpl = self.flags.get(entry_key)
        if join_tpl is None:
            ctx.log(f"找「参加」失败：{join_key} 模板未标定。", level="warn")
            return None
        rect = (ctx.window.region_to_screen_rect(list_region)
                if list_region else ctx.window.rect())
        if rect is None:
            return None
        scene = win_mod.grab(rect)
        if scene is None:
            return None
        rx, ry = rect[0], rect[1]
        ex, ey = entry_screen_xy
        row_h = entry_tpl.shape[0] if entry_tpl is not None else 40
        band = max(40, int(row_h * 2))
        sh, sw = scene.shape[:2]
        ey_local = int(ey - ry)
        ex_local = int(ex - rx)
        cols = max(1, int(loop.get("activity_columns", 2)))
        col_w = sw / cols
        col_idx = min(cols - 1, max(0, int(ex_local // col_w)))
        col_right = int(round((col_idx + 1) * col_w))
        y0 = max(0, ey_local - band // 2)
        y1 = min(sh, ey_local + band // 2)
        x0 = max(0, ex_local)
        x1 = min(sw, col_right)
        if y1 - y0 < 1 or x1 - x0 < 1:
            return None
        crop = scene[y0:y1, x0:x1]
        m = vision.match(crop, join_tpl, threshold)
        if m is None:
            return None
        cx, cy, score = m
        return (rx + x0 + cx, ry + y0 + cy, score)

    @staticmethod
    def _goto(rec, state):
        rec["state"] = state
        rec["t_state"] = time.time()

    @staticmethod
    def _state_elapsed(rec):
        return time.time() - rec["t_state"]

    def _resolve_contexts(self, ctx, multi):
        """按选择把目标窗口包成「要轮转的上下文」列表。
        单开→复用主 ctx 并绑到选中的那个窗口；多开→每号一个子上下文(带「号N」标签，日志自动加前缀)。"""
        wins = ctx.select_windows()
        if not wins:
            return []
        if multi:
            return [ctx.make_child(w, f"号{i + 1}") for i, w in enumerate(wins)]
        ctx.window = wins[0]
        return [ctx]
