# -*- coding: utf-8 -*-
"""
刷副本 · 宝图任务（一次性、两阶段状态机）。

游戏自带「自动战斗」全托管，脚本只做导航 + 状态监控 + 关键点击：

阶段 A 收图：
  复位(快捷键) → 开「活动」→ 滚轮找「宝图任务」点它 → 传送+自动寻路到 NPC 对话
  → 弹框选「听听无妨」→ 自动寻宝(自动战斗) → 所有藏宝图拿完后人物站着不动(帧差判静止=收集完成)。
阶段 B 挖宝：
  开背包(快捷键) → 滚轮找藏宝图 → 双击用 → 自动传送挖宝 → 挖完游戏弹「下一张使用」按钮 → 点它
  → 循环到不再弹 → 再开背包确认无图 → 结束。

导航靠 ctx.send_hotkey(动作名)（键位在 config.hotkeys，用户需按游戏「系统设置-快捷键」核对）；
无快捷键的入口（如活动）降级为点标定坐标。滑动用鼠标滚轮。

停止：①背包挖空自然结束(主)②时间上限分钟(安全网)③手动停止/鼠标甩左上角 failsafe。
安全默认 dry_run=true：不发快捷键/不点关键操作/不双击用图，只对当前屏幕做识别自检，便于先验证模板。
"""

import time

from ..core import vision
from ..core import window as win_mod
from .base import Task, register

# 状态机状态
S_RESET = "RESET"
S_OPEN_ACTIVITY = "OPEN_ACTIVITY"
S_DIALOG = "DIALOG"
S_COLLECTING = "COLLECTING"
S_DIG_OPEN_BAG = "DIG_OPEN_BAG"
S_DIG_FIND = "DIG_FIND"
S_DIGGING = "DIGGING"
S_CHECK_DONE = "CHECK_DONE"
S_FINISHED = "FINISHED"

_STILL_DIFF = 2.0   # 帧差低于此视为画面静止（人物不动）

# 必备模板（缺失则 preflight 阻断）与可选模板（缺失仅 warn）
_FLAG_KEYS = ["flag_treasure_entry", "flag_tingting", "flag_dialog",
              "flag_battle", "flag_next_map", "treasure_item"]
_REQUIRED_FLAGS = ["flag_treasure_entry", "flag_tingting", "flag_next_map", "treasure_item"]


