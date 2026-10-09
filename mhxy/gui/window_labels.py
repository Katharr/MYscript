# -*- coding: utf-8 -*-
"""页面后台枚举使用的窗口显示名查询。"""
from ..core import window as win_mod
from ..core import accounts

def resolve_window_labels(title, offset, targets):
    """后台线程用：选出目标窗口并给出「角色名（等级）」显示名列表（认不出退回「号N」）。

    与 win_mod.resolve_targets 严格同序，故可以直接和「号N」序号互换。
    这里只读 core/accounts 的已确认身份缓存，不唤窗、不在后台执行 OCR。
    ⚠ 会枚举进程，别在 UI 线程直接调——各页都放在后台枚举的 work() 里。"""
    try:
        wins = win_mod.resolve_targets(title, offset, targets)
    except Exception:
        return []
    try:
        return accounts.cached_labels_for(wins)
    except Exception:
        return [accounts.fallback_label(i) for i in range(len(wins))]
