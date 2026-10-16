# -*- coding: utf-8 -*-
"""历史 Tk 测试的兼容导出；当前界面使用 qtui.shortcuts。"""
from ..qtui.shortcuts import (
    HOTKEY_VK, HOTKEY_NAMES, MODIFIER_VK, DEFAULT_STOP_HOTKEY,
    FAILSAFE_CORNERS, FAILSAFE_LABELS, FAILSAFE_VALUE_OF, DEFAULT_FAILSAFE,
    _in_failsafe_corner, _vk_down, _parse_stop_hotkey,
    _compose_stop_hotkey, _split_stop_hotkey,
)