@register
class TreasureMapTask(Task):
    name = "treasure_map"
    title = "刷副本·宝图"
    description = "自动开活动→收藏宝图→挖宝→领奖，一条龙（战斗交给游戏自动）"

    CALIBRATION = {
        "regions": [
            ("scene", "主识别区", "整窗或大半屏——战斗/对话/下一张等所有标志都在这里找，框大一点"),
            ("activity_list", "活动列表区域", "「活动」界面里那片列表，滚轮在此翻找「宝图任务」条目"),
            ("bag_list", "背包列表区域", "背包里道具格那片区域，滚轮在此翻找藏宝图"),
            ("activity_button", "活动入口按钮(可选)", "若「活动」没有快捷键，标这个按钮，脚本点它打开活动"),
            ("blank_spot", "安全空白处(可选)", "卡死恢复时点这里关掉异常弹窗，选一处永远安全的空地"),
        ],
        "templates": [
            ("flag_treasure_entry", "宝图任务入口", "活动列表里「宝图任务」那一条，框图标+文字、要独特"),
            ("flag_tingting", "「听听无妨」选项", "和 NPC 对话弹框里要点的那个选项"),
            ("flag_next_map", "「下一张使用」按钮", "挖完一张后游戏自动弹出的继续按钮"),
            ("treasure_item", "藏宝图道具", "背包里藏宝图那个图标的样子"),
            ("flag_battle", "战斗界面标志(可选)", "战斗独有的画面元素，用于避免战斗期被误判卡死"),
            ("flag_dialog", "对话框标志(可选)", "对话框出现的独特标志，用于等对话/排除误判"),
        ],
        "watchlist": False,
    }

    # ------------------------------------------------------------------
    def preflight(self, ctx):
        tc = ctx.task_cfg(self.name)
        problems = []
        regions = tc.get("regions", {})
        templates = tc.get("templates", {})
        skip_collect = tc.get("skip_collect", False)   # 已有宝图：跳过阶段A

        # 区域：主识别区+背包区始终要；活动列表区仅阶段A要
        need_regions = [("scene", "主识别区"), ("bag_list", "背包列表区域")]
        if not skip_collect:
            need_regions.append(("activity_list", "活动列表区域"))
        for rk, label in need_regions:
            if not regions.get(rk):
                problems.append(f"『{label}』未标定 —— 请先做标定")

        # 模板：挖宝必备始终要；领宝图的两个仅阶段A要
        need_flags = ["flag_next_map", "treasure_item"]
        if not skip_collect:
            need_flags += ["flag_treasure_entry", "flag_tingting"]
        for tk in need_flags:
            path = templates.get(tk)
            if not path or vision.load_template(path) is None:
                problems.append(f"模板『{tk}』缺失或加载失败 —— 请在标定向导里框选裁图")

        # 活动入口仅阶段A需要：要么有快捷键，要么标了 activity_button 坐标
        if not skip_collect and not ctx.hotkeys.get("open_activity") and not regions.get("activity_button"):
            problems.append("打开『活动』没有办法：请在设置里填 open_activity 快捷键，或标定『活动入口按钮』")
        # 开背包必须有快捷键（没有背包按钮可点）
        if not ctx.hotkeys.get("open_bag"):
            problems.append("打开『背包』缺快捷键：请在 config.hotkeys.open_bag 填上（如 alt+e）")

        if not ctx.window.locate():
            problems.append(f"没找到游戏窗口（标题含「{ctx.window.title_substr}」），请先打开游戏")

        # 可选模板缺失只提示（flag_dialog 仅阶段A用，已有宝图时不提）
        optional = ["flag_battle"] if skip_collect else ["flag_battle", "flag_dialog"]
        for tk in optional:
            if not templates.get(tk) or vision.load_template(templates.get(tk)) is None:
                ctx.log(f"提示：可选模板『{tk}』未标定，将降级靠帧差+超时推进（可靠性略降）。", level="warn")

        return (len(problems) == 0), problems

    # ------------------------------------------------------------------
    def run(self, ctx):
        tc = ctx.task_cfg(self.name)
        loop = tc["loop"]
        regions = tc["regions"]
        dry_run = tc.get("dry_run", True)
        self._skip_collect = tc.get("skip_collect", False)
        threshold = loop["match_threshold"]
        self.flags = self._load_flags(tc)

        time_limit = loop.get("time_limit_min", 0) or 0
        start_ts = time.time()
        deadline = start_ts + time_limit * 60 if time_limit > 0 else None

        if not self._is_admin():
            ctx.log("⚠ 当前非管理员权限：游戏在前台时鼠标/键盘注入可能被 UIPI 拦截。"
                    "请用『以管理员身份运行』重开。", level="warn")

        if dry_run:
            if self._skip_collect:
                hint = "打开背包，看日志能否认出 藏宝图/下一张/战斗"
            else:
                hint = "手动打开对应界面，看日志能否认出 宝图入口/听听无妨/战斗/下一张/藏宝图"
            ctx.log("演练模式：不会真正推进副本，仅对当前屏幕循环做『各标志识别自检』。"
                    + hint + "。", level="warn")
            self._dry_run_selfcheck(ctx, regions, threshold, deadline, self._skip_collect)
            return

        if self._skip_collect:
            ctx.log("★ 实战模式（已有宝图）：跳过领取，直接开背包挖包裹里的藏宝图 ★", level="warn")
        else:
            ctx.log("★ 实战模式：会真开活动、真用宝图、真领奖 ★", level="warn")
        if time_limit > 0:
            ctx.log(f"时间上限 {time_limit} 分钟（到点自停）。主终止条件是背包藏宝图挖空。")

        self._dug = 0
        state = S_RESET
        state_enter = time.time()
        recover = 0

        while not ctx.should_stop():
            if deadline and time.time() >= deadline:
                ctx.log(f"已达时间上限 {time_limit} 分钟，停止。")
                break
            if state == S_FINISHED:
                break
            if not ctx.window.locate():
                ctx.log("游戏窗口不见了，2 秒后重试…", level="warn")
                self._interruptible_sleep(ctx, 2.0)
                continue

            nxt = self._dispatch(ctx, state, tc, loop, regions, threshold)

            if nxt == "STOP":
                break
            if nxt == "STUCK":
                recover += 1
                ctx.log(f"状态 {state} 卡住（第 {recover}/{loop.get('max_stuck_recover',3)} 次），尝试复位恢复…",
                        level="warn")
                self._save_capture(self._grab_scene(ctx, regions), f"stuck_{state}")
                if recover >= loop.get("max_stuck_recover", 3):
                    ctx.log("多次卡死仍无进展，主动停止。", level="error")
                    break
                self._reset(ctx, ctx.hotkeys)
                state, state_enter = S_RESET, time.time()
                continue

            # 到达里程碑(收集/挖宝/挖到图)就清零恢复计数
            if nxt in (S_COLLECTING, S_DIGGING):
                recover = 0
            if nxt != state:
                state, state_enter = nxt, time.time()

        ctx.log(f"已停止。共挖宝图 {getattr(self,'_dug',0)} 张，用时 {(time.time()-start_ts)/60:.1f} 分钟。")

    # ------------------------------------------------------------------
    # 状态分发
    # ------------------------------------------------------------------
    def _dispatch(self, ctx, state, tc, loop, regions, threshold):
        if state == S_RESET:
            self._reset(ctx, ctx.hotkeys)
            # 已有宝图：跳过阶段A(开活动领宝图)，复位后直接进挖宝
            return S_DIG_OPEN_BAG if self._skip_collect else S_OPEN_ACTIVITY
        if state == S_OPEN_ACTIVITY:
            return self._st_open_activity(ctx, loop, regions, threshold)
        if state == S_DIALOG:
            return self._st_dialog(ctx, loop, regions, threshold)
        if state == S_COLLECTING:
            return self._st_collecting(ctx, loop, regions, threshold)
        if state == S_DIG_OPEN_BAG:
            return self._st_dig_open_bag(ctx)
        if state == S_DIG_FIND:
            return self._st_dig_find(ctx, loop, regions, threshold)
        if state == S_DIGGING:
            return self._st_digging(ctx, loop, regions, threshold)
        if state == S_CHECK_DONE:
            return self._st_check_done(ctx, loop, regions, threshold)
        return S_FINISHED

    # ---- 阶段 A ----
    def _focus(self, ctx):
        """把游戏窗口切到前台——键盘快捷键(SendInput)只发给有焦点的窗口，发键前必须先激活，
        否则 Alt+E 之类会发给助手界面而不是游戏。"""
        try:
            ctx.window.activate()
        except Exception:
            pass

    def _reset(self, ctx, hotkeys):
        """复位：先把游戏切到前台，再连发几次「关闭面板」快捷键，回到可操作状态。"""
        self._focus(ctx)
        ctx.log("复位：关闭所有面板…")
        for _ in range(3):
            if ctx.should_stop():
                return
            if not ctx.send_hotkey("close_panel"):
                break   # 没配 close_panel 就跳过
            self._interruptible_sleep(ctx, self._jitter(0.25, ctx))

    def _st_open_activity(self, ctx, loop, regions, threshold):
        # 打开活动：优先快捷键，没有就点标定的活动按钮（发键前先激活游戏）
        self._focus(ctx)
        if not ctx.send_hotkey("open_activity"):
            if not self._click_region(ctx, regions.get("activity_button")):
                ctx.log("打不开活动界面（无快捷键且未标定活动按钮）。", level="error")
                return "STUCK"
        ctx.log("已打开活动，滚轮翻找「宝图任务」…")
        self._interruptible_sleep(ctx, self._jitter(0.6, ctx))

        hit = self._scroll_find(ctx, regions.get("activity_list"),
                                self.flags.get("flag_treasure_entry"), loop, threshold, "宝图任务入口")
        if hit is None:
            ctx.log("活动列表里没找到「宝图任务」入口。", level="warn")
            return "STUCK"
        ctx.mouse.click(hit[0], hit[1])
        ctx.log(f"点击「宝图任务」入口（相似度 {hit[2]:.3f}）。")
        return S_DIALOG

    def _st_dialog(self, ctx, loop, regions, threshold):
        """等 NPC 对话框出现，点「听听无妨」。"""
        ctx.log("等待传送+寻路到 NPC、对话框出现…")
        timeout = loop.get("dialog_timeout_sec", 30)
        t0 = time.time()
        tpl = self.flags.get("flag_tingting")
        while not ctx.should_stop():
            if time.time() - t0 > timeout:
                ctx.log("等对话框超时。", level="warn")
                return "STUCK"
            scene_rect = self._scene_rect(ctx, regions)
            cur = win_mod.grab(scene_rect)
            hit = vision.match(cur, tpl, threshold) if tpl is not None else None
            if hit is not None:
                cx, cy, score = hit
                ctx.mouse.click(scene_rect[0] + cx, scene_rect[1] + cy)
                ctx.log(f"选择「听听无妨」（相似度 {score:.3f}），开始自动寻宝。", level="hit")
                return S_COLLECTING
            self._interruptible_sleep(ctx, 0.4)
        return "STOP"

    def _st_collecting(self, ctx, loop, regions, threshold):
        """监控收集阶段：自动寻宝+自动战斗，直到人物持续静止（且非战斗非对话）=收集完成。"""
        ctx.log("收集阶段：自动寻宝中（战斗交给游戏），等人物站定…")
        idle_need = loop.get("collect_idle_sec", 4.0)
        overall = loop.get("collect_timeout_sec", 600)
        t0 = time.time()
        last = None
        still_since = None
        while not ctx.should_stop():
            if time.time() - t0 > overall:
                ctx.log("收集阶段超时。", level="warn")
                return "STUCK"
            scene_rect = self._scene_rect(ctx, regions)
            cur = win_mod.grab(scene_rect)
            if self._present(cur, "flag_battle", threshold) or self._present(cur, "flag_dialog", threshold):
                still_since, last = None, None   # 战斗/对话中不计静止
            else:
                if last is not None and self._frame_diff(last, cur) < _STILL_DIFF:
                    if still_since is None:
                        still_since = time.time()
                    elif time.time() - still_since >= idle_need:
                        ctx.log("人物持续静止 → 收集完成，转入挖宝。", level="hit")
                        return S_DIG_OPEN_BAG
                else:
                    still_since = None
                last = cur
            self._interruptible_sleep(ctx, 0.3)
        return "STOP"

    # ---- 阶段 B ----
    def _st_dig_open_bag(self, ctx):
        self._focus(ctx)
        if not ctx.send_hotkey("open_bag"):
            ctx.log("打不开背包（缺 open_bag 快捷键）。", level="error")
            return "STUCK"
        ctx.log("打开背包，翻找藏宝图…")
        self._interruptible_sleep(ctx, self._jitter(0.6, ctx))
        return S_DIG_FIND

    def _st_dig_find(self, ctx, loop, regions, threshold):
        hit = self._scroll_find(ctx, regions.get("bag_list"),
                                self.flags.get("treasure_item"), loop, threshold, "藏宝图")
        if hit is None:
            ctx.log("背包里没找到藏宝图，去确认是否挖完。")
            return S_CHECK_DONE
        ctx.mouse.double_click(hit[0], hit[1])
        ctx.log(f"双击使用藏宝图（相似度 {hit[2]:.3f}），自动传送挖宝。")
        return S_DIGGING

    def _st_digging(self, ctx, loop, regions, threshold):
        """监控挖宝：挖完游戏弹「下一张使用」→点它继续；长时间无弹窗且静止→认为挖完。"""
        per_map_timeout = loop.get("dig_timeout_sec", 120)
        idle_need = loop.get("collect_idle_sec", 4.0)
        t0 = time.time()
        last = None
        still_since = None
        while not ctx.should_stop():
            scene_rect = self._scene_rect(ctx, regions)
            cur = win_mod.grab(scene_rect)

            nxt = self.flags.get("flag_next_map")
            hit = vision.match(cur, nxt, threshold) if nxt is not None else None
            if hit is not None:
                cx, cy, score = hit
                ctx.mouse.click(scene_rect[0] + cx, scene_rect[1] + cy)
                self._dug += 1
                ctx.log(f"挖完第 {self._dug} 张，点「下一张使用」继续。", level="hit")
                t0, last, still_since = time.time(), None, None
                self._interruptible_sleep(ctx, self._jitter(1.0, ctx))
                continue

            if self._present(cur, "flag_battle", threshold):
                t0, last, still_since = time.time(), None, None   # 战斗中刷新计时
            else:
                if last is not None and self._frame_diff(last, cur) < _STILL_DIFF:
                    if still_since is None:
                        still_since = time.time()
                    elif time.time() - still_since >= idle_need:
                        ctx.log("无更多「下一张」且画面静止 → 这批可能挖完，去确认。")
                        return S_CHECK_DONE
                else:
                    still_since = None
                last = cur

            if time.time() - t0 > per_map_timeout:
                ctx.log("单张挖宝超时，去确认背包。", level="warn")
                return S_CHECK_DONE
            self._interruptible_sleep(ctx, 0.3)
        return "STOP"

    def _st_check_done(self, ctx, loop, regions, threshold):
        """开背包确认是否还有藏宝图：有→回去接着挖；无→结束。"""
        self._focus(ctx)
        if not ctx.send_hotkey("open_bag"):
            ctx.log("确认阶段打不开背包，直接结束。", level="warn")
            return S_FINISHED
        self._interruptible_sleep(ctx, self._jitter(0.6, ctx))
        hit = self._scroll_find(ctx, regions.get("bag_list"),
                                self.flags.get("treasure_item"), loop, threshold, "藏宝图(确认)")
        if hit is not None:
            ctx.log("背包仍有藏宝图，继续挖。")
            return S_DIG_FIND
        ctx.log("背包已无藏宝图 → 全部挖完。", level="hit")
        return S_FINISHED

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------
    def _load_flags(self, tc):
        templates = tc.get("templates", {})
        return {k: vision.load_template(templates.get(k)) if templates.get(k) else None
                for k in _FLAG_KEYS}

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

    def _scroll_find(self, ctx, list_region, tpl, loop, threshold, label):
        """在 list_region 内滚轮翻找 tpl。命中返回 (screen_x, screen_y, score)；
        翻完 scroll_max_tries 屏仍无→返回 None。滚动本身无害，演练/实战都执行。"""
        if tpl is None:
            ctx.log(f"找 {label} 失败：模板未标定。", level="warn")
            return None
        tries = max(1, loop.get("scroll_max_tries", 8))
        step = loop.get("scroll_step", -3)
        for i in range(tries):
            if ctx.should_stop():
                return None
            rect = (ctx.window.region_to_screen_rect(list_region)
                    if list_region else ctx.window.rect())
            if rect is None:
                return None
            scene = self._wait_still(ctx, rect, loop.get("still_min_sec", 0.3),
                                     loop.get("still_wait_sec", 2.0))
            if scene is None:
                return None
            hit = vision.match(scene, tpl, threshold)
            if hit is not None:
                cx, cy, score = hit
                return (rect[0] + cx, rect[1] + cy, score)
            # 没找到→在列表中心向下滚一屏再找
            cx_c, cy_c = rect[0] + rect[2] // 2, rect[1] + rect[3] // 2
            ctx.mouse.scroll(step, cx_c, cy_c)
        return None

    def _dry_run_selfcheck(self, ctx, regions, threshold, deadline, skip_collect=False):
        """演练：周期性对当前屏幕识别各标志，报告命中，便于用户验证模板/阈值。
        已有宝图(skip_collect)时只自检挖宝相关标志，不提阶段A的宝图入口/听听无妨/对话框。"""
        if skip_collect:
            keys = [("flag_battle", "战斗"), ("flag_next_map", "下一张使用"),
                    ("treasure_item", "藏宝图")]
        else:
            keys = [("flag_treasure_entry", "宝图入口"), ("flag_tingting", "听听无妨"),
                    ("flag_dialog", "对话框"), ("flag_battle", "战斗"),
                    ("flag_next_map", "下一张使用"), ("treasure_item", "藏宝图")]
        while not ctx.should_stop():
            if deadline and time.time() >= deadline:
                ctx.log("演练时间上限到，停止。")
                break
            if not ctx.window.locate():
                self._interruptible_sleep(ctx, 1.5)
                continue
            scene = self._grab_scene(ctx, regions)
            found = []
            for key, label in keys:
                tpl = self.flags.get(key)
                if tpl is None:
                    continue
                hit = vision.match(scene, tpl, threshold) if scene is not None else None
                if hit is not None:
                    found.append(f"{label}({hit[2]:.2f})")
            if found:
                ctx.log("识别到：" + "、".join(found), level="hit")
            else:
                ctx.log("当前屏幕未识别到任何已标定标志（请打开对应界面再看）。")
            self._interruptible_sleep(ctx, self._jitter(1.5, ctx))
