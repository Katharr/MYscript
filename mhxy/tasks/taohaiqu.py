# -*- coding: utf-8 -*-
"""
副本·蹈海去（50级）。第一支「组队 + 副本内剧情战斗」的完整副本任务。

用户拍板的两条总约束：
  ① 任何副本都建立在【组队】的前提下——本任务先复用 core.teaming.TeamFormation 把队组好，再跑副本流程；
  ② 副本流程【只有队长操作】——队员只在队伍里被传送 + 自动战斗，全程不点。故组队成功后是
     【队长单角色线性流程】，不需要多开轮转（轮转是给「各号各跑同一状态机」的任务用的）。

用户口述的真实流程（队长视角，一遍跑完即收尾）：
  开活动 → 点「蹈海去」卡片右侧「参加」→ 角色寻路到 NPC 弹对话框 → 点「选择副本」
  → 点蹈海去【下方】的「进入」（几个进入长得一样，靠位置区分，只在比例框里找）
  → 点「跳过剧情动画」→ 快捷键开任务弹窗 → 点「日常」→ 点中蹈海去任务 → 点「马上传送」→ 弹框点「竖子尔敢！」→ 第1场战斗
  → 战斗结束点「跳过剧情动画」→ 开任务弹窗 → 点「日常」→ 点中蹈海去任务 → 「马上传送」→ 弹框点「恕难从命」→ 第2场战斗
  → 战斗结束点「跳过剧情动画」→ 开任务弹窗 → 点「日常」→ 点中蹈海去任务 → 「马上传送」→ 弹框点「与尔一战！」→ 第3场战斗
  → 战斗结束点「跳过剧情动画」→ 再点一下屏幕 → 点「小闹钟」结束副本。

关键观察（决定怎么等战斗）：每场战斗【结束后】都会再弹「跳过剧情动画」按钮——所以
「等这场战斗打完」= 轮询下一个「跳过剧情」按钮重新出现（自然信号，不用额外标定战斗/结束标志）。
故三场战斗的公共节奏抽成一个循环：[等并点跳过 → 开任务弹窗 →（可选）点「日常」分类 → 在任务列表点选蹈海去任务 → 马上传送 → 点本场对话选项]，
三场只是对话选项不同（竖子尔敢/恕难从命/与尔一战）。循环后收尾第三场的跳过 + 点屏幕 + 小闹钟。
（注意：开任务弹窗后【不能直接传送】，要先在任务列表里点中蹈海去这条任务，「马上传送」才对应它；
「日常」分类是可选步骤——检测到就先点它列出蹈海去任务，没检测到就跳过、直接点蹈海去任务。）

安全默认 dry_run=true：不组队、不发快捷键/不点，只对各号识别组队+副本相关模板做自检，先验证模板/阈值。
"""

import time

from ..core import scan
from ..core import vision
from ..core import window as win_mod
from ..core.teaming import (TeamFormation, TEAM_REQUIRED_REGIONS, TEAM_REQUIRED_TEMPLATES,
                            DISBAND_REQUIRED_TEMPLATES)
from .base import Task, register

# 三场战斗前各自要点的对话选项（顺序固定）。
_DIALOGS = [("thq_opt1", "竖子尔敢！"), ("thq_opt2", "恕难从命"), ("thq_opt3", "与尔一战！")]


