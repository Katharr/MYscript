# -*- coding: utf-8 -*-
"""
刷副本 · 宝图任务（两阶段状态机，支持多开逐号轮转）。

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

from ..core import vision
from ..core import window as win_mod
from .base import Task, register

# 每个号的状态机状态（非阻塞：每访问一次只推进一步）
S_OPEN_ACTIVITY = "OPEN_ACTIVITY"     # 阶段A：发活动快捷键
S_FIND_CARD = "FIND_CARD"             # 阶段A：活动列表里滚轮找「宝图任务」卡片 → 点「参加」
S_DIALOG = "DIALOG"                   # 阶段A：等 NPC 对话框 → 点「听听无妨」
S_COLLECTING = "COLLECTING"           # 阶段A：自动寻宝中，盯人物静止=收集完成
S_DIG_OPEN_BAG = "DIG_OPEN_BAG"       # 阶段B：发开背包快捷键
S_DIG_FIND = "DIG_FIND"               # 阶段B：背包里滚轮找藏宝图 → 双击用；翻完无图=该号挖完
S_DIGGING = "DIGGING"                 # 阶段B：挖宝中，盯「下一张使用」续挖 / 静止判这批挖完

# 状态→中文（仅用于停止汇总「某号停在哪一步」的可读提示）
_STATE_CN = {
    S_OPEN_ACTIVITY: "开活动", S_FIND_CARD: "找宝图卡片/点参加", S_DIALOG: "等NPC对话框/点听听无妨",
    S_COLLECTING: "收集寻宝中", S_DIG_OPEN_BAG: "开背包", S_DIG_FIND: "翻背包找藏宝图", S_DIGGING: "挖宝中",
}

_STILL_DIFF = 8.0   # 帧差低于此视为画面静止（人物不动）的默认阈值——可被 loop.still_diff 覆盖。
#   太小→人物待机动画/周围NPC玩家走动/特效会让整屏帧差长期偏高，永远判不到「静止」→收集/挖宝误超时。
#   运行时日志会实时打印真实帧差，照着把阈值设到「静止时帧差」之上、「走动时帧差」之下即可。

# 必备模板（缺失则 preflight 阻断）与可选模板（缺失仅 warn）
#   flag_join=活动列表里「宝图任务」那一行右侧的「参加」按钮——按行匹配点它（不是点条目本身）。
_FLAG_KEYS = ["flag_treasure_entry", "flag_join", "flag_tingting",
              "flag_battle", "flag_next_map", "treasure_item"]
_REQUIRED_FLAGS = ["flag_treasure_entry", "flag_join", "flag_tingting",
                   "flag_next_map", "treasure_item"]


@register
class TreasureMapTask(Task):
    name = "treasure_map"
    title = "刷副本·宝图"
    description = "自动开活动→收藏宝图→挖宝→领奖，一条龙（战斗交给游戏自动，支持多开轮转）"

    CALIBRATION = {
        "regions": [
            ("scene", "主识别区", "留空=整个窗口当识别区(推荐)；战斗/对话/下一张等标志都在这里找", True),
            ("activity_list", "活动列表区域", "「活动」界面里那片列表，滚轮在此翻找「宝图任务」条目"),
            ("bag_list", "背包列表区域", "背包里道具格那片区域，滚轮在此翻找藏宝图"),
        ],
        "templates": [
            ("flag_treasure_entry", "宝图任务入口", "活动列表里「宝图任务」那一条，框图标+文字、要独特"),
            ("flag_join", "参加按钮", "活动列表里「宝图任务」那一行右侧的「参加」按钮，框按钮本身、要独特"),
            ("flag_tingting", "「听听无妨」选项", "和 NPC 对话弹框里要点的那个选项"),
            ("flag_next_map", "「下一张使用」按钮", "挖完一张后游戏自动弹出的继续按钮"),
            ("treasure_item", "藏宝图道具", "背包里藏宝图那个图标的样子"),
            ("flag_battle", "战斗界面标志(可选)", "战斗独有的画面元素，用于避免战斗期被误判卡死"),
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
            return

        if dry_run:
            if self._skip_collect:
                hint = "打开背包，看日志能否认出 藏宝图/下一张/战斗"
            else:
                hint = "手动打开对应界面，看日志能否认出 宝图入口/参加按钮/听听无妨/战斗/下一张/藏宝图"
            ctx.log("演练模式：不会真正推进副本，仅对各号当前屏幕循环做『各标志识别自检』。"
                    + hint + ("（多号逐个扫描）" if multi else "") + "。", level="warn")
            self._dry_run_selfcheck(ctx, contexts, multi, regions, threshold, switch_delay,
                                    deadline, self._skip_collect)
            return

        if self._skip_collect:
            ctx.log("★ 实战模式（已有宝图）：跳过领取，直接开背包挖包裹里的藏宝图 ★", level="warn")
        else:
            ctx.log("★ 实战模式：会真开活动、真用宝图、真领奖 ★", level="warn")
        ctx.log(f"★ {('多开轮转 ' + str(len(contexts)) + ' 个号' if multi else '单号')}，"
                "号与号之间逐步轮转；每号终止条件=背包藏宝图挖空 ★", level="warn")
        if time_limit > 0:
            ctx.log(f"时间上限 {time_limit} 分钟（到点自停）。主终止条件是各号背包藏宝图挖空。")

        records = [self._new_record(c) for c in contexts]
        while not ctx.should_stop():
            if deadline and time.time() >= deadline:
                ctx.log(f"已达时间上限 {time_limit} 分钟，停止。")
                break
            if all(r["done"] for r in records):
                ctx.log("所有号都已挖空/结束。")
                break

            active = [r for r in records if not r["done"]]
            for rec in records:
                if ctx.should_stop():
                    break
                if rec["done"]:
                    continue
                wctx = rec["ctx"]
                if wctx.window.rect() is None:
                    if not rec["dead_logged"]:
                        wctx.log("目标窗口不见了，跳过该号（其余号继续）。", level="warn")
                        rec["dead_logged"] = True
                    continue
                rec["dead_logged"] = False
                # 操作某号前先切前台，确保点击/快捷键落在这个号身上（多开必须；单开也无害）
                if multi:
                    wctx.window.activate()
                    if wctx.should_stop():
                        break
                # 推进这个号一小步
                self._step_once(wctx, rec, loop, regions, threshold)
                # 号与号之间留个小间隔，别太机械
                if multi and len(active) > 1:
                    self._interruptible_sleep(ctx, self._jitter(switch_delay, ctx))

            # 一整轮（所有号各推进一步）之间留间隔（带抖动）
            self._interruptible_sleep(ctx, self._jitter(tick, ctx))

        total = sum(r["dug"] for r in records)
        ctx.log(f"已停止。共挖宝图 {total} 张，用时 {(time.time() - start_ts) / 60:.1f} 分钟。")
        # 报告未挖完的号停在哪一步，便于排查「某号落后/没挖」（多开节奏慢或对话框没认出都会卡在等待步）
        unfinished = [r for r in records if not r["done"]]
        if unfinished:
            desc = "、".join(
                f"{(r['ctx'].label or '该号')}停在「{_STATE_CN.get(r['state'], r['state'])}」"
                f"（已 {self._state_elapsed(r):.0f}s）" for r in unfinished)
            ctx.log("未挖完：" + desc + "。若卡在『等对话框』，多为多开节奏慢(传送没到)或"
                    "「听听无妨」相似度低于阈值——看上面诊断行的最佳相似度，必要时重标 flag_tingting "
                    "或调大 dialog_timeout_sec。", level="warn")

    # ------------------------------------------------------------------
    # 多开轮转：上下文与每号状态记录
    # ------------------------------------------------------------------
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

    def _new_record(self, wctx):
        """每个号一份独立状态。轮转时按 state 各推进一步，互不干扰。
        phase_b：是否已进入挖宝阶段（决定卡死兜底回开活动还是回重开背包）。
        last/still_since/t0/t_diag：收集/挖宝监控的逐帧状态（原内部 while 循环搬到这里、跨访问保留）。"""
        return {"ctx": wctx, "state": self._start_state, "t_state": time.time(),
                "scrolls": 0, "dug": 0, "phase_b": self._skip_collect,
                "last": None, "still_since": None, "t0": 0.0, "t_diag": 0.0,
                "recover": 0, "done": False, "dead_logged": False}

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
        elif st == S_DIG_FIND:
            self._do_dig_find(ctx, rec, loop, regions, threshold)
        elif st == S_DIGGING:
            self._do_digging(ctx, rec, loop, regions, threshold)

    # ---- 阶段 A：开活动 ----
    def _do_open_activity(self, ctx, rec, loop, regions, threshold):
        self._focus(ctx)
        if not ctx.send_hotkey("open_activity"):
            ctx.log("打不开活动界面（open_activity 快捷键未配置），放弃该号。", level="error")
            rec["done"] = True
            return
        ctx.log("已打开活动，滚轮翻找「宝图任务」…")
        self._interruptible_sleep(ctx, self._jitter(0.6, ctx))
        rec["scrolls"] = 0
        self._goto(rec, S_FIND_CARD)

    # ---- 阶段 A：找卡片 → 点「参加」（每访问一次：找不到就滚一屏，超 scroll_max_tries 屏则恢复）----
    def _do_find_card(self, ctx, rec, loop, regions, threshold):
        list_region = regions.get("activity_list")
        rect = (ctx.window.region_to_screen_rect(list_region)
                if list_region else ctx.window.rect())
        if rect is None:
            return
        scene = win_mod.grab(rect)
        hit = (vision.match(scene, self.flags.get("flag_treasure_entry"), threshold)
               if scene is not None else None)
        if hit is not None:
            cx, cy, score = hit
            entry_xy = (rect[0] + cx, rect[1] + cy)
            join = self._find_join_on_row(ctx, list_region, entry_xy, threshold, loop)
            if join is not None:
                ctx.mouse.click(join[0], join[1])
                ctx.log(f"找到「宝图任务」（{score:.3f}）→ 点「参加」（{join[2]:.3f}），开始传送找 NPC。",
                        level="hit")
                self._goto(rec, S_DIALOG)
                return
            ctx.log("认出「宝图任务」但没找到右侧「参加」（检查 flag_join 模板/阈值）。", level="warn")
        else:
            # 没找到：在列表中心向下滚一屏（settle 交给轮转间隔），下次再找
            cx_c, cy_c = rect[0] + rect[2] // 2, rect[1] + rect[3] // 2
            ctx.mouse.scroll(loop.get("scroll_step", -3), cx_c, cy_c)
        rec["scrolls"] += 1
        if rec["scrolls"] > max(1, loop.get("scroll_max_tries", 8)):
            ctx.log("活动列表里翻找「宝图任务」多次未果。", level="warn")
            self._recover_window(ctx, rec, loop, regions)

    # ---- 阶段 A：等对话框 → 点「听听无妨」（超时则恢复）----
    def _do_dialog(self, ctx, rec, loop, regions, threshold):
        timeout = loop.get("dialog_timeout_sec", 30)
        scene_rect = self._scene_rect(ctx, regions)
        cur = win_mod.grab(scene_rect)
        hit = self._match_scene(cur, scene_rect, "flag_tingting", threshold)
        if hit is not None:
            ctx.mouse.click(hit[0], hit[1])
            ctx.log(f"选择「听听无妨」（{hit[2]:.3f}），开始自动寻宝。", level="hit")
            self._enter_monitor(rec, S_COLLECTING)
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
            ctx.log("等 NPC 对话框超时。", level="warn")
            self._recover_window(ctx, rec, loop, regions)

    # ---- 阶段 A：收集监控（每访问一次扫一帧；人物持续静止=收集完成，转挖宝）----
    def _do_collecting(self, ctx, rec, loop, regions, threshold):
        idle_need = loop.get("collect_idle_sec", 4.0)
        overall = loop.get("collect_timeout_sec", 600)
        still_diff = loop.get("still_diff", _STILL_DIFF)
        if time.time() - rec["t0"] > overall:
            # 超时不回退阶段A（活动已参加、按钮已变「已参加」，重开会连环卡死）；
            # 按「已收集完」直接转挖宝——挖不到图时阶段B会干净收尾。
            ctx.log(f"收集超过 {overall:.0f}s 仍没判到静止（多半是 still_diff 偏小），"
                    "按已收集完处理，转入挖宝。", level="warn")
            self._goto_dig(rec)
            return
        scene_rect = self._scene_rect(ctx, regions)
        cur = win_mod.grab(scene_rect)
        in_battle = self._present(cur, "flag_battle", threshold)
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

    # ---- 阶段 B：开背包 ----
    def _do_dig_open_bag(self, ctx, rec, loop, regions, threshold):
        self._focus(ctx)
        if not ctx.send_hotkey("open_bag"):
            ctx.log("打不开背包（缺 open_bag 快捷键），放弃该号。", level="error")
            rec["done"] = True
            return
        ctx.log("打开背包，翻找藏宝图…")
        self._interruptible_sleep(ctx, self._jitter(0.6, ctx))
        rec["phase_b"] = True
        rec["scrolls"] = 0
        self._goto(rec, S_DIG_FIND)

    # ---- 阶段 B：找藏宝图 → 双击用；翻完整背包都没有=该号挖完（关背包结束）----
    def _do_dig_find(self, ctx, rec, loop, regions, threshold):
        list_region = regions.get("bag_list")
        rect = (ctx.window.region_to_screen_rect(list_region)
                if list_region else ctx.window.rect())
        if rect is None:
            return
        scene = win_mod.grab(rect)
        hit = (vision.match(scene, self.flags.get("treasure_item"), threshold)
               if scene is not None else None)
        if hit is not None:
            cx, cy, score = hit
            ctx.mouse.double_click(rect[0] + cx, rect[1] + cy)
            ctx.log(f"双击使用藏宝图（{score:.3f}），自动传送挖宝。")
            self._enter_monitor(rec, S_DIGGING)
            return
        # 没找到：在背包列表中心向下滚一屏，下次再找
        cx_c, cy_c = rect[0] + rect[2] // 2, rect[1] + rect[3] // 2
        ctx.mouse.scroll(loop.get("scroll_step", -3), cx_c, cy_c)
        rec["scrolls"] += 1
        if rec["scrolls"] > max(1, loop.get("scroll_max_tries", 8)):
            ctx.log("背包已无藏宝图 → 全部挖完，关上背包。", level="hit")
            self._close_bag(ctx)
            rec["done"] = True

    # ---- 阶段 B：挖宝监控（盯「下一张使用」续挖；长时间无弹窗且静止→这批挖完，回开背包确认）----
    def _do_digging(self, ctx, rec, loop, regions, threshold):
        per_map_timeout = loop.get("dig_timeout_sec", 120)
        idle_need = loop.get("collect_idle_sec", 4.0)
        still_diff = loop.get("still_diff", _STILL_DIFF)
        scene_rect = self._scene_rect(ctx, regions)
        cur = win_mod.grab(scene_rect)

        nxt = self._match_scene(cur, scene_rect, "flag_next_map", threshold)
        if nxt is not None:
            ctx.mouse.click(nxt[0], nxt[1])
            rec["dug"] += 1
            ctx.log(f"挖完第 {rec['dug']} 张，点「下一张使用」继续。", level="hit")
            rec["t0"], rec["last"], rec["still_since"] = time.time(), None, None
            self._interruptible_sleep(ctx, self._jitter(1.0, ctx))
            return

        in_battle = self._present(cur, "flag_battle", threshold)
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
        rec["recover"] += 1
        ctx.log(f"卡住（第 {rec['recover']}/{loop.get('max_stuck_recover', 3)} 次），尝试恢复…",
                level="warn")
        self._save_capture(self._grab_scene(ctx, regions), f"stuck_{rec['state']}")
        if rec["recover"] >= loop.get("max_stuck_recover", 3):
            ctx.log("多次卡死仍无进展，放弃该号。", level="error")
            rec["done"] = True
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

    def _find_join_on_row(self, ctx, list_region, entry_screen_xy, threshold, loop):
        """在「宝图任务」条目所在【那张卡片】的右侧条带里匹配「参加」按钮(flag_join)。
        命中返回 (screen_x, screen_y, score)，否则 None。
        按行+只取条目右侧、且限制在条目所属卡片列内，能抗滚动、抗「一排多张卡片」时
        扫进右邻卡片点到它的「参加」按钮（活动列表默认两张卡片一排）。"""
        join_tpl = self.flags.get("flag_join")
        entry_tpl = self.flags.get("flag_treasure_entry")
        if join_tpl is None:
            ctx.log("找「参加」失败：flag_join 模板未标定。", level="warn")
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
        # 行条带高度：取条目模板高 ×2，下限 40px；纵向以条目中心为中线
        row_h = entry_tpl.shape[0] if entry_tpl is not None else 40
        band = max(40, int(row_h * 2))
        sh, sw = scene.shape[:2]
        # 换算到 scene 局部坐标：纵向取条带、横向从条目中心到列表右缘（只看右侧）
        ey_local = int(ey - ry)
        ex_local = int(ex - rx)
        # 活动列表是「每排多张卡片」(默认两张一排)：参加按钮只在【条目所属那张卡片】内。
        # 若一路扫到列表右缘(x1=sw)，右邻卡片的「参加」按钮会被一并扫进来、甚至胜出，
        # 导致点到右边卡片的参加。故把列表按列等分，定位条目所在列，x1 收到该列右边界。
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

    def _match_scene(self, cur, scene_rect, flag_key, threshold):
        """在整张 scene 里匹配 flag_key，命中返回屏幕绝对 (x,y,score)，否则 None。"""
        tpl = self.flags.get(flag_key)
        if cur is None or tpl is None or scene_rect is None:
            return None
        m = vision.match(cur, tpl, threshold)
        if m is None:
            return None
        return (scene_rect[0] + m[0], scene_rect[1] + m[1], m[2])

    def _best_score(self, cur, flag_key):
        """不受阈值限制取 flag_key 在 cur 里的最佳相似度（用 0 阈值），用于诊断「弹了但分低」。
        cur/模板缺失或尺寸不够返回 None。"""
        tpl = self.flags.get(flag_key)
        if cur is None or tpl is None:
            return None
        m = vision.match(cur, tpl, 0.0)
        return m[2] if m is not None else None

    def _close_bag(self, ctx):
        """收尾关背包：聚焦后按一次「关闭面板」(Esc)。"""
        self._focus(ctx)
        if ctx.send_hotkey("close_panel"):
            self._interruptible_sleep(ctx, self._jitter(0.25, ctx))

    def _dry_run_selfcheck(self, ctx, contexts, multi, regions, threshold, switch_delay,
                           deadline, skip_collect=False):
        """演练：周期性对【每个号】当前屏幕识别各标志，报告命中，便于用户验证模板/阈值。
        已有宝图(skip_collect)时只自检挖宝相关标志，不提阶段A的宝图入口/听听无妨/对话框。"""
        if skip_collect:
            keys = [("flag_battle", "战斗"), ("flag_next_map", "下一张使用"),
                    ("treasure_item", "藏宝图")]
        else:
            keys = [("flag_treasure_entry", "宝图入口"), ("flag_join", "参加按钮"),
                    ("flag_tingting", "听听无妨"), ("flag_battle", "战斗"),
                    ("flag_next_map", "下一张使用"), ("treasure_item", "藏宝图")]
        while not ctx.should_stop():
            if deadline and time.time() >= deadline:
                ctx.log("演练时间上限到，停止。")
                break
            for wctx in contexts:
                if ctx.should_stop():
                    break
                if wctx.window.rect() is None:
                    continue
                if multi:
                    wctx.window.activate()
                scene = self._grab_scene(wctx, regions)
                found = []
                for key, label in keys:
                    tpl = self.flags.get(key)
                    if tpl is None:
                        continue
                    hit = vision.match(scene, tpl, threshold) if scene is not None else None
                    if hit is not None:
                        found.append(f"{label}({hit[2]:.2f})")
                if found:
                    wctx.log("识别到：" + "、".join(found), level="hit")
                else:
                    wctx.log("当前屏幕未识别到任何已标定标志（请打开对应界面再看）。")
                if multi and len(contexts) > 1:
                    self._interruptible_sleep(ctx, self._jitter(switch_delay, ctx))
            self._interruptible_sleep(ctx, self._jitter(1.5, ctx))
