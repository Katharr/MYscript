# -*- coding: utf-8 -*-
"""
窗口排列工具（从左到右水平布局）。

用于一键五开或手动排列多个游戏窗口，确保不重叠。
配合 config.targets.base_size 统一窗口尺寸，整齐美观。
"""

import tkinter as tk


def arrange_windows(windows, base_size, screen_gap=10, taskbar_height=40, margin=10):
    """
    从左到右水平排列窗口。

    参数：
    - windows: GameWindow列表（按顺序排列）
    - base_size: [width, height] 基准尺寸
    - screen_gap: 窗口间距（像素），默认10
    - taskbar_height: 任务栏高度（像素），默认40
    - margin: 屏幕边距（像素），默认10

    返回：
    - 成功排列的窗口数量
    """
    if not windows or not base_size or len(base_size) < 2:
        return 0

    window_width, window_height = int(base_size[0]), int(base_size[1])
    n_windows = len(windows)

    # 获取屏幕分辨率
    root = tk.Tk()
    screen_width = root.winfo_screenwidth()
    screen_height = root.winfo_screenheight()
    root.destroy()

    # 计算可用宽度（左右边距）
    available_width = screen_width - 2 * margin

    # 验证窗口是否能放下
    total_width = n_windows * window_width + (n_windows - 1) * screen_gap
    if total_width > available_width:
        # 宽度不足，无法排列
        return 0

    # 计算起始位置（居中排列）
    start_x = (available_width - total_width) // 2 + margin
    y = taskbar_height + margin  # 顶部留出任务栏高度

    # 逐个移动窗口
    success_count = 0
    for i, win in enumerate(windows):
        x = start_x + i * (window_width + screen_gap)
        try:
            # 移动并调整窗口大小
            if win.resize_to(window_width, window_height, move_to=(x, y)):
                success_count += 1
        except Exception:
            # 移动失败，跳过该窗口
            continue

    return success_count


def get_arrangement_info(base_size, n_windows=5, screen_gap=10):
    """
    获取排列信息（用于预检查）。

    参数：
    - base_size: [width, height] 基准尺寸
    - n_windows: 窗口数量，默认5
    - screen_gap: 窗口间距（像素），默认10

    返回：
    - (can_arrange: bool, message: str)
    """
    if not base_size or len(base_size) < 2:
        return False, "未设置基准尺寸"

    window_width, window_height = int(base_size[0]), int(base_size[1])

    # 获取屏幕分辨率
    root = tk.Tk()
    screen_width = root.winfo_screenwidth()
    screen_height = root.winfo_screenheight()
    root.destroy()

    # 计算所需宽度
    margin = 10
    available_width = screen_width - 2 * margin
    total_width = n_windows * window_width + (n_windows - 1) * screen_gap

    if total_width > available_width:
        return False, f"屏幕宽度不足以排列 {n_windows} 个窗口（需要 {total_width}px，可用 {available_width}px）"

    return True, f"可以排列 {n_windows} 个窗口（总宽 {total_width}px）"