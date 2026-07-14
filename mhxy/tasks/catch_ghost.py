# -*- coding: utf-8 -*-
"""
捉鬼任务（组队循环捉鬼；游戏自带自动寻路+自动战斗，脚本只做导航 + 监控 + 关键点击）。

完整流程（队长视角）：

  组队（队长发起组队，队员加入）→ 开「活动」
  → 滚轮找「捉鬼」条目 → 点【右侧的「参加」按钮】
  → NPC对话框弹出 → 点「捉鬼」选项（接任务按钮）
  → 开始捉鬼（自动寻路+自动战斗）
  → 一轮捉鬼结束 → 弹出「一轮结束」弹窗 → 点「确定」按钮
  → NPC对话框再次弹出 → 点「捉鬼」选项 → 循环...
  → 达到设定次数或时间上限 → 结束。

★ 组队前提：
  捉鬼建立在【组队】前提下，先复用 core.teaming.TeamFormation 组队。
  组队成功后是【队长单角色线性流程】，队员只在队伍里跟随，不操作。

★ 循环捉鬼：
  每轮的寻路+战斗由游戏自动完成，脚本只监控：
  ① NPC对话框（接任务按钮）
  ② 一轮结束弹窗（确定按钮）
  
停止：①达到设定轮数 ②时间上限分钟 ③手动停止/急停键。
安全默认 dry_run=true：不组队、不发快捷键/不点关键操作，只做模板识别自检。
"""

import time

from ..core import scan
from ..core import vision
from ..core import window as win_mod
from ..core.teaming import (TeamFormation, TEAM_REQUIRED_REGIONS, TEAM_REQUIRED_TEMPLATES,
                            DISBAND_REQUIRED_TEMPLATES)
from .base import Task, register

# 状态机状态（队长视角）
S_TEAM = "TEAM"                     # 组队（复用 TeamFormation）
S_OPEN_ACTIVITY = "OPEN_ACTIVITY"   # 发活动快捷键
S_FIND_CARD = "FIND_CARD"           # 滚轮找捉鬼条目 → 点参加
S_WAIT_NPC = "WAIT_NPC"             # 等NPC对话框（接任务按钮）
S_ACCEPT_TASK = "ACCEPT_TASK"       # 点接任务按钮
S_SELECT_TASK = "SELECT_TASK"       # 在任务列表里点「捉鬼」任务条目
S_CATCHING = "CATCHING"             # 捉鬼中：监控战斗状态/一轮结束弹窗
S_ROUND_END = "ROUND_END"           # 一轮结束弹窗 → 点确定
S_NEXT_ROUND = "NEXT_ROUND"         # 点确定后 → 回NPC对话框 → 判断是否继续

_REQUIRED_FLAGS = ["ghost_entry", "activity_join", "ghost_accept_task", "ghost_task", "ghost_round_end", "ghost_confirm"]


