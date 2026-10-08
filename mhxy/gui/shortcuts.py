# -*- coding: utf-8 -*-
"""急停配置与轮询；保留原键码、角落与触发判据。"""
from ..core.emergency import in_failsafe_corner as _in_failsafe_corner, vk_down as _vk_down

HOTKEY_VK = {
    "F1": 0x70, "F2": 0x71, "F3": 0x72, "F4": 0x73, "F5": 0x74, "F6": 0x75,
    "F7": 0x76, "F8": 0x77, "F9": 0x78, "F10": 0x79, "F11": 0x7A, "F12": 0x7B,
    "Pause": 0x13, "ScrollLock": 0x91, "Home": 0x24, "End": 0x23,
    "Insert": 0x2D, "Delete": 0x2E, "`(~)": 0xC0,
}
HOTKEY_NAMES = list(HOTKEY_VK.keys())

# 全局【急停】热键 = 若干修饰键 + 一个主键（上面的功能键），存成 "ctrl+alt+F12" 这样的字符串。
# 默认 Ctrl+Alt+F12：组合键比单键更不易和游戏内操作误撞，按一下立刻停止一切正在跑的任务。
MODIFIER_VK = {"ctrl": 0x11, "alt": 0x12, "shift": 0x10}
DEFAULT_STOP_HOTKEY = "ctrl+alt+F12"


# 失控急停：任务运行时把鼠标甩到屏幕某个角(撞到角落)即停。内部值 <-> 中文显示，off=关闭。
FAILSAFE_CORNERS = {
    "top_right": "右上角", "top_left": "左上角",
    "bottom_right": "右下角", "bottom_left": "左下角", "off": "关闭",
}
FAILSAFE_LABELS = list(FAILSAFE_CORNERS.values())
FAILSAFE_VALUE_OF = {v: k for k, v in FAILSAFE_CORNERS.items()}
DEFAULT_FAILSAFE = "top_right"


def _parse_stop_hotkey(s):
    """'ctrl+alt+F12' -> (修饰键VK列表, 主键VK)。主键缺失/不认得返回 ([], None)。修饰键名不区分大小写。"""
    mods, key_vk = [], None
    for part in str(s or "").split("+"):
        p = part.strip()
        if not p:
            continue
        low = p.lower()
        if low in MODIFIER_VK:
            mods.append(MODIFIER_VK[low])
            continue
        for name, vk in HOTKEY_VK.items():
            if name.lower() == low:
                key_vk = vk
                break
    return mods, key_vk


def _compose_stop_hotkey(ctrl, alt, shift, key):
    """(ctrl,alt,shift 勾选 + 主键名) -> 'ctrl+alt+F12' 字符串（修饰键在前、主键在后）。"""
    parts = []
    if ctrl:
        parts.append("ctrl")
    if alt:
        parts.append("alt")
    if shift:
        parts.append("shift")
    parts.append(key)
    return "+".join(parts)


def _split_stop_hotkey(s):
    """'ctrl+alt+F12' -> (ctrl:bool, alt:bool, shift:bool, 主键名)，供设置 UI 回填；主键认不得回退 F12。"""
    mods, key_vk = _parse_stop_hotkey(s)
    key = "F12"
    for name, vk in HOTKEY_VK.items():
        if vk == key_vk:
            key = name
            break
    return (MODIFIER_VK["ctrl"] in mods, MODIFIER_VK["alt"] in mods,
            MODIFIER_VK["shift"] in mods, key)
