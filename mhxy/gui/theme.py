# -*- coding: utf-8 -*-
"""
界面主题：统一配色、字体、圆角与间距。改这里即可整体换肤。
风格：深色、简约、精致；强调蓝点缀；留白充足，信息密度适中。
"""

import customtkinter as ctk

# ---- 配色 ----
BG = "#0e1014"          # 窗口底色
SIDEBAR = "#13161c"     # 侧边栏
SURFACE = "#181c24"     # 卡片
SURFACE_2 = "#20252f"   # 卡片内分区 / 输入框
BORDER = "#2a313d"      # 描边
TEXT = "#e7eaf0"        # 主文字
TEXT_DIM = "#8a93a3"    # 次要文字
ACCENT = "#4f8cff"      # 主强调
ACCENT_HOVER = "#3d79ee"
SUCCESS = "#3ecf8e"
WARN = "#f2b34b"
DANGER = "#ff5f5f"
DANGER_HOVER = "#ec4b4b"

# ---- 圆角 ----
RADIUS = 12
RADIUS_SM = 8

FONT_FAMILY = "Microsoft YaHei UI"
MONO_FAMILY = "Consolas"


def build_fonts():
    """必须在创建好 CTk 根窗口之后调用（CTkFont 需要 Tk 默认根）。"""
    return {
        "title": ctk.CTkFont(FONT_FAMILY, 19, "bold"),
        "h2": ctk.CTkFont(FONT_FAMILY, 15, "bold"),
        "body": ctk.CTkFont(FONT_FAMILY, 13),
        "body_b": ctk.CTkFont(FONT_FAMILY, 13, "bold"),
        "small": ctk.CTkFont(FONT_FAMILY, 12),
        "nav": ctk.CTkFont(FONT_FAMILY, 14),
        "btn": ctk.CTkFont(FONT_FAMILY, 14, "bold"),
        "mono": ctk.CTkFont(MONO_FAMILY, 12),
    }


def tune_scroll_speed(scrollable, pixels_per_notch=60):
    """加大 CTkScrollableFrame 的滚轮步长。

    customtkinter 在 Windows 下每个滚轮格只滚 int(120/6)=20 个 unit、每 unit=1px（约 20px/格），
    长列表要拨很多下、每下都触发一批 CTk 控件重绘，体感拖沓。把每 unit 的像素数调大，
    同样的滚动距离所需步数更少、重绘批次更少，滑动更跟手。失败则静默忽略（仅影响手感）。
    """
    try:
        canvas = scrollable._parent_canvas
        inc = max(1, round(pixels_per_notch / 20))
        canvas.configure(yscrollincrement=inc, xscrollincrement=inc)
    except Exception:
        pass


# 日志级别 -> 颜色
LEVEL_COLOR = {
    "info": TEXT,
    "hit": SUCCESS,
    "warn": WARN,
    "error": DANGER,
}
