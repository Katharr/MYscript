# -*- coding: utf-8 -*-
"""
组队（一键组队）。

把「队长建队→队员申请→队长接受→双方关窗」这套组队握手单独做成一个可运行动作：
在「通用 / 工具」页选好谁当队长、点「一键组队」即把所选多开窗口组成一队（建好即停）。
任何副本本身都先组队再跑，这里只是把「单纯组个队」抽出来方便手动用。

组队逻辑全在可复用的 core.teaming.TeamFormation 里（副本/师门/帮派等都复用同一套）；
本任务只负责：校验前置条件、按 captain_index 给所选窗口分配角色、把活儿交给 TeamFormation。

组队的标定（模板/区域）以及本动作的角色参数（captain_index / dry_run）都放共享命名空间
tasks.teaming——组队是跨任务共享能力，参数也随它走，与具体副本解耦。
（注意：任务名仍是 "dungeon" 只为兼容历史 get_task("dungeon")；「刷副本」页本身改成副本中枢，
不再运行本任务，而是运行被选中的副本，见 gui/app.py DungeonPage。）
"""

from ..core import vision
from ..core.teaming import (TeamFormation, TEAM_REQUIRED_REGIONS, TEAM_REQUIRED_TEMPLATES)
from .base import Task, register

_PARAM_NS = "teaming"   # 角色参数（captain_index/dry_run）与组队标定共用 teaming 命名空间


@register
class DungeonTask(Task):
    name = "dungeon"
    title = "组队"
    description = "把所选多开窗口自动组成一队（队长建队→队员申请→接受→关窗），组队成功即结束"

    # 本任务自身无标定项——组队资产标定走共享 teaming 命名空间（GUI 的「标定（组队）」按钮）
    CALIBRATION = {"regions": [], "templates": [], "watchlist": False}

    # ------------------------------------------------------------------
    def preflight(self, ctx):
        problems = []
        targets = ctx.cfg.get("targets", {})
        wins = ctx.select_windows()
        if not targets.get("multi"):
            problems.append("组队需多开模式：请在「选择窗口」切到多开并选 2~5 个号")
        if len(wins) < 2:
            problems.append(f"组队至少 2 人，当前选中 {len(wins)} 个号（队长+≥1 队员）")
        elif len(wins) > 5:
            problems.append(f"最多 5 人，当前选中 {len(wins)} 个号，请减少")

        dc = ctx.task_cfg(_PARAM_NS)
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
        dc = ctx.task_cfg(_PARAM_NS)
        dry_run = dc.get("dry_run", False)
        cap = dc.get("captain_index", 0)
        wins = ctx.select_windows()
        if len(wins) < 2:
            ctx.log("选中窗口不足 2 个（组队至少队长+1 队员），已停止。", level="error")
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

        ctx.log(f"★ 一键组队：队长=号{cap + 1}，队员 {len(wins) - 1} 人 ★", level="warn")
        if dry_run:
            ctx.log("演练模式：只对各号识别组队标志、打日志，不发快捷键/不点。", level="warn")

        team_cfg = ctx.task_cfg("teaming")
        team = TeamFormation(ctx, assignments, team_cfg, dry_run=dry_run)
        ok, reason = team.run_until_formed()
        if dry_run:
            return
        if ok:
            ctx.log("✔ 组队完成。", level="hit")
        else:
            ctx.log(f"组队未完成（{reason}）。", level="warn")
