# -*- coding: utf-8 -*-
"""
宝图任务（两阶段状态机，支持多开逐号轮转）。

游戏自带「自动战斗」全托管，脚本只做导航 + 状态监控 + 关键点击：

阶段 A 收图：
  开「活动」(快捷键 Alt+C) → 滚轮找「宝图任务」条目 → 点该行【右侧的「参加」按钮】(按行匹配，不是点条目本身)
  → 传送+自动寻路到 NPC 对话 → 弹框选「听听无妨」→ 自动寻宝(自动战斗)
  → 所有藏宝图拿完后人物站着不动(帧差判静止=收集完成)。
阶段 B 挖宝：
  开背包(快捷键) → 滚轮找藏宝图 → 双击用 → 自动传送挖宝 → 挖完游戏弹「下一张使用」按钮 → 点它
  → 循环到不再弹 → 再开背包确认无图 →【关上背包】→ 该号结束。

★ 多开轮转（用户要求「多号之间每一步都轮转」，2026-06-23 比照秘境降妖重做）：
  每个号各持一份状态(record)，主循环对每个号【各推进一小步】(非阻塞)，号与号之间逐步轮转——
  这样 号1 在自动寻宝/挖宝(游戏自动战斗)时，可以去把 号2/号3 也推进，再回头照看 号1。
  鼠标光标全局唯一，故单线程轮转、操作某号前先 activate() 切前台。单开=列表只有一个号、同走这套轮转。
  ⚠ 原来「收集/挖宝监控」是内部 while 阻塞循环（只能盯住第一个号），重做为「每访问一次扫一帧、
     帧差/计时状态存进该号 record」，从而能在多个号之间真正轮转。

导航靠 ctx.send_hotkey(动作名)（键位在 config.hotkeys，用户需按游戏「系统设置-快捷键」核对）；
无快捷键的入口（如活动）降级为点标定坐标。滑动用鼠标滚轮。

停止：①所有号背包都挖空自然结束(主)②时间上限分钟(安全网)③手动停止/鼠标甩左上角 failsafe。
安全默认 dry_run=true：不发快捷键/不点关键操作/不双击用图，只对各号当前屏幕做识别自检，便于先验证模板。
"""

import time

from ..core import activity
from ..core import calib_profiles as calib
from ..core import vision
from ..core import window as win_mod
from ..core import rotation
from ..core import scan
from ..core import list_row
from ..core import calibration_runtime as calibrated
from .base import Task, register, _input_policy

# 每个号的状态机状态（非阻塞：每访问一次只推进一步）
S_OPEN_ACTIVITY = "OPEN_ACTIVITY"     # 阶段A：发活动快捷键
S_FIND_CARD = "FIND_CARD"             # 阶段A：活动列表里滚轮找「宝图任务」卡片 → 点「参加」
S_DIALOG = "DIALOG"                   # 阶段A：等 NPC 对话框 → 点「听听无妨」
S_COLLECTING = "COLLECTING"           # 阶段A：自动寻宝中，盯人物静止=收集完成
S_DIG_OPEN_BAG = "DIG_OPEN_BAG"       # 阶段B：发开背包快捷键
S_DIG_WAIT_BAG = "DIG_WAIT_BAG"       # 阶段B：确认背包确实打开，不能把快捷键发出当成功
S_DIG_FIND = "DIG_FIND"               # 阶段B：背包里滚轮找藏宝图 → 双击用；翻完无图=该号挖完
S_DIGGING = "DIGGING"                 # 阶段B：挖宝中，盯「下一张使用」续挖 / 静止判这批挖完
S_DIG_CLOSE_BAG = "DIG_CLOSE_BAG"
S_DIG_WAIT_CLOSED = "DIG_WAIT_CLOSED"

# 状态→中文（仅用于停止汇总「某号停在哪一步」的可读提示）
_STATE_CN = {
    S_OPEN_ACTIVITY: "开活动", S_FIND_CARD: "找宝图卡片/点参加", S_DIALOG: "等NPC对话框/点听听无妨",
    S_COLLECTING: "收集寻宝中", S_DIG_OPEN_BAG: "开背包", S_DIG_WAIT_BAG: "确认背包打开",
    S_DIG_FIND: "翻背包找藏宝图", S_DIGGING: "挖宝中",
    S_DIG_CLOSE_BAG: "关闭背包", S_DIG_WAIT_CLOSED: "确认背包关闭",
}

_STILL_DIFF = 8.0   # 帧差低于此视为画面静止（人物不动）的默认阈值——可被 loop.still_diff 覆盖。
_UNSET_SCENE = object()
#   太小→人物待机动画/周围NPC玩家走动/特效会让整屏帧差长期偏高，永远判不到「静止」→收集/挖宝误超时。
#   运行时日志会实时打印真实帧差，照着把阈值设到「静止时帧差」之上、「走动时帧差」之下即可。

# 必备模板（缺失则 preflight 阻断）与可选模板（缺失仅 warn）
#   flag_join=活动列表里「宝图任务」那一行右侧的「参加」按钮——按行匹配点它（不是点条目本身）。
_FLAG_KEYS = ["flag_treasure_entry", "flag_join", "flag_tingting",
              "flag_battle", "flag_next_map", "treasure_item", "flag_bag_open"]
_REQUIRED_FLAGS = ["flag_treasure_entry", "flag_join", "flag_tingting",
                   "flag_next_map", "treasure_item"]