@register
class TaohaiquTask(Task):
    name = "taohaiqu"
    title = "蹈海去·50"
    description = "组队后由队长跑完整条蹈海去(50级)副本：参加→选副本→进入→三场剧情战斗→小闹钟收尾，跑一遍即停"
    is_dungeon = True       # 「刷副本」页收录它作为可选副本之一
    # 本副本自身模板键（thq_ 前缀，存盘 templates/tm_thq_*.png，避免与别的任务同名互相覆盖）。
    _FLAG_KEYS = ["thq_entry", "activity_join", "thq_select", "thq_enter", "thq_skip",
                  "thq_daily", "thq_task", "thq_teleport", "thq_opt1", "thq_opt2", "thq_opt3", "thq_clock"]
    # 预检必选的模板（thq_daily 运行时可选，不强制标定）。
    _REQUIRED_FLAGS = ["thq_entry", "activity_join", "thq_select", "thq_enter", "thq_skip",
                       "thq_task", "thq_teleport", "thq_opt1", "thq_opt2", "thq_opt3", "thq_clock"]

    CALIBRATION = {
        "regions": [
            *Task.BASE_CALIBRATION_REGIONS,
        ],
        "templates": [
            ("thq_entry", "活动卡片入口", "活动列表里「蹈海去」那张卡片，框图标+文字、要独特"),
            ("thq_select", "「选择副本」按钮", "寻路到 NPC 后对话框里的「选择副本」按钮"),
            ("thq_enter", "蹈海去「进入」按钮", "副本列表里蹈海去【下方】的「进入」按钮。几个进入长得一样，"
                                          "运行时只在比例框 enter_box 里找它（默认整屏；点错就收窄到蹈海去那块）"),
            ("thq_skip", "「跳过剧情动画」按钮", "剧情动画时角落的「跳过」按钮——每场战斗前后都点它；它的重新出现也用来判战斗结束"),
            ("thq_daily", "任务弹窗·「日常」按钮", "开任务弹窗后先点的「日常」分类按钮——点它才会列出蹈海去那条任务"),
            ("thq_task", "任务列表·蹈海去条目", "点「日常」后，任务列表里「蹈海去」那一条任务——先点中它，「马上传送」才对应它"),
            ("thq_teleport", "「马上传送」按钮", "任务弹窗里、点中蹈海去任务后出现的「马上传送」按钮"),
            ("thq_opt1", "对话「竖子尔敢！」", "第 1 场战斗前弹框里的「竖子尔敢！」选项"),
            ("thq_opt2", "对话「恕难从命」", "第 2 场战斗前弹框里的「恕难从命」选项"),
            ("thq_opt3", "对话「与尔一战！」", "第 3 场战斗前弹框里的「与尔一战！」选项"),
            ("thq_clock", "结束「小闹钟」", "副本结束时点的小闹钟按钮（点它收尾）"),
        ],
        "watchlist": False,
    }

    # ------------------------------------------------------------------
    def preflight(self, ctx):
        problems = []
        tc = ctx.task_cfg(self.name)
        targets = ctx.cfg.get("targets", {})
        wins = ctx.select_windows()
        skip_team = tc.get("skip_team", False)   # 「已组队」=跳过组队，直接由队长跑副本

        if skip_team:
            # 已组队：只需队长那个号能定位；不要求多开、不查组队资产
            if not wins:
                problems.append("没找到/没选中目标窗口 —— 请先「选择窗口」选好队长所在的号")
        else:
            # 组队前提：必须多开、至少 2 个号（队长 + ≥1 队员）
            if not targets.get("multi"):
                problems.append("蹈海去需先组队：请在「选择窗口」切到多开并选好队长+队员（≥2 个号）")
            if len(wins) < 2:
                problems.append(f"组队至少 2 人（队长+队员），当前选中 {len(wins)} 个号")

        cap = tc.get("captain_index", 0)
        if wins and not (0 <= cap < len(wins)):
            problems.append(f"队长序号 号{cap + 1} 越界（共 {len(wins)} 个号），请在下拉框重选队长")

        # 组队资产（共享 teaming 命名空间）——已组队则不需要
        if not skip_team:
            team_tc = ctx.task_cfg("teaming")
            for rk in TEAM_REQUIRED_REGIONS:
                if not team_tc.get("regions", {}).get(rk):
                    problems.append(f"组队区域『{rk}』未标定 —— 请在「通用」页点「标定（组队）」框选")
            for tk in TEAM_REQUIRED_TEMPLATES:
                p = team_tc.get("templates", {}).get(tk)
                if not p or vision.load_template(p) is None:
                    problems.append(f"组队模板『{tk}』缺失 —— 请在「通用」页点「标定（组队）」裁图")

        # 自动解散队伍：勾了就需要共享 teaming 的「退出队伍」模板（不论是否 skip_team）
        if tc.get("auto_disband", False):
            team_tc = ctx.task_cfg("teaming")
            for tk in DISBAND_REQUIRED_TEMPLATES:
                p = team_tc.get("templates", {}).get(tk)
                if not p or vision.load_template(p) is None:
                    problems.append(f"勾了「跑完解散队伍」但退队模板『{tk}』缺失 —— 请在「通用」页点「标定（组队）」框选「退出队伍」")

        # 副本自身模板/区域
        regions = tc.get("regions", {})
        if not regions.get("activity_list"):
            problems.append("『活动列表区域』未标定 —— 请在本页「标定」里框选")
        templates = tc.get("templates", {})
        for tk in self._REQUIRED_FLAGS:
            p = templates.get(tk)
            if not p or vision.load_template(p) is None:
                problems.append(f"副本模板『{tk}』缺失或加载失败 —— 请在本页「标定」里框选裁图")

        if not ctx.hotkeys.get("open_activity"):
            problems.append("缺快捷键 open_activity（如 alt+c）—— 请在设置里填")
        if not ctx.hotkeys.get("open_task"):
            problems.append("缺快捷键 open_task（任务弹窗，如 alt+y）—— 请在设置里填")
        if not skip_team:
            if not ctx.hotkeys.get("open_team"):
                problems.append("缺快捷键 open_team（如 alt+t）—— 组队要用")
            if not ctx.hotkeys.get("open_friend"):
                problems.append("缺快捷键 open_friend（如 alt+f）—— 组队要用")

        sizes = {tuple(w.rect()[2:4]) for w in wins if w.rect()}
        if len(sizes) > 1:
            ctx.log("提示：所选号尺寸不一致，多开共用标定可能点偏，建议统一分辨率。", level="warn")

        return (len(problems) == 0), problems

    # ------------------------------------------------------------------
    def run(self, ctx):
        tc = ctx.task_cfg(self.name)
        loop = tc["loop"]
        regions = tc["regions"]
        threshold = loop["match_threshold"]
        dry_run = tc.get("dry_run", True)
        skip_team = tc.get("skip_team", False)   # 「已组队」=跳过组队，直接由队长跑副本
        cap = tc.get("captain_index", 0)
        self.flags = self._load_flags(tc)

        wins = ctx.select_windows()
        if not wins:
            ctx.log("没找到/没选中目标窗口，已停止。", level="error")
            return
        if not skip_team and len(wins) < 2:
            ctx.log("选中窗口不足 2 个（组队至少队长+1 队员）；若已自行组好队，请勾选「已组队」。", level="error")
            return
        if not (0 <= cap < len(wins)):
            cap = 0

        if not self._is_admin():
            ctx.log("⚠ 当前非管理员权限：游戏在前台时鼠标/键盘注入可能被 UIPI 拦截，建议以管理员重开。",
                    level="warn")

        # 按 captain_index 分配角色（队长放第 0 位，便于组队握手尽快收敛）。
        cap_child = None
        member_pairs = []
        for i, w in enumerate(wins):
            child = ctx.make_child(w, f"号{i + 1}")
            if i == cap:
                cap_child = child
            else:
                member_pairs.append((child, TeamFormation.ROLE_MEMBER))
        assignments = [(cap_child, TeamFormation.ROLE_CAPTAIN)] + member_pairs

        if dry_run:
            ctx.log("演练模式：不组队、不发快捷键/不点，只对各号识别组队+副本模板做自检。", level="warn")
            self._dry_run_selfcheck(ctx, assignments, regions, threshold)
            return

        # —— 第一步：组队（已勾选「已组队」则跳过，直接由队长跑）——
        if skip_team:
            ctx.log(f"★ 蹈海去·50：已组队，跳过组队，直接由队长（号{cap + 1}）跑副本 ★", level="warn")
        else:
            ctx.log(f"★ 蹈海去·50：先组队（队长=号{cap + 1}，队员 {len(wins) - 1} 人），再由队长跑副本 ★",
                    level="warn")
            team_cfg = ctx.task_cfg("teaming")
            team = TeamFormation(ctx, assignments, team_cfg, dry_run=False)
            ok, reason = team.run_until_formed()
            if ctx.should_stop():
                return
            if not ok:
                ctx.log(f"组队未完成（{reason}），蹈海去中止。", level="error")
                return
            ctx.log("组队完成，队长开始跑蹈海去副本流程…", level="hit")

        # —— 第二步：队长跑副本流程 ——
        self._interruptible_sleep(ctx, self._jitter(0.8, ctx))
        self._run_dungeon(cap_child, loop, regions, threshold)

        # —— 第三步（可选）：副本跑完后自动解散队伍（所有号退队，不分队长队员同一套流程）——
        if tc.get("auto_disband", False) and not ctx.should_stop():
            ctx.log("副本结束，自动解散队伍（所有号退队）…", level="warn")
            self._interruptible_sleep(ctx, self._jitter(0.8, ctx))
            team_cfg = ctx.task_cfg("teaming")
            team = TeamFormation(ctx, assignments, team_cfg, dry_run=False)
            ok, _ = team.run_disband()
            if ok:
                ctx.log("队伍已解散。", level="hit")

    # ------------------------------------------------------------------
    # 队长副本流程（线性、阻塞式；每个轮询里勤查 should_stop）
    # ------------------------------------------------------------------
    def _run_dungeon(self, ctx, loop, regions, threshold):
        self._focus(ctx)
        npc_to = loop.get("npc_dialog_sec", 60)
        step_to = loop.get("step_timeout_sec", 30)
        daily_to = loop.get("daily_wait_sec", 1)     # 「日常」可选步骤的短等待：打开任务列表就有它，没检测到就快速跳过、直接点蹈海去
        battle_to = loop.get("battle_timeout_sec", 600)

        # 前缀：开活动 → 参加 → 选副本 → 进入
        if not self._open_and_join(ctx, loop, regions, threshold):
            return
        if not self._click_when(ctx, "thq_select", "选择副本", regions, threshold, npc_to):
            ctx.log("等「选择副本」对话框超时（角色可能还没寻路到 NPC），中止。", level="error")
            return
        if not self._click_enter(ctx, loop, regions, threshold, step_to):
            ctx.log("没点到蹈海去「进入」，中止。", level="error")
            return

        # 三场剧情战斗：公共节奏 [等并点跳过 → 开任务弹窗 → 马上传送 → 点本场对话选项]
        skip_to = loop.get("entry_skip_sec", 60)   # 第 1 个跳过跟在「进入」后的传送动画后，给短一点
        for i, (opt_key, opt_label) in enumerate(_DIALOGS, start=1):
            if not self._click_when(ctx, "thq_skip", f"跳过剧情({i})", regions, threshold, skip_to):
                ctx.log(f"第 {i} 段等「跳过剧情」超时，中止。", level="error")
                return
            self._focus(ctx)
            if not ctx.send_hotkey("open_task"):
                ctx.log("打不开任务弹窗（open_task 未配置），中止。", level="error")
                return
            self._interruptible_sleep(ctx, self._jitter(0.6, ctx))
            # 开任务弹窗后【不能直接传送】：先尝试点「日常」分类、再在列表点中蹈海去这条任务，「马上传送」才对应它。
            # 「日常」是可选步骤——检测到就点(它才会列出蹈海去任务)；没检测到就跳过、直接点蹈海去任务。
            if not self._click_when(ctx, "thq_daily", "日常", regions, threshold, daily_to):
                ctx.log("没检测到任务弹窗「日常」按钮，直接点蹈海去任务。", level="warn")
            if not self._click_when(ctx, "thq_task", "任务列表·蹈海去", regions, threshold, step_to):
                ctx.log("等任务列表里「蹈海去」任务条目超时，中止。", level="error")
                return
            if not self._click_when(ctx, "thq_teleport", "马上传送", regions, threshold, step_to):
                ctx.log("等「马上传送」超时，中止。", level="error")
                return
            if not self._click_when(ctx, opt_key, opt_label, regions, threshold, step_to):
                ctx.log(f"等对话「{opt_label}」超时，中止。", level="error")
                return
            ctx.log(f"第 {i} 场战斗已发起，等它打完…", level="hit")
            skip_to = battle_to     # 后续跳过都跟在一场战斗后，给足时长

        # 收尾：第三场战斗结束的跳过 → 再点一下屏幕 → 小闹钟
        if not self._click_when(ctx, "thq_skip", "跳过剧情(收尾)", regions, threshold, battle_to):
            ctx.log("等收尾「跳过剧情」超时，中止。", level="error")
            return
        self._interruptible_sleep(ctx, self._jitter(0.6, ctx))
        self._click_scene_center(ctx, regions)          # 「再点一下屏幕」
        self._interruptible_sleep(ctx, self._jitter(0.6, ctx))
        if self._click_when(ctx, "thq_clock", "小闹钟", regions, threshold, step_to):
            ctx.log("★ 蹈海去·50 完成，已点小闹钟收尾。★", level="hit")
        else:
            ctx.log("没点到「小闹钟」（副本可能已自动结束）。流程结束。", level="warn")

    # ---- 开活动 → 找蹈海去卡片 → 点「参加」----
    def _open_and_join(self, ctx, loop, regions, threshold):
        self._focus(ctx)
        if not ctx.send_hotkey("open_activity"):
            ctx.log("打不开活动界面（open_activity 未配置），中止。", level="error")
            return False
        ctx.log("已打开活动，翻找蹈海去卡片…")
        self._interruptible_sleep(ctx, self._jitter(0.6, ctx))
        list_region = regions.get("activity_list")

        def grab_rect():
            rect = (ctx.window.region_to_screen_rect(list_region)
                    if list_region else ctx.window.rect())
            return rect

        def probe(scene, rect):
            hit = vision.match(scene, self.flags.get("thq_entry"), threshold) if scene is not None else None
            if hit is None:
                return scan.SCROLL, None
            entry_xy = (rect[0] + hit[0], rect[1] + hit[1])
            join = self._find_join_on_row(ctx, list_region, entry_xy, threshold, loop,
                                            "activity_join", "thq_entry")
            if join is not None:
                ctx.mouse.click(join[0], join[1])
                ctx.log(f"找到蹈海去卡片（{hit[2]:.3f}）→ 点「参加」（{join[2]:.3f}），等寻路到 NPC。", level="hit")
                return scan.ACCEPT, join
            ctx.log("认出卡片但没找到右侧「参加」（检查 thq_join 模板/阈值）。", level="warn")
            return scan.STAY, None

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
        if res.found:
            self._interruptible_sleep(ctx, self._jitter(0.5, ctx))
            return True
        if res.stopped:
            return False
        ctx.log("翻找蹈海去卡片多次未果，中止。", level="error")
        return False

    # ---- 点蹈海去「进入」：几个进入长得一样，只在比例框 enter_box 里找（靠位置区分）----
    def _click_enter(self, ctx, loop, regions, threshold, timeout):
        tpl = self.flags.get("thq_enter")
        if tpl is None:
            return False
        box = loop.get("enter_box") or [0.0, 0.0, 1.0, 1.0]   # [x0,y0,x1,y1] 比例；默认整屏
        deadline = time.time() + timeout
        while not ctx.should_stop():
            hit = self._match_subregion(ctx, regions, tpl, threshold,
                                        (box[0], box[2]), (box[1], box[3]))
            if hit is not None:
                ctx.mouse.click(hit[0], hit[1])
                ctx.log(f"点蹈海去「进入」（{hit[2]:.3f}）。", level="hit")
                self._interruptible_sleep(ctx, self._jitter(0.6, ctx))
                return True
            if time.time() > deadline:
                return False
            self._interruptible_sleep(ctx, self._jitter(0.4, ctx))
        return False

    # ------------------------------------------------------------------
    # 通用：轮询等某模板出现就点它（命中返回 True；超时返回 False）。全程勤查 should_stop。
    # ------------------------------------------------------------------
    def _click_when(self, ctx, flag_key, label, regions, threshold, timeout):
        deadline = time.time() + timeout
        last_diag = 0.0
        while not ctx.should_stop():
            scene_rect = self._scene_rect(ctx, regions)
            cur = win_mod.grab(scene_rect) if scene_rect else None
            hit = self._match_scene(cur, scene_rect, flag_key, threshold)
            if hit is not None:
                ctx.mouse.click(hit[0], hit[1])
                ctx.log(f"点「{label}」（{hit[2]:.3f}）。", level="hit")
                self._interruptible_sleep(ctx, self._jitter(0.4, ctx))
                return True
            now = time.time()
            if now > deadline:
                return False
            if now - last_diag >= 15.0:
                ctx.log(f"等「{label}」…（已 {now - (deadline - timeout):.0f}/{timeout:.0f}s）")
                last_diag = now
            self._interruptible_sleep(ctx, self._jitter(0.5, ctx))
        return False

    def _click_scene_center(self, ctx, regions):
        rect = self._scene_rect(ctx, regions)
        if rect is None:
            return
        cx, cy = rect[0] + rect[2] // 2, rect[1] + rect[3] // 2
        ctx.mouse.click(cx, cy)
        ctx.log("再点一下屏幕（推进收尾）。")

    # ------------------------------------------------------------------
    # 演练：周期性对每个号识别其相关模板（队长=副本模板，队员=组队队员模板），验证模板/阈值
    # ------------------------------------------------------------------
    def _dry_run_selfcheck(self, ctx, assignments, regions, threshold):
        cap_keys = [(k, k) for k in self._FLAG_KEYS]
        multi = ctx.cfg.get("targets", {}).get("multi", False)
        switch_delay = ctx.cfg.get("targets", {}).get("switch_delay_sec", 0.15)
        while not ctx.should_stop():
            for wctx, role in assignments:
                if ctx.should_stop():
                    break
                if wctx.window.rect() is None:
                    continue
                if multi:
                    wctx.window.activate()
                scene = self._grab_scene(wctx, regions)
                found = []
                if role == TeamFormation.ROLE_CAPTAIN:
                    for key, label in cap_keys:
                        tpl = self.flags.get(key)
                        if tpl is None or scene is None:
                            continue
                        hit = vision.match(scene, tpl, threshold)
                        if hit is not None:
                            found.append(f"{label}({hit[2]:.2f})")
                if found:
                    wctx.log("识别到：" + "、".join(found), level="hit")
                else:
                    role_txt = "队长" if role == TeamFormation.ROLE_CAPTAIN else "队员"
                    wctx.log(f"[{role_txt}] 当前屏幕未识别到副本标志（请切到对应界面再看）。")
                if multi and len(assignments) > 1:
                    self._interruptible_sleep(ctx, self._jitter(switch_delay, ctx))
            self._interruptible_sleep(ctx, self._jitter(1.5, ctx))

    # ------------------------------------------------------------------
    # 识别/点击工具：统一走 Task 基类（_focus/_load_flags/_scene_rect/_grab_scene/
    #   _match_scene/_match_subregion/_find_join_on_row），本文件不再各自存一份。
    # ------------------------------------------------------------------

