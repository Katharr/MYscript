# -*- coding: utf-8 -*-
"""
解散队伍（一键解散）。

把「每个号开队伍面板 → 找『退出队伍』点掉 → 关面板」这套退队动作单独做成一个可运行动作：
在「通用 / 工具」页点「一键解散」即让所选多开窗口里的每个号都退出当前队伍（所有人退队）。

退队流程对【每个窗口】都一样（不分队长队员），全在可复用的 core.teaming.TeamFormation.run_disband 里——
本任务只负责：校验前置条件、给所选窗口建上下文、把活儿交给 TeamFormation.run_disband。

标定（退出队伍按钮等）与组队共用 tasks.teaming 命名空间（GUI 的「标定（组队）」按钮里有这两项）。
任务名仍与组队解耦，单列一个 disband，便于「一键解散」单独触发、也供副本跑完后自动调用。
"""

from ..core import vision
from ..core.teaming import (TeamFormation, DISBAND_REQUIRED_TEMPLATES)
from .base import Task, register

_PARAM_NS = "teaming"   # 退队标定与角色参数共用 teaming 命名空间


@register
class DisbandTask(Task):
    name = "disband"
    title = "解散队伍"
    description = "让所选多开窗口里的每个号都退出当前队伍（开队伍面板→退出队伍→关面板），所有人退队即结束"

    # 本任务无自己的标定项——退队资产标定走共享 teaming 命名空间（GUI 的「标定（组队）」按钮）
    CALIBRATION = {"regions": [], "templates": [], "watchlist": False}

    # ------------------------------------------------------------------
    def preflight(self, ctx):
        problems = []
        wins = ctx.select_windows()
        if not wins:
            problems.append("没找到/没选中目标窗口 —— 请先「选择窗口」选好要退队的号")

        team_tc = ctx.task_cfg(_PARAM_NS)
        for tk in DISBAND_REQUIRED_TEMPLATES:
            p = team_tc.get("templates", {}).get(tk)
            if not p or vision.load_template(p) is None:
                problems.append(f"退队模板『{tk}』缺失 —— 请点「标定（组队）」框选「退出队伍」按钮")

        if not ctx.hotkeys.get("open_team"):
            problems.append("缺快捷键 open_team（如 alt+t）—— 请在设置里填")

        sizes = {tuple(w.rect()[2:4]) for w in wins if w.rect()}
        if len(sizes) > 1:
            ctx.log("提示：所选号尺寸不一致，多开共用标定可能点偏，建议统一分辨率。", level="warn")

        return (len(problems) == 0), problems

    # ------------------------------------------------------------------
    def run(self, ctx):
        dc = ctx.task_cfg(_PARAM_NS)
        dry_run = dc.get("dry_run", False)
        wins = ctx.select_windows()
        if not wins:
            ctx.log("没找到/没选中目标窗口，已停止。", level="error")
            return

        if not self._is_admin():
            ctx.log("⚠ 当前非管理员权限：游戏在前台时鼠标/键盘注入可能被 UIPI 拦截，建议以管理员重开。",
                    level="warn")

        # 退队不分角色：每个号都跑同一套流程。仍按 TeamFormation 的约定给 (ctx, role)，role 不影响退队。
        assignments = [(ctx.make_child(w, f"号{i + 1}"), TeamFormation.ROLE_MEMBER)
                       for i, w in enumerate(wins)]

        ctx.log(f"★ 一键解散：让所选 {len(wins)} 个号各自退出当前队伍 ★", level="warn")
        if dry_run:
            ctx.log("演练模式：只对各号识别退队标志、打日志，不发快捷键/不点。", level="warn")

        team_cfg = ctx.task_cfg("teaming")
        team = TeamFormation(ctx, assignments, team_cfg, dry_run=dry_run)
        ok, reason = team.run_disband()
        if dry_run:
            return
        if ok:
            ctx.log("✔ 解散队伍完成（所有号已退队）。", level="hit")
        else:
            ctx.log(f"解散队伍未全部完成（{reason}）。", level="warn")