@register
class TreasureMapTask(Task):
    name = "treasure_map"
    title = "宝图"
    description = "自动开活动→收藏宝图→挖宝→领奖，一条龙（战斗交给游戏自动，支持多开轮转）"
    CHAINS_PER_WINDOW = True   # 可做「日常一条龙·每窗口独立链」

    CALIBRATION = {
        "regions": [
            ("scene", "主识别区", "留空=整窗检测(推荐)", True),
            ("activity_list", "活动列表区域", "滚轮在此翻找「宝图任务」"),
            ("bag_list", "背包列表区域", "滚轮在此翻找藏宝图"),
        ],
        "templates": [
            ("flag_treasure_entry", "宝图任务入口", "「宝图任务」那一条，框图标+文字"),
            ("flag_join", "参加按钮", "「宝图任务」那行右侧的「参加」"),
            ("flag_tingting", "「听听无妨」选项", "和 NPC 对话弹框里要点的选项"),
            ("flag_next_map", "「下一张使用」按钮", "挖完一张后弹出的继续按钮"),
            ("treasure_item", "藏宝图道具", "背包里藏宝图的图标"),
            ("flag_bag_open", "背包打开标志", "背包内固定的「整理」按钮，可复用整理背包标定"),
            ("flag_battle", "战斗界面标志(可选)", "战斗独有元素，防战斗期被误判卡死"),
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

        # scene 留空=整窗检测，不再强制标定；背包区始终要、活动列表区仅阶段A要
        need_regions = [("bag_list", "背包列表区域")]
        if not skip_collect:
            need_regions.append(("activity_list", "活动列表区域"))
        for rk, label in need_regions:
            if not regions.get(rk):
                problems.append(f"『{label}』未标定 —— 请先做标定")

        # 模板：挖宝必备始终要；领宝图相关仅阶段A要（含「参加」按钮）
        need_flags = ["flag_next_map", "treasure_item"]
        if not skip_collect:
            need_flags += ["flag_treasure_entry", "flag_join", "flag_tingting"]
        for tk in need_flags:
            path = templates.get(tk)
            if not path or vision.load_template(path) is None:
                problems.append(f"模板『{tk}』缺失或加载失败 —— 请在标定向导里框选裁图")

        if self._bag_template(ctx) is None:
            problems.append("『背包打开标志』缺失 —— 请标定背包内固定的「整理」按钮，或复用『整理背包』的整理按钮标定")

        # 活动入口仅阶段A需要：必须有 open_activity 快捷键（默认 Alt+C）
        if not skip_collect and not ctx.hotkeys.get("open_activity"):
            problems.append("打开『活动』缺快捷键：请在 config.hotkeys.open_activity 填上（如 alt+c）")
        # 开背包必须有快捷键（没有背包按钮可点）
        if not ctx.hotkeys.get("open_bag"):
            problems.append("打开『背包』缺快捷键：请在 config.hotkeys.open_bag 填上（如 alt+e）")

        if not ctx.select_windows():
            problems.append(f"没找到/没选中目标窗口（标题含「{ctx.window.title_substr}」）"
                            "，请先打开游戏并在「选择窗口」里选好")

        # 可选模板缺失只提示
        optional = ["flag_battle"]
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
        self._start_state = S_DIG_OPEN_BAG if self._skip_collect else S_OPEN_ACTIVITY
        threshold = loop["match_threshold"]
        self.flags = self._load_flags(tc)
        self.flags["flag_bag_open"] = self._load_bag_template(ctx)

        multi = ctx.cfg.get("targets", {}).get("multi", False)
        switch_delay = ctx.cfg.get("targets", {}).get("switch_delay_sec", 0.15)
        tick = loop.get("tick_interval_sec", 0.6)

        time_limit = loop.get("time_limit_min", 0) or 0
        start_ts = time.time()
        deadline = start_ts + time_limit * 60 if time_limit > 0 else None

        if not self._is_admin():
            ctx.log("⚠ 当前非管理员权限：游戏在前台时鼠标/键盘注入可能被 UIPI 拦截。"
                    "请用『以管理员身份运行』重开。", level="warn")

        contexts = self._resolve_contexts(ctx, multi)
        if not contexts:
            ctx.log("没找到/没选中目标窗口，已停止。", level="error")
            return "stopped" if ctx.should_stop() else "failed"

        if dry_run:
            if self._skip_collect:
                hint = "打开背包，看日志能否认出 藏宝图/下一张/战斗"
            else:
                hint = "手动打开对应界面，看日志能否认出 宝图入口/参加按钮/听听无妨/战斗/下一张/藏宝图"
            ctx.log("演练模式：不会真正推进副本，仅对各号当前屏幕循环做『各标志识别自检』。"
                    + hint + ("（多号逐个扫描）" if multi else "") + "。", level="warn")
            self._dry_run_selfcheck(ctx, contexts, multi, regions, threshold, switch_delay,
                                    deadline, self._skip_collect)
            return "stopped" if ctx.should_stop() else "timeout"

        if self._skip_collect:
            ctx.log("★ 实战模式（已有宝图）：跳过领取，直接开背包挖包裹里的藏宝图 ★", level="warn")
        else:
            ctx.log("★ 实战模式：会真开活动、真用宝图、真领奖 ★", level="warn")
        ctx.log(f"★ {('多开轮转 ' + str(len(contexts)) + ' 个号' if multi else '单号')}，"
                "号与号之间逐步轮转；每号终止条件=背包藏宝图挖空 ★", level="warn")
        if time_limit > 0:
            ctx.log(f"时间上限 {time_limit} 分钟（到点自停）。主终止条件是各号背包藏宝图挖空。")

        records = [self._new_record(c) for c in contexts]
        rc = self._make_rotation(
            ctx, records,
            lambda rec: self._step_once(rec["ctx"], rec, loop, regions, threshold),
            multi, switch_delay, tick, time_limit)
        try:
            rotation.run_rotation(rc)
        finally:
            if not ctx.should_stop():
                for rec in records:
                    self.finish_chain_driver(rec["ctx"], rec)

        self._log_summary(ctx, records, rc.termination_reason, (time.time() - start_ts) / 60)

        return self._activity_run_outcome(ctx, records, rc.termination_reason, "dug")

    # ------------------------------------------------------------------
    # 日常一条龙·每窗口独立链：暴露「单窗口一份 record + 单步推进函数」
    # ------------------------------------------------------------------
    def make_chain_driver(self, wctx):
        """给定单窗口上下文，返回 (record, step_fn)。step_fn() 推进该窗口本任务状态机一步
        （沿用 run() 同款 _step_once），record["done"]=本任务在该窗口完成。
        与 run() 共用 _new_record/_step_once，不自跑 rotation、不切前台（由一条龙总轮转统一切）。
        注意：_new_record 依赖 self._skip_collect/_start_state，须先在此设好（同 run() 开头）。"""
        tc = wctx.task_cfg(self.name)
        loop = tc["loop"]
        regions = tc["regions"]
        threshold = loop["match_threshold"]
        self.flags = self._load_flags(tc)
        self.flags["flag_bag_open"] = self._load_bag_template(wctx)
        self._skip_collect = tc.get("skip_collect", False)
        self._start_state = S_DIG_OPEN_BAG if self._skip_collect else S_OPEN_ACTIVITY
        rec = self._new_record(wctx)
        return rec, (lambda: self._step_once(wctx, rec, loop, regions, threshold))

    # ------------------------------------------------------------------
    # 多开轮转：上下文与每号状态记录
    # ------------------------------------------------------------------
    def _resolve_contexts(self, ctx, multi):
        """按选择把目标窗口包成「要轮转的上下文」列表。
        单开→复用主 ctx 并绑到选中的那个窗口；多开→每号一个子上下文(带「角色名（等级）」标签，日志自动加前缀)。"""
        wins = ctx.select_windows()
        if not wins:
            return []
        if multi:
            labels = ctx.window_labels(wins)
            return [ctx.make_child(w, labels[i]) for i, w in enumerate(wins)]
        ctx.window = wins[0]
        return [ctx]

    def _new_record(self, wctx):
        """每个号一份独立状态。轮转时按 state 各推进一步，互不干扰。
        phase_b：是否已进入挖宝阶段（决定卡死兜底回开活动还是回重开背包）。
        last/still_since/t0/t_diag：收集/挖宝监控的逐帧状态（原内部 while 循环搬到这里、跨访问保留）。"""
        return {"ctx": wctx, "state": self._start_state, "t_state": time.time(),
                "scrolls": 0, "dug": 0, "phase_b": self._skip_collect,
                "last": None, "still_since": None, "t0": 0.0, "t_diag": 0.0,
                "recover": 0, "done": False, "dead_logged": False, "empty_passes": 0,
                "bag_scan": None, "inventory_revision": getattr(wctx, "inventory_revision", 0),
                "close_attempts": 0, "cleanup_outcome": None, "unsafe_ui": False,
                "scan_failures": 0}

    @staticmethod
    def _goto(rec, state):
        rec["state"] = state
        rec["t_state"] = time.time()

    def _enter_monitor(self, rec, state):
        """进入收集/挖宝监控：清空逐帧状态、重置阶段起始计时。"""
        self._goto(rec, state)
        rec["t0"] = time.time()
        rec["last"] = None
        rec["still_since"] = None
        rec["t_diag"] = 0.0

    @staticmethod
    def _state_elapsed(rec):
        return time.time() - rec["t_state"]

    # ------------------------------------------------------------------
    # 单步推进：按这个号的 state 做【一小步】非阻塞动作，然后立刻返回（好轮转到下一个号）
    # ------------------------------------------------------------------
    def _step_once(self, ctx, rec, loop, regions, threshold):
        st = rec["state"]
        if st == S_OPEN_ACTIVITY:
            self._do_open_activity(ctx, rec, loop, regions, threshold)
        elif st == S_FIND_CARD:
            self._do_find_card(ctx, rec, loop, regions, threshold)
        elif st == S_DIALOG:
            self._do_dialog(ctx, rec, loop, regions, threshold)
        elif st == S_COLLECTING:
            self._do_collecting(ctx, rec, loop, regions, threshold)
        elif st == S_DIG_OPEN_BAG:
            self._do_dig_open_bag(ctx, rec, loop, regions, threshold)
        elif st == S_DIG_WAIT_BAG:
            self._do_dig_wait_bag(ctx, rec, loop, regions, threshold)
        elif st == S_DIG_FIND:
            self._do_dig_find(ctx, rec, loop, regions, threshold)
        elif st == S_DIGGING:
            self._do_digging(ctx, rec, loop, regions, threshold)
        elif st in (S_DIG_CLOSE_BAG, S_DIG_WAIT_CLOSED):
            self._do_bag_cleanup(ctx, rec, loop, threshold)

    # ---- 阶段 A：开活动 ----
    def _do_open_activity(self, ctx, rec, loop, regions, threshold):
        self._focus(ctx)
        if not ctx.send_hotkey("open_activity"):
            ctx.log("打不开活动界面（open_activity 快捷键未配置），放弃该号。", level="error")
            rec["outcome"] = "failed"
            rec["done"] = True
            return
        ctx.log("已打开活动，滚轮翻找「宝图任务」…")
        self._interruptible_sleep(ctx, self._jitter(0.6, ctx))
        activity.select_daily(ctx, lambda s: self._interruptible_sleep(ctx, self._jitter(s, ctx)), self.name)
        rec["scrolls"] = 0
        self._goto(rec, S_FIND_CARD)

    # ---- 阶段 A：找卡片 → 点「参加」；没有参加按钮就直接开背包用已有图 ----
    # 用户要求：滚轮查找在【同一个号】上一气呵成跑完（找到/翻完才轮转下个号），不在滚动中途返回。
    #   故用内部 while 把整段查找做完再返回；每滚一屏后自等 scroll_settle_sec 让画面落定。仍勤查 should_stop。
    def _do_find_card(self, ctx, rec, loop, regions, threshold):
        list_region = regions.get("activity_list")

        def grab_rect():
            return calibrated.region_rect(ctx, self.name, "activity_list", list_region)

        def probe(scene, rect):
            if scene is None:
                return (scan.SCROLL, None)
            got = list_row.locate_card(scene, rect, self.flags.get("flag_treasure_entry"),
                                       self.flags.get("flag_join"), threshold,
                                       self._calib_size(ctx, "flag_treasure_entry"), loop, log=ctx.log,
                                       window_rect=ctx.window.rect(),
                                       join_calib_size=self._calib_size(ctx, "flag_join"))
            if got is None:
                return (scan.SCROLL, None)
            if got.join_x is None:
                ctx.log("认出「宝图任务」但没找到「参加」，直接打开背包使用已有藏宝图。", level="warn")
                # ACCEPT + 空负载：终止扫描，交给下方背包分支；不是已点击参加。
                return (scan.ACCEPT, None)
            ctx.mouse.click(got.join_x, got.join_y)
            ctx.log(f"找到「宝图任务」（{got.anchor_score:.3f}）→ 点「参加」"
                    f"（{got.join_score:.3f}），开始传送找 NPC。", level="hit")
            return (scan.ACCEPT, (got.join_x, got.join_y, got.join_score))

        res = scan.scroll_search(
            grab_rect=grab_rect, probe=probe, mouse=ctx.mouse,
            should_stop=ctx.should_stop,
            sleep=lambda s: self._interruptible_sleep(ctx, self._jitter(s, ctx)),
            scroll_step=loop.get("scroll_step", -3),
            max_tries=max(1, loop.get("scroll_max_tries", 8)),
            settle_sec=loop.get("scroll_settle_sec", 0.35),
            reset_to_top=loop.get("scroll_reset_top", True),
            end_diff=loop.get("scroll_end_diff", 2.0),
            reset_max=loop.get("scroll_reset_max", 20),
            log=ctx.log, label="活动列表")
        if res.found and res.payload is not None:
            self._goto(rec, S_DIALOG)
            return
        if res.stopped:
            return
        ctx.log("未识别到宝图「参加」，关闭活动页并检查背包里的已有藏宝图。", level="warn")
        self._focus(ctx)
        if ctx.send_hotkey("close_panel"):
            self._interruptible_sleep(ctx, self._jitter(0.25, ctx))
        if ctx.should_stop():
            return
        self._goto_dig(rec)
        self._do_dig_open_bag(ctx, rec, loop, regions, threshold)

    # ---- 阶段 A：等对话框；战斗说明已开始收图，超时转查已有图 ----
    def _do_dialog(self, ctx, rec, loop, regions, threshold):
        timeout = loop.get("dialog_timeout_sec", 30)
        scene_rect = self._scene_rect(ctx, regions)
        cur = win_mod.grab(scene_rect)
        if cur is None:
            if self._state_elapsed(rec) > timeout:
                ctx.log("等待对话时截图失败，转入恢复；未确认收图状态。", level="warn")
                self._recover_window(ctx, rec, loop, regions)
            return
        if self._in_battle(cur, loop, threshold):
            ctx.log("已进入战斗，跳过等待 NPC 对话，继续监控自动收图。", level="hit")
            rec["recover"] = 0
            self._enter_monitor(rec, S_COLLECTING)
            return
        hit = self._match_scene(cur, scene_rect, "flag_tingting", threshold)
        if hit is not None:
            if ctx.mouse.click(hit[0], hit[1]) is False:
                self._recover_window(ctx, rec, loop, regions)
                return
            ctx.log(f"选择「听听无妨」（{hit[2]:.3f}），开始自动寻宝。", level="hit")
            self._enter_monitor(rec, S_COLLECTING)
            rec["recover"] = 0
            return
        # 没命中且没超时时原本完全静默 → 用户以为该号卡死。加节流诊断：每 ~6s 报一次「在等对话框」，
        # 并打印当前「听听无妨」的最佳相似度，便于区分「对话框还没弹」与「弹了但相似度低于阈值认不出」。
        now = time.time()
        if now - (rec.get("t_diag") or 0.0) >= 6.0:
            best = self._best_score(cur, "flag_tingting")
            tip = f"最佳相似度 {best:.2f}<阈值 {threshold}" if best is not None else "尚未出现对话框"
            ctx.log(f"等 NPC 对话框中…（已 {self._state_elapsed(rec):.0f}/{timeout:.0f}s，{tip}）")
            rec["t_diag"] = now
        if self._state_elapsed(rec) > timeout:
            ctx.log("等 NPC 对话框超时，可能已跳过对话；转查背包已有藏宝图，不再重复开活动。", level="warn")
            self._goto_dig(rec)

    # ---- 阶段 A：收集监控（每访问一次扫一帧；人物持续静止=收集完成，转挖宝）----
    def _do_collecting(self, ctx, rec, loop, regions, threshold):
        idle_need = loop.get("collect_idle_sec", 4.0)
        overall = loop.get("collect_timeout_sec", 600)
        still_diff = loop.get("still_diff", _STILL_DIFF)
        if time.time() - rec["t0"] > overall:
            # 监控超时只允许转查背包，不能用超时证明已收图完毕。
            ctx.log(f"收集监控超过 {overall:.0f}s，转查背包已有藏宝图。", level="warn")
            self._goto_dig(rec)
            return
        scene_rect = self._scene_rect(ctx, regions)
        cur = win_mod.grab(scene_rect)
        if cur is None:
            rec["last"], rec["still_since"] = None, None
            return
        in_battle = self._in_battle(cur, loop, threshold)
        diff = self._frame_diff(rec["last"], cur) if rec["last"] is not None else None
        if in_battle:
            rec["still_since"], rec["last"] = None, None   # 战斗中不计静止
        else:
            if diff is not None and diff < still_diff:
                if rec["still_since"] is None:
                    rec["still_since"] = time.time()
                elif time.time() - rec["still_since"] >= idle_need:
                    ctx.log("人物持续静止 → 收集完成，转入挖宝。", level="hit")
                    self._goto_dig(rec)
                    return
            else:
                rec["still_since"] = None
            rec["last"] = cur
        self._log_motion_diag(ctx, rec, "收集中", diff, still_diff, idle_need, in_battle)

    def _goto_dig(self, rec):
        rec["phase_b"] = True
        self._goto(rec, S_DIG_OPEN_BAG)

    def _in_battle(self, cur, loop, threshold):
        # 战斗小图标受按钮光效影响，本次存证真战斗约 0.82；不能共用道具的 0.85。
        return self._present(cur, "flag_battle", min(threshold, loop.get("battle_threshold", 0.80)))

    # ---- 阶段 B：开背包 ----
    def _do_dig_open_bag(self, ctx, rec, loop, regions, threshold):
        self._focus(ctx)
        if ctx.should_stop():
            return
        cur = self._grab_scene(ctx, regions)
        if cur is not None and self._in_battle(cur, loop, threshold):
            # 战斗中不能打开背包；等待结束，不能消耗恢复次数或反复按开关。
            return
        visible = self._bag_visible(ctx, threshold)
        if visible is None:
            rec["empty_passes"] = 0
            ctx.log("无法确认背包界面，转入恢复；未确认挖空。", level="warn")
            self._recover_window(ctx, rec, loop, regions)
            return
        rec["phase_b"] = True
        rec["scrolls"] = 0
        rec["bag_scan"] = None
        if visible:
            ctx.log("背包已打开，直接翻找藏宝图。")
            self._goto(rec, S_DIG_FIND)
            return
        if not ctx.send_hotkey("open_bag"):
            ctx.log("打不开背包（缺 open_bag 快捷键），放弃该号。", level="error")
            rec["outcome"] = "failed"
            rec["done"] = True
            return
        ctx.log("已发送背包快捷键，等待确认背包打开…")
        self._interruptible_sleep(ctx, self._jitter(0.6, ctx))
        if not ctx.should_stop():
            self._goto(rec, S_DIG_WAIT_BAG)

    def _do_dig_wait_bag(self, ctx, rec, loop, regions, threshold):
        if ctx.should_stop():
            return
        cur = self._grab_scene(ctx, regions)
        if cur is not None and self._in_battle(cur, loop, threshold):
            rec["empty_passes"] = 0
            self._goto(rec, S_DIG_OPEN_BAG)
            return
        visible = self._bag_visible(ctx, threshold)
        if visible is True:
            ctx.log("已确认背包打开，翻找藏宝图…")
            self._goto(rec, S_DIG_FIND)
            return
        if self._state_elapsed(rec) > loop.get("bag_open_timeout_sec", 5.0):
            rec["empty_passes"] = 0
            ctx.log("未能确认背包打开，转入恢复；未确认挖空。", level="warn")
            self._recover_window(ctx, rec, loop, regions)

    # ---- 阶段 B：找藏宝图 → 双击用；翻完整背包都没有=该号挖完（关背包结束）----
    #   每窗口保留一轮完整扫描的进度；轮转预算耗尽只暂停，不回顶、不消耗恢复次数。
    def _do_dig_find(self, ctx, rec, loop, regions, threshold):
        list_region = regions.get("bag_list")
        if ctx.should_stop():
            return
        template = self.flags.get("treasure_item")
        if template is None:
            rec["empty_passes"] = 0
            ctx.log("藏宝图模板不可用，转入恢复；未确认挖空。", level="warn")
            self._recover_window(ctx, rec, loop, regions)
            return
        if self._bag_visible(ctx, threshold) is not True:
            rec["empty_passes"] = 0
            ctx.log("背包界面不可确认，重新检查背包；未确认挖空。", level="warn")
            self._goto(rec, S_DIG_OPEN_BAG)
            return
        action_failed = False
        revision = getattr(ctx, "inventory_revision", 0)
        if rec["inventory_revision"] != revision:
            rec["bag_scan"], rec["empty_passes"] = None, 0
            rec["inventory_revision"] = revision
        if rec["bag_scan"] is None:
            rec["bag_scan"] = scan.ScanProgress()
        progress = rec["bag_scan"]

        def probe(scene, rect):
            nonlocal action_failed
            if scene is None or scene.shape[0] < template.shape[0] or scene.shape[1] < template.shape[1]:
                action_failed = True
                return (scan.STAY, None)
            hit = calibrated.match_flag(self, scene, template, "treasure_item", threshold, ctx)
            if hit is not None:
                if ctx.mouse.double_click(rect[0] + hit[0], rect[1] + hit[1]) is False:
                    action_failed = True
                    return (scan.STAY, None)
                ctx.log(f"双击使用藏宝图（{hit[2]:.3f}），自动传送挖宝。")
                return (scan.ACCEPT, hit)
            return (scan.SCROLL, None)

        try:
            res = progress.advance(
                capture=lambda: self._capture_bag(ctx, list_region, threshold),
                probe=probe, mouse=ctx.mouse,
                should_stop=lambda: ctx.should_stop() or action_failed,
                sleep=lambda s: self._interruptible_sleep(ctx, self._jitter(s, ctx)),
                scroll_step=loop.get("scroll_step", -3),
                max_scrolls=max(1, loop.get("bag_scan_batch_scrolls", 8)),
                budget_sec=max(0.5, loop.get("bag_scan_batch_sec", 3.0)),
                min_settle_sec=loop.get("bag_scroll_min_settle_sec", 0.16),
                settle_sec=loop.get("bag_scroll_settle_sec", 0.35),
                end_diff=loop.get("scroll_end_diff", 2.0),
                total_scrolls=max(8, loop.get("bag_scan_max_scrolls", 256)),
                timeout_sec=max(1.0, loop.get("bag_scan_timeout_sec", 120.0)))
        except Exception as exc:
            ctx.log(f"背包扫描异常：{exc}，关闭背包后结束该号。", level="error")
            self._begin_bag_cleanup(rec, "failed")
            return
        if ctx.should_stop():
            rec["empty_passes"] = 0
            return
        if res.paused:
            rec["scan_failures"] = 0
            ctx.log(f"背包扫描进度已保留（累计滚动 {progress.scrolls} 次），下轮从当前位置继续。")
            return
        times = progress.timings
        ctx.log(f"本轮背包扫描累计滚动 {progress.scrolls} 次：观察 {times['observe']:.2f}s / "
                f"识别与使用 {times['probe']:.2f}s / 滚轮 {times['scroll']:.2f}s / "
                f"等待 {times['wait']:.2f}s。")
        if res.found and not action_failed:
            rec["scan_failures"] = 0
            rec["bag_scan"] = None
            rec["empty_passes"] = 0
            rec["recover"] = 0
            self._enter_monitor(rec, S_DIGGING)
            return
        if (res.stopped or action_failed
                or not res.exhausted or not res.reached_top or not res.reached_end):
            rec["empty_passes"] = 0
            rec["bag_scan"] = None
            if progress.last_reason in ("scan_limit", "invalid_step"):
                cause = "滚轮方向配置无效" if progress.last_reason == "invalid_step" else "整轮扫描达到安全上限"
                ctx.log(f"背包{cause}，关闭背包后异常结束；未确认挖空。", level="error")
                self._begin_bag_cleanup(rec, "failed")
                return
            rec["scan_failures"] += 1
            if rec["scan_failures"] < 3:
                ctx.log("背包观察或输入暂不可用，下轮重新核验；不计卡死。", level="warn")
                return
            rec["scan_failures"] = 0
            ctx.log("背包观察或输入异常，重新核验；未确认挖空。", level="warn")
            self._recover_window(ctx, rec, loop, regions)
            return
        if self._bag_visible(ctx, threshold) is not True:
            rec["empty_passes"] = 0
            ctx.log("扫描后背包界面不可确认，重新检查；未确认挖空。", level="warn")
            self._goto(rec, S_DIG_OPEN_BAG)
            return
        rec["empty_passes"] += 1
        rec["scan_failures"] = 0
        rec["bag_scan"] = None
        if rec["empty_passes"] < 2:
            ctx.log("完整扫描未发现藏宝图（1/2），稍后从顶部再次确认。")
            return  # 保持状态，让出给其他号；下轮独立从顶部复查。
        ctx.log("背包连续两轮完整扫描均无藏宝图，准备关闭并确认。")
        self._begin_bag_cleanup(rec, "completed")

    def _capture_bag(self, ctx, list_region, threshold):
        """同一帧确认背包并裁出物品区；每次观察都更新输入会话证据。"""
        window_rect = ctx.window.rect()
        rect = calibrated.region_rect(ctx, self.name, "bag_list", list_region)
        if not list_region or not rect or not window_rect:
            return None, None
        x, y, w, h = rect
        wx, wy, ww, wh = window_rect
        if w <= 0 or h <= 0 or x < wx or y < wy or x + w > wx + ww or y + h > wy + wh:
            return None, None
        cur = win_mod.grab(window_rect)
        if self._bag_visible(ctx, threshold, scene=cur) is not True:
            return None, None
        if cur is None or cur.shape[0] < y - wy + h or cur.shape[1] < x - wx + w:
            return None, None
        return cur[y - wy:y - wy + h, x - wx:x - wx + w], rect

    def _begin_bag_cleanup(self, rec, outcome):
        rec["bag_scan"] = None
        rec["cleanup_outcome"] = outcome
        rec["close_attempts"] = 0
        self._goto(rec, S_DIG_CLOSE_BAG)

    def finish_chain_driver(self, ctx, rec):
        """总超时或外层异常时尽力收尾；主动停止后绝不补发输入。"""
        if ctx.should_stop() or rec["done"] or not rec["phase_b"]:
            return
        loop = ctx.task_cfg(self.name)["loop"]
        with _input_policy(ctx, self.name):
            try:
                if not ctx.window.activate():
                    raise RuntimeError("无法激活目标窗口")
                if rec["state"] not in (S_DIG_CLOSE_BAG, S_DIG_WAIT_CLOSED):
                    self._begin_bag_cleanup(rec, "failed")
                # 关闭最多三次，每次等待最多 2 秒；另有固定迭代上限防配置过大。
                for _ in range(32):
                    if ctx.should_stop() or rec["done"]:
                        return
                    self._do_bag_cleanup(ctx, rec, loop, loop["match_threshold"])
                    if rec["done"] or ctx.should_stop():
                        return
                    self._interruptible_sleep(ctx, 0.25)
            except Exception as exc:
                ctx.log(f"背包收尾异常：{exc}。", level="error")
        if not ctx.should_stop():
            rec["done"], rec["unsafe_ui"], rec["outcome"] = True, True, "failed"
            ctx.log("背包收尾未确认，阻止该号继续执行。", level="error")

    def _do_bag_cleanup(self, ctx, rec, loop, threshold):
        if ctx.should_stop():
            return
        visible = self._bag_visible(ctx, threshold)
        if ctx.should_stop():
            return
        if visible is False:
            rec["outcome"] = rec["cleanup_outcome"] or "failed"
            rec["done"] = True
            ctx.log("已确认背包关闭。", level="hit")
            return
        timeout = max(0.1, loop.get("bag_close_timeout_sec", 2.0))
        if rec["state"] == S_DIG_WAIT_CLOSED and self._state_elapsed(rec) < timeout:
            return
        if rec["close_attempts"] >= 3:
            rec["outcome"], rec["done"], rec["unsafe_ui"] = "failed", True, True
            ctx.log("无法确认背包关闭，已阻止该号后续任务；其余号继续。", level="error")
            return
        rec["close_attempts"] += 1
        # 不可观察时不盲按 Esc；等待下一次有效观察，仍受重试上限约束。
        if visible is True:
            self._close_bag(ctx)
        if not ctx.should_stop():
            self._goto(rec, S_DIG_WAIT_CLOSED)

    # ---- 阶段 B：挖宝监控（盯「下一张使用」续挖；长时间无弹窗且静止→这批挖完，回开背包确认）----
    def _do_digging(self, ctx, rec, loop, regions, threshold):
        per_map_timeout = loop.get("dig_timeout_sec", 120)
        idle_need = loop.get("collect_idle_sec", 4.0)
        still_diff = loop.get("still_diff", _STILL_DIFF)
        scene_rect = self._scene_rect(ctx, regions)
        cur = win_mod.grab(scene_rect)
        if cur is None:
            rec["last"], rec["still_since"] = None, None
            if time.time() - rec["t0"] > per_map_timeout:
                self._recover_window(ctx, rec, loop, regions)
            return

        nxt = self._match_scene(cur, scene_rect, "flag_next_map", threshold)
        if nxt is not None:
            if ctx.mouse.click(nxt[0], nxt[1]) is False:
                self._recover_window(ctx, rec, loop, regions)
                return
            rec["dug"] += 1
            ctx.log(f"第 {rec['dug']} 次点击「下一张使用」继续挖宝。", level="hit")
            rec["t0"], rec["last"], rec["still_since"] = time.time(), None, None
            self._interruptible_sleep(ctx, self._jitter(1.0, ctx))
            return

        in_battle = self._in_battle(cur, loop, threshold)
        diff = self._frame_diff(rec["last"], cur) if rec["last"] is not None else None
        if in_battle:
            rec["t0"], rec["last"], rec["still_since"] = time.time(), None, None   # 战斗中刷新计时
        else:
            if diff is not None and diff < still_diff:
                if rec["still_since"] is None:
                    rec["still_since"] = time.time()
                elif time.time() - rec["still_since"] >= idle_need:
                    ctx.log("无更多「下一张」且画面静止 → 这批可能挖完，回开背包确认。")
                    self._goto(rec, S_DIG_OPEN_BAG)
                    return
            else:
                rec["still_since"] = None
            rec["last"] = cur

        self._log_motion_diag(ctx, rec, "挖宝中", diff, still_diff, idle_need, in_battle)
        if time.time() - rec["t0"] > per_map_timeout:
            ctx.log("单张挖宝超时，回开背包确认。", level="warn")
            self._goto(rec, S_DIG_OPEN_BAG)

    # ------------------------------------------------------------------
    # 卡死兜底恢复
    # ------------------------------------------------------------------
    def _recover_window(self, ctx, rec, loop, regions):
        """某号卡死兜底：截图存证 + 计数；超上限放弃该号。
        已进挖宝阶段→重开背包继续挖；否则回开活动重试（避免在背包内反复重开活动）。"""
        if ctx.should_stop():
            return
        rec["empty_passes"] = 0
        rec["bag_scan"] = None
        rec["recover"] += 1
        ctx.log(f"卡住（第 {rec['recover']}/{loop.get('max_stuck_recover', 3)} 次），尝试恢复…",
                level="warn")
        self._save_capture(self._grab_scene(ctx, regions), f"stuck_{rec['state']}")
        if rec["recover"] >= loop.get("max_stuck_recover", 3):
            ctx.log("多次恢复仍无进展，先关闭背包再异常退出；未确认挖空。", level="error")
            self._begin_bag_cleanup(rec, "failed")
            return
        # 按一次 Esc 清掉异常弹窗，再回合适的阶段起点
        self._focus(ctx)
        if ctx.send_hotkey("close_panel"):
            self._interruptible_sleep(ctx, self._jitter(0.25, ctx))
        rec["scrolls"] = 0
        self._goto(rec, S_DIG_OPEN_BAG if rec["phase_b"] else self._start_state)

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------
    @staticmethod
    def _bag_asset(ctx):
        """模板和标定来源一起选择；保留专属图优先、无法加载时复用整理背包的规则。"""
        tc = ctx.task_cfg("treasure_map")
        path = tc.get("templates", {}).get("flag_bag_open")
        if path:
            template = vision.load_template(path)
            if template is not None:
                return template, ("treasure_map", "flag_bag_open")
        shared = ctx.cfg.get("tasks", {}).get("organize_bag", {}).get("templates", {})
        template = vision.load_template(shared.get("sort_button")) if shared.get("sort_button") else None
        return template, ("organize_bag", "sort_button")

    @staticmethod
    def _bag_template(ctx):
        return TreasureMapTask._bag_asset(ctx)[0]

    def _load_bag_template(self, ctx):
        template, source = self._bag_asset(ctx)
        self._template_sources = {"flag_bag_open": source}
        return template

    def _bag_visible(self, ctx, threshold, *, scene=_UNSET_SCENE):
        """True=固定背包标志可见，False=有效画面未命中，None=不可观察。"""
        self._calibration_ctx = ctx
        if self.flags.get("flag_bag_open") is None:
            return None
        rect = ctx.window.rect()
        cur = (win_mod.grab(rect) if rect else None) if scene is _UNSET_SCENE else scene
        if cur is None or cur.size == 0 or cur.ndim != 3 or cur.std(axis=(0, 1)).max() < 1.0:
            return None
        return self._present(cur, "flag_bag_open", threshold)

    def _log_summary(self, ctx, records, reason, elapsed_min):
        completed = 0
        for rec in records:
            outcome = rec.get("outcome")
            label = rec["ctx"].label or "该号"
            state = _STATE_CN.get(rec["state"], rec["state"])
            if outcome == "completed":
                completed += 1
                ctx.log(f"{label}：确认挖空。", level="hit")
            else:
                if outcome == "failed":
                    status = "异常退出"
                elif reason == "stopped" or ctx.should_stop():
                    status = "手动停止"
                elif reason == "timeout" or outcome == "timeout":
                    status = "超时"
                else:
                    status = "异常结束"
                ctx.log(f"{label}：{status}，未确认挖空，停在「{state}」。", level="warn")
        if completed == len(records):
            ctx.log("所有号均已确认挖空。")
        else:
            ctx.log(f"确认挖空 {completed}/{len(records)} 个号，其余号未确认挖空。", level="warn")
        # dug 沿用点击「下一张」的旧计数，不再将其误写成客户端实际消耗张数。
        ctx.log(f"已停止。点击「下一张使用」共 {sum(r['dug'] for r in records)} 次，"
                f"用时 {elapsed_min:.1f} 分钟。")

    def _focus(self, ctx):
        """把游戏窗口切到前台——键盘快捷键(SendInput)只发给有焦点的窗口，发键前必须先激活，
        否则 Alt+E 之类会发给助手界面而不是游戏。"""
        try:
            ctx.window.activate()
        except Exception:
            pass

    def _log_motion_diag(self, ctx, rec, label, diff, still_diff, idle_need, in_battle):
        """每 ~5s 打印一次实时帧差诊断，便于据此调 still_diff（计时存在 rec['t_diag']）。"""
        now = time.time()
        if now - (rec.get("t_diag") or 0.0) < 5.0:
            return
        d = f"{diff:.1f}" if diff is not None else "—"
        held = (now - rec["still_since"]) if rec["still_since"] else 0.0
        extra = "，战斗中" if in_battle else ""
        ctx.log(f"{label}…帧差 {d}（静止阈值 {still_diff}，已静止 {held:.1f}/{idle_need}s）{extra}")
        rec["t_diag"] = now

    def _calib_size(self, ctx, key=None):
        """有 key 时取最终模板自己的标定尺寸，包含通用继承及旧配置归属兜底。
        未传 key 时保留激活尺寸组/targets.base_size 的旧接口；均无记录则返回 None，
        匹配保持原始像素尺度。只缩放内存画面，不调整游戏窗口。
        """
        try:
            if key is not None:
                return calibrated.item_size(ctx, self.name, "templates", key)
            return calib.active_size(ctx.cfg)
        except Exception:
            return None

    def _load_flags(self, tc):
        templates = tc.get("templates", {})
        return {k: vision.load_template(templates.get(k)) if templates.get(k) else None
                for k in _FLAG_KEYS}

    def _scene_rect(self, ctx, regions):
        self._calibration_ctx = ctx
        region = regions.get("scene")
        return calibrated.region_rect(ctx, self.name, "scene", region)

    def _grab_scene(self, ctx, regions):
        rect = self._scene_rect(ctx, regions)
        return win_mod.grab(rect) if rect else None

    def _present(self, scene, flag_key, threshold):
        tpl = self.flags.get(flag_key)
        if scene is None or tpl is None:
            return False
        return calibrated.match_flag(self, scene, tpl, flag_key, threshold) is not None

    def _match_scene(self, cur, scene_rect, flag_key, threshold):
        """在整张 scene 里匹配 flag_key，命中返回屏幕绝对 (x,y,score)，否则 None。"""
        tpl = self.flags.get(flag_key)
        if cur is None or tpl is None or scene_rect is None:
            return None
        m = calibrated.match_flag(self, cur, tpl, flag_key, threshold)
        if m is None:
            return None
        return (scene_rect[0] + m[0], scene_rect[1] + m[1], m[2])

    def _best_score(self, cur, flag_key):
        """不受阈值限制取 flag_key 在 cur 里的最佳相似度（用 0 阈值），用于诊断「弹了但分低」。
        cur/模板缺失或尺寸不够返回 None。"""
        tpl = self.flags.get(flag_key)
        if cur is None or tpl is None:
            return None
        m = calibrated.match_flag(self, cur, tpl, flag_key, 0.0)
        return m[2] if m is not None else None

    def _close_bag(self, ctx):
        """收尾关背包：聚焦后按一次「关闭面板」(Esc)。"""
        if ctx.should_stop():
            return False
        self._focus(ctx)
        if ctx.send_hotkey("close_panel"):
            self._interruptible_sleep(ctx, self._jitter(0.25, ctx))
            return not ctx.should_stop()
        ctx.log("背包关闭输入失败，暂不结束该号。", level="warn")
        return False

    def _dry_run_selfcheck(self, ctx, contexts, multi, regions, threshold, switch_delay,
                           deadline, skip_collect=False):
        """演练：周期性对【每个号】当前屏幕识别各标志，报告命中，便于用户验证模板/阈值。
        已有宝图(skip_collect)时只自检挖宝相关标志，不提阶段A的宝图入口/听听无妨/对话框。"""
        if skip_collect:
            keys = [("flag_battle", "战斗"), ("flag_next_map", "下一张使用"),
                    ("treasure_item", "藏宝图"), ("flag_bag_open", "背包已打开")]
        else:
            keys = [("flag_treasure_entry", "宝图入口"), ("flag_join", "参加按钮"),
                    ("flag_tingting", "听听无妨"), ("flag_battle", "战斗"),
                    ("flag_next_map", "下一张使用"), ("treasure_item", "藏宝图"),
                    ("flag_bag_open", "背包已打开")]
        loop = ctx.task_cfg(self.name).get("loop", {})
        while not ctx.should_stop():
            if deadline and time.time() >= deadline:
                ctx.log("演练时间上限到，停止。")
                break
            for wctx in contexts:
                if ctx.should_stop():
                    break
                if wctx.window.rect() is None:
                    continue
                # 最小化或被遮挡不算窗口消失；演练扫描前也要先还原并置前。
                if not wctx.window.activate():
                    continue
                scene = self._grab_scene(wctx, regions)
                found = []
                for key, label in keys:
                    tpl = self.flags.get(key)
                    if tpl is None:
                        continue
                    flag_threshold = min(threshold, loop.get("battle_threshold", 0.80)) if key == "flag_battle" else threshold
                    hit = calibrated.match_flag(self, scene, tpl, key, flag_threshold, wctx)
                    if hit is not None:
                        found.append(f"{label}({hit[2]:.2f})")
                if found:
                    wctx.log("识别到：" + "、".join(found), level="hit")
                else:
                    wctx.log("当前屏幕未识别到任何已标定标志（请打开对应界面再看）。")
                if multi and len(contexts) > 1:
                    self._interruptible_sleep(ctx, self._jitter(switch_delay, ctx))
            self._interruptible_sleep(ctx, self._jitter(1.5, ctx))
