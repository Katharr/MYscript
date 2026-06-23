# -*- coding: utf-8 -*-
"""
界面主题：统一配色、字体、圆角与间距。改这里即可整体换肤（明/暗两端一并维护）。
风格：现代、简约、精致；强调蓝点缀；留白充足，信息密度适中。

颜色令牌一律是 (light, dark) 二元组——customtkinter 的 fg_color/text_color/border_color/
hover_color/progress_color 等参数原生接受这种形式，并随 ctk.set_appearance_mode("light"|"dark")
自动切换。因此绝大多数调用点写 fg_color=T.SURFACE 即同时支持明暗，无需改动。
唯一例外是「直接喂给底层 tk 控件」的颜色（如日志 Text 的 tag_config），它们只收单个字符串，
必须用 resolve(token) 取出当前外观对应端的单值（见 resolve / apply_log_tags）。

约定：每个令牌的 dark 端取值与历史线上深色值保持一致，保证落地后深色观感不变、只新增白天模式。
"""

import customtkinter as ctk

# ---- 配色：每个常量都是 (light, dark) 二元组 ----
BG = ("#f4f5f7", "#0e1014")           # 窗口底色
SIDEBAR = ("#eceef2", "#13161c")      # 侧边栏底
SURFACE = ("#ffffff", "#181c24")      # 卡片底
SURFACE_2 = ("#eef0f4", "#20252f")    # 卡片内分区 / 输入框 / 工具按钮底
BORDER = ("#dfe3e9", "#2a313d")       # 描边 / 分隔线
BTN = ("#e2e6ec", "#2f3744")          # 次级/工具按钮底：明显区别于卡片底，别和卡片糊在一起
BTN_HOVER = ("#d3d9e2", "#3a4452")    # 次级/工具按钮悬停
TEXT = ("#1b1f27", "#e7eaf0")         # 主文字
TEXT_DIM = ("#6b7280", "#8a93a3")     # 次要文字 / 说明
ACCENT = ("#2f6fed", "#4f8cff")       # 主强调（主按钮 / 选中 / 滑块 / 链接感）
ACCENT_HOVER = ("#2861d8", "#3d79ee") # 强调悬停
SUCCESS = ("#1f9d6b", "#3ecf8e")      # 成功 / 演练 / 已连接 / 命中(hit)
SUCCESS_HOVER = ("#1a8a5d", "#34b87c")  # 成功色悬停（绿色按钮 hover）
WARN = ("#c98a16", "#f2b34b")         # 警告 / 风险提示
DANGER = ("#e23b3b", "#ff5f5f")       # 危险 / 实战 / 停止按钮 / 错误
DANGER_HOVER = ("#cf3030", "#ec4b4b") # 危险悬停
PILL_OK_BG = ("#dff3e8", "#15301f")   # 药丸底：已连接 / 演练（配 SUCCESS 文字）
PILL_DANGER_BG = ("#fae0e0", "#3a1d1d")  # 药丸底：实战（配 DANGER 文字）
ON_ACCENT = ("#ffffff", "#ffffff")    # 强调色块上的文字（主按钮 / Toast）

# ---- 圆角 ----
RADIUS = 12          # 卡片、大容器、Toast
RADIUS_SM = 8        # 按钮、输入框、下拉、列表行、小标签
RADIUS_PILL = 20     # 药丸（胶囊形）

# ---- 间距（4 / 8 栅格）----
SP_1 = 4    # 紧贴元素的细微间隔、行内微调
SP_2 = 8    # 控件之间的小间隔、按钮组内距
SP_3 = 12   # 卡片之间的纵向间距、列表行 pady
SP_4 = 16   # 卡片统一内边距、区块标准内距
SP_5 = 20   # 页面内容区上下外边距
SP_6 = 24   # 页面内容区左右外边距、侧栏左内距

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


def resolve(token):
    """把 (light, dark) 二元组按当前外观模式解析成单个颜色字符串；传入已是单值则原样返回。

    用于「不吃二元组」的场合：底层 tk 控件（日志 Text 的 tag_config）、直接传给原生 tk 的颜色、
    roi_overlay 这种刻意不用 CTk 的临时浮层。切换明暗后这些地方需要重跑取值（见 apply_log_tags）。
    """
    if isinstance(token, (tuple, list)):
        mode = ctk.get_appearance_mode()   # "Light" / "Dark"
        return token[0] if mode == "Light" else token[1]
    return token


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


# 日志级别 -> 颜色令牌（语义固定；配色时用 resolve() 取当前端单值）
LEVEL_COLOR = {
    "info": TEXT,
    "hit": SUCCESS,
    "warn": WARN,
    "error": DANGER,
}


def apply_log_tags(textbox):
    """给底层 tk Text 配置各日志级别前景色（按当前外观解析单值）。

    日志走 tag_config，只收单个颜色字符串、不随 set_appearance_mode 自动变，
    故切换明暗后需对每个日志框重跑一遍本函数。失败静默忽略。
    """
    for lvl, token in LEVEL_COLOR.items():
        try:
            textbox.tag_config(lvl, foreground=resolve(token))
        except Exception:
            pass
