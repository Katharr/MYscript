# -*- coding: utf-8 -*-
r"""
自检：角色名识别（core/accounts）能不能用。

跑法：python tools/check_accounts.py
打印本机角色名册与「窗口 → 显示名」的结果，并对 OCR 结果到角色名册的白名单匹配跑一组
纯逻辑用例。改动 OCR 归一化、阈值或歧义规则后务必重跑；不需要管理员权限。

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
        print("   ↑ 有窗口没认出来 = 标签条 OCR 未通过名册唯一匹配，已安全退回「号N」。")
        print("     请确认窗口顶部标签里的角色名清晰可见，再在「选择窗口」里点「刷新」。")

    print("OCR 状态     : %s" % accounts.ocr_status())
    print()
    print("=== 2) OCR 名册匹配用例（match_ocr_result）===")
    roles = {
        "a": _role("王昭君_", "83", 1000),
        "b": _role("牛夫人_", "69", 1100),
        "c": _role("牛二奶_", "69", 1200),
    }
    cases = [
        ("完整识别 → 正确角色", [[None, "王昭君_", 0.99]], "a"),
        ("末尾下划线漏识别仍可匹配", [[None, "王昭君", 0.85]], "a"),
        ("相近角色名可区分", [[None, "牛夫人_", 0.97]], "b"),
        ("OCR 自身置信度过低 → 放弃", [[None, "牛夫人", 0.40]], None),
        ("文字过短 → 放弃", [[None, "牛", 0.99]], None),
        ("同图识出两个候选 → 放弃", [[None, "王昭君_", 0.98], [None, "牛夫人_", 0.98]], None),
    ]
    bad = 0
    for title, ocr_rows, want in cases:
        got = accounts.match_ocr_result(ocr_rows, roles)
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
