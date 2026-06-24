# -*- coding: utf-8 -*-
"""
刷副本（第一版：只做组队）。

刷副本至少 3 人，第一版用 1 大号带 N 小号：在 GUI 指定一个号当队长、其余当队员，
自动完成组队（队长建队→队员申请→队长接受→双方关窗），组队成功即结束（副本内战斗流程下一版做）。

组队逻辑全在可复用的 core.teaming.TeamFormation 里（未来师门/帮派等任务都可复用）；
本任务只负责：校验前置条件、按 captain_index 给所选窗口分配角色、把活儿交给 TeamFormation。

组队的标定（模板/区域）走共享命名空间 tasks.teaming（GUI「标定（组队）」按钮）；
本任务自身只存角色参数（captain_index / dry_run）在 tasks.dungeon。
"""

from ..core import vision
from ..core.teaming import (TeamFormation, TEAM_REQUIRED_REGIONS, TEAM_REQUIRED_TEMPLATES)
from .base import Task, register


@register
class DungeonTask(Task):
    name = "dungeon"
    title = "刷副本"
    description = "第一版：1 大号带 N 小号自动组队（队长建队→队员申请→接受→关窗），组队成功即结束"

    # 本任务自身无标定项——组队资产标定走共享 teaming 命名空间（GUI 的「标定（组队）」按钮）
    CALIBRATION = {"regions": [], "templates": [], "watchlist": False}

    # ------------------------------------------------------------------
    def preflight(self, ctx):
        problems = []
        targets = ctx.cfg.get("targets", {})
        wins = ctx.select_windows()
        if not targets.get("multi"):
            problems.append("刷副本组队需多开模式：请在「选择窗口」切到多开并选 3~5 个号")
        if len(wins) < 3:
            problems.append(f"组队至少 3 人，当前选中 {len(wins)} 个号（1 大号带≥2 小号）")
        elif len(wins) > 5:
            problems.append(f"最多 5 人，当前选中 {len(wins)} 个号，请减少")

        dc = ctx.task_cfg(self.name)
        cap = dc.get("captain_index", 0)
        if wins and not (0 <= cap < len(wins)):
            problems.append(f"队长序号 号{cap + 1} 越界（共 {len(wins)} 个号），请在下拉框重选队长")

        # 组队模板/区域（共享 teaming 命名空间）
        team_tc = ctx.task_cfg("teaming")
        for rk in TEAM_REQUIRED_REGIONS:
            if not team_tc.get("regions", {}).get(rk):
                problems.append(f"组队区域『{rk}』未标定 —— 请点「标定（组队）」框选")
        for tk in TEAM_REQUIRED_TEMPLATES:
            p = team_tc.get("templates", {}).get(tk)
            if not p or vision.load_template(p) is None:
                problems.append(f"组队模板『{tk}』缺失 —— 请点「标定（组队）」框选裁图")

        if not ctx.hotkeys.get("open_team"):
            problems.append("缺快捷键 open_team（如 alt+t）—— 请在设置里填")
        if not ctx.hotkeys.get("open_friend"):
            problems.append("缺快捷键 open_friend（如 alt+f）—— 请在设置里填")

        # 尺寸一致性仅提示（多开共用组队标定）
        sizes = {tuple(w.rect()[2:4]) for w in wins if w.rect()}
        if len(sizes) > 1:
            ctx.log("提示：所选号尺寸不一致，多开共用组队标定可能点偏，建议统一分辨率。", level="warn")

        return (len(problems) == 0), problems

    # ------------------------------------------------------------------
    def run(self, ctx):
        dc = ctx.task_cfg(self.name)
        dry_run = dc.get("dry_run", True)
        cap = dc.get("captain_index", 0)
        wins = ctx.select_windows()
        if len(wins) < 3:
            ctx.log("选中窗口不足 3 个，已停止。", level="error")
            return
        if not (0 <= cap < len(wins)):
            cap = 0

        if not self._is_admin():
            ctx.log("⚠ 当前非管理员权限：游戏在前台时鼠标/键盘注入可能被 UIPI 拦截，建议以管理员重开。",
                    level="warn")

        # 按 captain_index 分配角色 + 派生子上下文（队长放第 0 位，便于握手尽快收敛）。
        cap_pair = None
        member_pairs = []
        for i, w in enumerate(wins):
            child = ctx.make_child(w, f"号{i + 1}")
            if i == cap:
                cap_pair = (child, TeamFormation.ROLE_CAPTAIN)
            else:
                member_pairs.append((child, TeamFormation.ROLE_MEMBER))
        assignments = [cap_pair] + member_pairs

        ctx.log(f"★ 刷副本·组队：队长=号{cap + 1}，队员 {len(wins) - 1} 人 ★", level="warn")
        if dry_run:
            ctx.log("演练模式：只对各号识别组队标志、打日志，不发快捷键/不点。", level="warn")

        team_cfg = ctx.task_cfg("teaming")
        team = TeamFormation(ctx, assignments, team_cfg, dry_run=dry_run)
        ok, reason = team.run_until_formed()
        if dry_run:
            return
        if ok:
            ctx.log("✔ 组队完成。第一版到此结束（副本内战斗流程下一版做）。", level="hit")
        else:
            ctx.log(f"组队未完成（{reason}）。", level="warn")