@register
class CatchGhostTask(Task):
    name = "catch_ghost"
    title = "捉鬼"
    description = "组队后由队长自动捉鬼：开活动→参加→NPC接任务→循环捉鬼（寻路+战斗交给游戏自动）"
    is_dungeon = False
    CHAINS_PER_WINDOW = True

    _FLAG_KEYS = [
        "ghost_entry",       # 捉鬼入口（活动列表里的图标+文字）
        "activity_join",     # 参加按钮（活动列表右侧，共用）
        "ghost_accept_task", # 接任务按钮（NPC对话框里的「捉鬼」选项）
        "ghost_task",        # 任务列表·捉鬼任务条目
        "ghost_round_end",   # 一轮结束弹窗
        "ghost_confirm",     # 确定按钮（一轮结束弹窗里）
        "ghost_battle",      # 战斗界面标志（可选）
    ]

    CALIBRATION = {
        "regions": [
            *Task.BASE_CALIBRATION_REGIONS,
        ],
        "templates": [
            *Task.BASE_CALIBRATION_TEMPLATES,
            ("ghost_entry", "捉鬼入口", "活动列表里「捉鬼」那一条，框图标+文字（左侧），不要框参加按钮"),
            ("ghost_accept_task", "接任务按钮", "NPC对话框里的「捉鬼」选项/按钮，用于接任务"),
            ("ghost_task", "任务列表·捉鬼任务条目", "点接任务按钮后，任务列表里「捉鬼」这一条任务——先点中它，才开始捉鬼"),
            ("ghost_round_end", "一轮结束弹窗", "一轮捉鬼结束后弹出的提示框（可框整个弹窗或独特部分）"),
            ("ghost_confirm", "确定按钮", "一轮结束弹窗里的「确定」按钮"),
            ("ghost_battle", "战斗界面标志(可选)", "战斗独有的画面元素，用于避免战斗期被误判卡死"),
        ],
        "watchlist": False,
    }

    def preflight(self, ctx):
        problems = []
        tc = ctx.task_cfg(self.name)
        targets = ctx.cfg.get("targets", {})
        wins = ctx.select_windows()
        skip_team = tc.get("skip_team", False)

        if skip_team:
            if not wins:
                problems.append("没找到/没选中目标窗口 —— 请先「选择窗口」选好队长所在的号")
        else:
            if not targets.get("multi"):
                problems.append("捉鬼需先组队：请在「选择窗口」切到多开并选好队长+队员（≥2 个号）")
            if len(wins) < 2:
                problems.append(f"组队至少 2 人（队长+队员），当前选中 {len(wins)} 个号")

        cap = tc.get("captain_index", 0)
        if wins and not (0 <= cap < len(wins)):
            problems.append(f"队长序号 号{cap + 1} 越界（共 {len(wins)} 个号），请在下拉框重选队长")

        # 组队资产检查（已组队则不需要）
        if not skip_team:
            team_tc = ctx.task_cfg("teaming")
            for rk in TEAM_REQUIRED_REGIONS:
                if not team_tc.get("regions", {}).get(rk):
                    problems.append(f"组队区域『{rk}』未标定 —— 请在「通用」页点「标定（组队）」")
            for tk in TEAM_REQUIRED_TEMPLATES:
                p = team_tc.get("templates", {}).get(tk)
                if not p or vision.load_template(p) is None:
                    problems.append(f"组队模板『{tk}』缺失 —— 请在「通用」页点「标定（组队）」")

        # 自动解散队伍检查
        if tc.get("auto_disband", False):
            team_tc = ctx.task_cfg("teaming")
            for tk in DISBAND_REQUIRED_TEMPLATES:
                p = team_tc.get("templates", {}).get(tk)
                if not p or vision.load_template(p) is None:
                    problems.append(f"自动解散勾选了，但「退出队伍」模板『{tk}』缺失 —— 请标定")

        # 捉鬼专属模板检查
        for fk in _REQUIRED_FLAGS:
            p = tc.get("templates", {}).get(fk)
            if not p or vision.load_template(p) is None:
                problems.append(f"捉鬼模板『{fk}』缺失 —— 请在本页点「标定」框选")

        # 活动列表区域检查
        if not tc.get("regions", {}).get("activity_list"):
            problems.append("『活动列表区域』未标定 —— 请在本页点「标定」框选")

        # 打开活动必须有快捷键配置
        if not ctx.hotkeys.get("open_activity"):
            problems.append("打开『活动』缺快捷键：请在 config.hotkeys.open_activity 填上（如 alt+c）")

        # 打开任务列表必须有快捷键配置
        if not ctx.hotkeys.get("open_task"):
            problems.append("打开『任务列表』缺快捷键：请在 config.hotkeys.open_task 填上（如 alt+y）")

        # 可选模板缺失只提示
        optional = ["ghost_battle"]
        for tk in optional:
            if not tc.get("templates", {}).get(tk) or vision.load_template(tc.get("templates", {}).get(tk)) is None:
                ctx.log(f"提示：可选模板『{tk}』未标定，将降级靠超时推进（可靠性略降）。", level="warn")

        return (len(problems) == 0), problems

    # ------------------------------------------------------------------
    def run(self, ctx):
        tc = ctx.task_cfg(self.name)
        loop = tc["loop"]
        regions = tc.get("regions", {})
        threshold = loop.get("match_threshold", 0.85)
        dry_run = tc.get("dry_run", True)
        skip_team = tc.get("skip_team", False)
        max_rounds = loop.get("max_rounds", 2)
        time_limit = loop.get("time_limit_min", 30)
        captain_idx = tc.get("captain_index", 0)
        auto_disband = tc.get("auto_disband", False)

        wins = ctx.select_windows()
        captain = wins[captain_idx] if wins and 0 <= captain_idx < len(wins) else None
        if captain is None:
            ctx.log("未找到队长窗口，中止。", level="error")
            return

        ctx.log(f"队长 [{captain.title}] 开始捉鬼，计划 {max_rounds} 轮。")

        # 加载模板
        self.flags = self._load_flags(tc)

        # 组队阶段
        if not skip_team:
            ctx.log("开始组队…", level="info")
            if dry_run:
                ctx.log("演练模式：跳过真实组队，仅检查组队模板。", level="warn")
            else:
                # 构建 assignments 列表
                cap_pair = None
                member_pairs = []
                for i, w in enumerate(wins):
                    child = ctx.make_child(w, f"号{i + 1}")
                    if i == cap:
                        cap_pair = (child, TeamFormation.ROLE_CAPTAIN)
                    else:
                        member_pairs.append((child, TeamFormation.ROLE_MEMBER))
                assignments = [cap_pair] + member_pairs
                team_cfg = ctx.task_cfg("teaming")
                tf = TeamFormation(ctx, assignments, team_cfg, dry_run=False)
                tf.run_teaming(ctx)
                ctx.log("组队成功，队长开始捉鬼流程。", level="hit")

        # 切到队长窗口（mouse是全局的，不用重新赋值）
        ctx.window = captain

        # 开始捉鬼主流程
        start_ts = time.time()
        deadline = start_ts + time_limit * 60 if time_limit > 0 else None
        tick = loop.get("tick_interval_sec", 0.5)

        state = S_OPEN_ACTIVITY
        rounds_done = 0
        t_state = time.time()
        t_round = time.time()

        while not ctx.should_stop():
            if deadline and time.time() >= deadline:
                ctx.log(f"已达时间上限 {time_limit} 分钟，停止。", level="warn")
                break

            # 状态机推进
            if state == S_OPEN_ACTIVITY:
                if self._open_activity(ctx, loop, regions, threshold, dry_run):
                    state = S_FIND_CARD
                    t_state = time.time()
                else:
                    ctx.log("打不开活动界面，中止。", level="error")
                    break

            elif state == S_FIND_CARD:
                if self._find_ghost_entry(ctx, loop, regions, threshold, dry_run):
                    state = S_WAIT_NPC
                    t_state = time.time()
                else:
                    ctx.log("找「捉鬼」入口失败，中止。", level="error")
                    break

            elif state == S_WAIT_NPC:
                # 等NPC对话框（接任务按钮）
                hit = self._wait_npc_dialog(ctx, regions, threshold, dry_run, timeout=10)
                if hit:
                    state = S_ACCEPT_TASK
                    t_state = time.time()
                else:
                    ctx.log("等NPC对话框超时，中止。", level="error")
                    break

            elif state == S_ACCEPT_TASK:
                # 点接任务按钮
                if self._click_accept_task(ctx, regions, threshold, dry_run):
                    state = S_SELECT_TASK  # 改为进入S_SELECT_TASK，而不是S_CATCHING
                    t_state = time.time()
                else:
                    ctx.log("点接任务按钮失败，中止。", level="error")
                    break

            elif state == S_SELECT_TASK:
                # 打开任务列表，然后点击「捉鬼」任务条目
                self._focus(ctx)
                if not ctx.send_hotkey("open_task"):
                    ctx.log("打不开任务列表（open_task 快捷键未配置），中止。", level="error")
                    break
                self._interruptible_sleep(ctx, 0.8)

                # 点击任务列表里的「捉鬼」任务条目
                if self._click_ghost_task(ctx, regions, threshold, dry_run, timeout=10):
                    state = S_CATCHING
                    t_state = time.time()
                    t_round = time.time()
                    rounds_done += 1
                    ctx.log(f"开始第 {rounds_done} 轮捉鬼。", level="hit")
                else:
                    ctx.log("点任务列表里「捉鬼」任务条目失败，中止。", level="error")
                    break

            elif state == S_CATCHING:
                # 监控捉鬼中：检测战斗状态和一轮结束弹窗
                scene_rect = self._scene_rect(ctx, regions)
                cur = win_mod.grab(scene_rect)

                # 检查战斗状态（可选）
                in_battle = self._present(cur, "ghost_battle", threshold)
                if in_battle:
                    # 战斗中，不计超时，继续监控（战斗持续时间长，降低检测频率）
                    battle_tick = loop.get("battle_check_interval_sec", 20.0)
                    ctx.log("战斗中，等待战斗结束…", level="info")
                    self._interruptible_sleep(ctx, battle_tick)
                    continue

                # 检查一轮结束弹窗
                hit_end = self._match_scene(cur, scene_rect, "ghost_round_end", threshold)
                if hit_end:
                    ctx.log(f"检测到「一轮结束」弹窗，准备点确定。", level="info")
                    state = S_ROUND_END
                    t_state = time.time()
                    continue

                # 正常捉鬼中，继续监控
                ctx.log("捉鬼进行中，等待一轮结束弹窗…", level="info")
                self._interruptible_sleep(ctx, tick)

            elif state == S_ROUND_END:
                # 点一轮结束弹窗的确定按钮
                if self._click_confirm(ctx, regions, threshold, dry_run):
                    # 点完确定后，判断是否继续下一轮
                    if rounds_done >= max_rounds:
                        ctx.log(f"已完成 {max_rounds} 轮，收尾。", level="hit")
                        break
                    else:
                        ctx.log(f"一轮结束，准备开始第 {rounds_done + 1} 轮。", level="info")
                        state = S_WAIT_NPC
                        t_state = time.time()
                else:
                    ctx.log("点确定按钮失败，中止。", level="error")
                    break

            self._interruptible_sleep(ctx, tick)

        # 结束后处理
        if auto_disband and not dry_run:
            ctx.log("自动解散队伍…", level="info")
            from .disband import DisbandTask
            DisbandTask().run(ctx)

        ctx.log(f"捉鬼完成，共 {rounds_done} 轮。")

    # ------------------------------------------------------------------
    def _open_activity(self, ctx, loop, regions, threshold, dry_run):
        """发活动快捷键，检测活动列表是否出现，失败重试"""
        # 激活窗口到前台
        self._focus(ctx)

        if dry_run:
            ctx.log("演练：跳过发活动快捷键。", level="warn")
            return True

        max_retries = loop.get("activity_open_retries", 3)
        retry_delay = loop.get("activity_open_delay_sec", 1.0)

        for attempt in range(1, max_retries + 1):
            ctx.log(f"发送活动快捷键（尝试 {attempt}/{max_retries}）…", level="info")
            if not ctx.send_hotkey("open_activity"):
                ctx.log("打不开活动界面（open_activity 快捷键未配置），中止。", level="error")
                return False
            self._interruptible_sleep(ctx, retry_delay)

            # 检测活动列表是否出现
            list_region = regions.get("activity_list")
            if list_region:
                rect = ctx.window.region_to_screen_rect(list_region)
                scene = win_mod.grab(rect) if rect else None
                if scene is not None:
                    # 尝试匹配任意一个活动入口模板（证明活动列表已打开）
                    for flag_key in ["ghost_entry", "activity_join"]:
                        tpl = self.flags.get(flag_key)
                        if tpl is not None:
                            hit = vision.match(scene, tpl, threshold)
                            if hit:
                                ctx.log(f"✓ 活动列表已打开（检测到活动卡片）。", level="info")
                                return True
                    # 没匹配到但截图成功，说明列表可能打开了但捉鬼不在当前屏
                    ctx.log("活动列表已打开（截图成功，未立即找到捉鬼卡片）。", level="info")
                    return True
            else:
                # 没有标定activity_list区域，无法检测，假设成功
                ctx.log("已发送活动快捷键（未标定活动列表区域，跳过检测）。", level="info")
                return True

            # 检测失败，准备重试
            if attempt < max_retries:
                ctx.log(f"活动列表未出现，{retry_delay}秒后重试…", level="warn")

        ctx.log(f"❌ 发送活动快捷键 {max_retries} 次后仍未检测到活动列表。", level="error")
        return False

    def _find_ghost_entry(self, ctx, loop, regions, threshold, dry_run):
        """滚轮找捉鬼条目 → 点参加"""
        list_region = regions.get("activity_list")
        entry_tpl = self.flags.get("ghost_entry")
        join_tpl = self.flags.get("activity_join")

        if entry_tpl is None:
            ctx.log("❌ ghost_entry 模板未加载：请先标定「捉鬼入口」。", level="error")
            return False
        if join_tpl is None:
            ctx.log("❌ activity_join 模板未加载：请先标定「参加按钮」。", level="error")
            return False

        def grab_rect():
            return (ctx.window.region_to_screen_rect(list_region)
                    if list_region else ctx.window.rect())

        def probe(scene, rect):
            if scene is None:
                return scan.SCROLL, None

            hit = vision.match(scene, entry_tpl, threshold)
            if hit is None:
                hit_low = vision.match(scene, entry_tpl, 0.70)
                if hit_low:
                    cx, cy, score = hit_low
                    ctx.log(f"⚠ 低阈值(0.70)匹配到疑似「捉鬼」（{score:.3f}<{threshold}），建议降低阈值。", level="warn")
                else:
                    ctx.log(f"❌ 未找到「捉鬼」（阈值 {threshold}），继续滚动…", level="info")
                return scan.SCROLL, None

            cx, cy, score = hit
            entry_xy = (rect[0] + cx, rect[1] + cy)
            ctx.log(f"✓ 找到「捉鬼」图标（{score:.3f}），坐标 {entry_xy}，开始找「参加」按钮…", level="info")

            join = self._find_join_on_row(ctx, list_region, entry_xy, threshold, loop,
                                          join_key="activity_join", entry_key="ghost_entry")
            if join is not None:
                if not dry_run:
                    ctx.mouse.click(join[0], join[1])
                    ctx.log(f"找到「捉鬼」→ 点「参加」（{join[2]:.3f}）。", level="hit")
                else:
                    ctx.log(f"演练：找到「捉鬼」和「参加」按钮（{join[2]:.3f}），跳过点击。", level="warn")
                return scan.ACCEPT, join

            ctx.log("认出「捉鬼」但没找到「参加」（检查 activity_join 模板/阈值）。", level="warn")
            return scan.STAY, None

        ctx.log(f"🔍 开始在活动列表搜索「捉鬼」（阈值 {threshold}）…")
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
        return res.found

    def _wait_npc_dialog(self, ctx, regions, threshold, dry_run, timeout=10):
        """等NPC对话框（接任务按钮）出现"""
        accept_tpl = self.flags.get("ghost_accept_task")
        if accept_tpl is None:
            ctx.log("❌ ghost_accept_task 模板未加载。", level="error")
            return None

        scene_rect = self._scene_rect(ctx, regions)
        start = time.time()

        while time.time() - start < timeout:
            if ctx.should_stop():
                return None

            cur = win_mod.grab(scene_rect)
            hit = vision.match(cur, accept_tpl, threshold)
            if hit:
                cx, cy, score = hit
                screen_xy = (scene_rect[0] + cx, scene_rect[1] + cy, score)
                ctx.log(f"✓ NPC对话框出现，找到「接任务」按钮（{score:.3f}）。", level="info")
                return screen_xy

            self._interruptible_sleep(ctx, 0.5)

        ctx.log(f"❌ 等NPC对话框超时（{timeout}秒）。", level="warn")
        return None

    def _click_accept_task(self, ctx, regions, threshold, dry_run):
        """点接任务按钮"""
        accept_tpl = self.flags.get("ghost_accept_task")
        if accept_tpl is None:
            ctx.log("❌ ghost_accept_task 模板未加载。", level="error")
            return False

        scene_rect = self._scene_rect(ctx, regions)
        cur = win_mod.grab(scene_rect)
        hit = vision.match(cur, accept_tpl, threshold)

        if hit is None:
            ctx.log("❌ 未找到「接任务」按钮。", level="error")
            return False

        cx, cy, score = hit
        screen_xy = (scene_rect[0] + cx, scene_rect[1] + cy)
        if not dry_run:
            ctx.mouse.click(screen_xy[0], screen_xy[1])
            ctx.log(f"点「接任务」按钮（{score:.3f}）。", level="hit")
        else:
            ctx.log(f"演练：找到「接任务」按钮（{score:.3f}），跳过点击。", level="warn")

        return True

    def _click_ghost_task(self, ctx, regions, threshold, dry_run, timeout):
        """点击任务列表里的「捉鬼」任务条目"""
        task_tpl = self.flags.get("ghost_task")
        if task_tpl is None:
            ctx.log("❌ ghost_task 模板未加载：请先标定「任务列表·捉鬼任务条目」。", level="error")
            return False

        t0 = time.time()
        scene_region = regions.get("scene")

        while time.time() - t0 < timeout:
            if ctx.stop_event.is_set():
                ctx.log("用户终止。", level="warn")
                return False

            rect = (ctx.window.region_to_screen_rect(scene_region)
                    if scene_region else ctx.window.rect())
            scene = win_mod.grab(rect)
            if scene is None:
                self._interruptible_sleep(ctx, 0.5)
                continue

            # 匹配任务列表里的捉鬼任务条目
            hit = vision.match(scene, task_tpl, threshold)
            if hit:
                cx, cy, score = hit
                screen_x = rect[0] + cx
                screen_y = rect[1] + cy

                if dry_run:
                    ctx.log(f"演练：点「捉鬼」任务条目({screen_x}, {screen_y})。", level="warn")
                    return True
                else:
                    ctx.mouse.click(screen_x, screen_y)
                    ctx.log(f"✓ 点击「捉鬼」任务条目（{score:.3f}）。", level="hit")
                    self._interruptible_sleep(ctx, 1.0)
                    return True

            self._interruptible_sleep(ctx, 0.5)

        ctx.log(f"未找到任务列表里的「捉鬼」任务条目（{timeout}秒超时）。", level="error")
        return False

    def _click_confirm(self, ctx, regions, threshold, dry_run):
        """点一轮结束弹窗的确定按钮"""
        confirm_tpl = self.flags.get("ghost_confirm")
        round_end_tpl = self.flags.get("ghost_round_end")

        if confirm_tpl is None:
            ctx.log("❌ ghost_confirm 模板未加载。", level="error")
            return False

        scene_rect = self._scene_rect(ctx, regions)
        cur = win_mod.grab(scene_rect)

        # 先确认一轮结束弹窗存在
        if round_end_tpl is not None and not self._present(cur, "ghost_round_end", threshold):
            ctx.log("⚠ 未检测到「一轮结束」弹窗，但仍尝试点确定。", level="warn")

        hit = vision.match(cur, confirm_tpl, threshold)
        if hit is None:
            ctx.log("❌ 未找到「确定」按钮。", level="error")
            return False

        cx, cy, score = hit
        screen_xy = (scene_rect[0] + cx, scene_rect[1] + cy)
        if not dry_run:
            ctx.mouse.click(screen_xy[0], screen_xy[1])
            ctx.log(f"点「确定」按钮（{score:.3f}）。", level="hit")
        else:
            ctx.log(f"演练：找到「确定」按钮（{score:.3f}），跳过点击。", level="warn")

        return True

    # ------------------------------------------------------------------
    def _interruptible_sleep(self, ctx, sec):
        interval = 0.1
        elapsed = 0.0
        while elapsed < sec and not ctx.should_stop():
            time.sleep(min(interval, sec - elapsed))
            elapsed += interval

    def _jitter(self, base, ctx):
        rng = ctx.cfg.get("rng", {})
        jitter = rng.get("jitter_sec", 0.15)
        import random
        return base + random.uniform(-jitter, jitter)