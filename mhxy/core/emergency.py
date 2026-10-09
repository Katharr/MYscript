# -*- coding: utf-8 -*-
"""急停的只读 Windows 观察；不发送输入，不拥有任务生命周期。"""
import ctypes

from .input import get_cursor


def in_failsafe_corner(cx, cy, corner, margin=3):
    """光标是否撞到所选主屏角落；保留既有判据与失败回退。"""
    if corner == "off" or cx is None or cy is None:
        return False
    try:
        u = ctypes.windll.user32
        W, H = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
    except Exception:
        return False
    left, right = cx <= margin, cx >= W - 1 - margin
    top, bottom = cy <= margin, cy >= H - 1 - margin
    return {
        "top_left": left and top, "top_right": right and top,
        "bottom_left": left and bottom, "bottom_right": right and bottom,
    }.get(corner, False)


def vk_down(vk):
    """虚拟键的按下状态。"""
    try:
        return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)
    except Exception:
        return False
