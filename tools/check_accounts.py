# -*- coding: utf-8 -*-
r"""
自检：角色名识别（core/accounts）能不能用。

跑法：python tools/check_accounts.py
打印本机角色名册与「窗口 → 显示名」的结果，并对 assign_roles 的配对规则跑一组纯逻辑用例
（这个启发式是整个特性唯一的猜测点，改动它之后务必重跑）。不需要管理员权限。

只读游戏客户端的文件，不写、不动游戏任何东西。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mhxy.core import accounts, window as win_mod   # noqa: E402


def _role(name, level, login_ts):
    return {"name": name, "level": level, "login_ts": login_ts}


def main():
    win_mod.set_dpi_aware()

    print("=== 1) 真实环境 ===")
    wins = win_mod.locate_all("梦幻西游", (0, 0))
    print("游戏窗口数 : %d" % len(wins))
    print("LocalData  : %s" % (accounts.data_dir(wins) or "（没找到——游戏没开或安装目录特殊）"))
    names = accounts.roster(wins)
    if not names:
        print("角色名册   : （空）—— 游戏没登录过，或 LocalData 里没有 XyqPocket_LoginInfo_*")
    for rid, v in names.items():
        print("   role_id=%-10s %-10s Lv.%-4s %s" % (rid, v.get("name"), v.get("level"), v.get("server")))
    labels = accounts.labels_for(wins)
    print("窗口显示名 : %s" % labels)
    if wins and any(x.startswith("号") for x in labels):
        print("   ↑ 有窗口没认出来 = 退回「号N」了（同一秒登录 / 登录时间戳对不上）。")
        print("     这时看游戏窗口左上角标签页人工分辨即可。")

    print()
    print("=== 2) 配对规则用例（assign_roles）===")
    two = {"a": _role("甲", "1", 1000), "b": _role("乙", "2", 1100)}
    one = {"a": _role("甲", "1", 1000)}
    tie = {"a": _role("甲", "1", 1000), "b": _role("乙", "2", 1000)}   # 同一秒登录
    cases = [
        # 窗口进程启动 + 约 15s 登录
        ("正常：两号两窗，各差 15s", two, [1, 2], {1: [985.0], 2: [1085.0]}, {0: "a", 1: "b"}),
        ("窗口顺序与启动顺序相反", two, [2, 1], {2: [1085.0], 1: [985.0]}, {0: "b", 1: "a"}),
        # 安全边界：判不准就放弃该窗口（宁可显示「号N」也不显示错名字）
        ("并列最优（同一秒登录）→ 放弃", tie, [1], {1: [985.0]}, {}),
        ("并列最优 + 两个窗口 → 都放弃", tie, [1, 2], {1: [985.0], 2: [985.0]}, {}),
        ("登录早于进程启动（不可能）→ 放弃", two, [1], {1: [1200.0]}, {}),
        ("差 500s（超出登录窗口）→ 放弃", two, [1], {1: [500.0]}, {}),
        ("没有游戏子进程 → 放弃", two, [1], {1: []}, {}),
        # 角色数少于窗口数：能认的认，认不了的空着
        ("两个窗口只有一个角色 → 只认一个", one, [1, 2], {1: [985.0], 2: [990.0]}, {0: "a"}),
    ]
    bad = 0
    for title, roles, pids, starts, want in cases:
        got = accounts.assign_roles(pids, starts, roles)
        ok = (got == want)
        bad += (not ok)
        print("  [%s] %s" % ("OK" if ok else "!!", title))
        if not ok:
            print("       期望 %s，实得 %s" % (want, got))
    print()
    print("用例 %d/%d 通过。" % (len(cases) - bad, len(cases)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
