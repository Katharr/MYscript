# -*- coding: utf-8 -*-
"""
全屏框选组件（纯 tkinter，无黑窗、无子进程）。

用途：标定时让用户在「冻结的屏幕截图」上用鼠标拖一个矩形框，
返回该矩形的【屏幕绝对坐标】[left, top, w, h]，取消则返回 None。

为什么冻结截图而不是直接在游戏上画框：
- 截一张图盖满整个显示器，用户在静止画面上框选，不受游戏动画/弹窗干扰；
- 框完即得到与「将来截图识别」完全一致的像素，所见即所得。

DPI：进程已设为 per-monitor aware（customtkinter 导入时即设置），
tkinter 的几何与鼠标坐标都是物理像素，mss 截图也是物理像素，二者 1:1 对齐。
用普通 tk.Toplevel（非 CTkToplevel）以避开 customtkinter 的缩放，保证不偏。
"""

import tkinter as tk

import mss
import numpy as np
import cv2
from PIL import Image, ImageDraw, ImageTk

from . import theme as T


def _pick_monitor(sct, point):
    """返回包含 point(x,y) 的显示器字典；找不到则用主屏。"""
    if point is not None:
        px, py = point
        for m in sct.monitors[1:]:
            if (m["left"] <= px < m["left"] + m["width"]
                    and m["top"] <= py < m["top"] + m["height"]):
                return m
    return sct.monitors[1]  # 主显示器


def select_roi_on_screen(master, title="拖动鼠标框住目标，松开完成", around_point=None,
                         min_size=6, with_crop=False):
    """
    在屏幕上框选一个矩形。

    master       : 任意已存在的 tk 窗口（用于挂载 Toplevel）。
    title        : 顶部提示文字。
    around_point : (x,y) 屏幕坐标，用来决定在哪块显示器上弹出（一般传游戏窗口中心）。
    with_crop    : True 时额外返回选区裁剪图（OpenCV BGR，取自冻结截图，绝不含本助手窗口）。
    返回           : with_crop=False -> [left,top,w,h] 或 None；
                    with_crop=True  -> ([left,top,w,h], crop_bgr) 或 (None, None)。
    """
    with mss.mss() as sct:
        mon = _pick_monitor(sct, around_point)
        raw = sct.grab(mon)
    img = Image.frombytes("RGB", (raw.width, raw.height), raw.rgb)

    state = {"rect": None, "start": None, "done": False}

    top = tk.Toplevel(master)
    top.overrideredirect(True)                       # 无标题栏、无边框
    top.geometry(f"{mon['width']}x{mon['height']}+{mon['left']}+{mon['top']}")
    top.attributes("-topmost", True)
    top.configure(cursor="crosshair")

    canvas = tk.Canvas(top, width=mon["width"], height=mon["height"],
                       highlightthickness=0, bd=0, bg="black")
    canvas.pack(fill="both", expand=True)
    photo = ImageTk.PhotoImage(img)
    canvas.create_image(0, 0, anchor="nw", image=photo)
    canvas.image = photo  # 防 GC

    # 颜色按打开瞬间的外观模式解析成单值（纯 tk Canvas 不吃二元组）
    c_bg = T.resolve(T.BG)
    c_text = T.resolve(T.TEXT)
    c_accent = T.resolve(T.ACCENT)

    # 放大镜：跟随鼠标显示光标周围放大图，便于精细框选小特征（参考 Snipaste 截图放大镜）。
    # 取光标周围 MAG_VIEW 像素的源截图，最近邻放大到 MAG 边长，画十字线标出光标像素，
    # 下方显示光标的屏幕绝对坐标。窗口初始隐藏，鼠标一动即显示。
    MAG = 220                       # 放大镜窗口边长（像素）
    MAG_VIEW = 55                   # 放大镜覆盖的【源截图】边长（像素）；越大放大倍数越小
    mag_top = tk.Toplevel(master)
    mag_top.overrideredirect(True)
    mag_top.attributes("-topmost", True)
    mag_top.configure(bg=c_bg)
    mag_label = tk.Label(mag_top, bd=0, bg=c_bg)
    mag_label.pack()
    mag_coord = tk.Label(mag_top, text="", fg=c_text, bg=c_bg,
                         font=("Consolas", 10))
    mag_coord.pack()
    mag_top.withdraw()              # 初始隐藏，_update_magnifier 里再显示
    mag_photo = None
    _last_mag_pos = (-999, -999)    # 上次光标位置，用于限流（3px 内不重绘）

    def _update_magnifier(mx, my):
        nonlocal mag_photo, _last_mag_pos
        # 光标移动不足 3px 时跳过刷新，减少主线程 ImageDraw 负载
        # if abs(mx - _last_mag_pos[0]) < 3 and abs(my - _last_mag_pos[1]) < 3:
            # return
        _last_mag_pos = (mx, my)
        mag_top.deiconify()         # 首次移动即显示
        r = MAG_VIEW // 2
        sx0 = max(0, mx - r); sy0 = max(0, my - r)
        sx1 = min(mon["width"], mx + r); sy1 = min(mon["height"], my + r)
        if sx1 <= sx0 or sy1 <= sy0:
            return
        sub = img.crop((sx0, sy0, sx1, sy1))
        # 最近邻放大：保留真实像素，便于把选区边缘对齐到精确像素
        zoom = max(1, MAG // max(1, sx1 - sx0))
        big = sub.resize((int(sub.width * zoom), int(sub.height * zoom)), Image.NEAREST)
        draw = ImageDraw.Draw(big)
        ccx = int((mx - sx0) * zoom)     # 光标在放大图里的位置（裁剪被夹紧时仍准）
        ccy = int((my - sy0) * zoom)
        draw.line((0, ccy, big.width, ccy), fill=c_accent, width=1)
        draw.line((ccx, 0, ccx, big.height), fill=c_accent, width=1)
        draw.rectangle((0, 0, big.width - 1, big.height - 1), outline=c_accent, width=2)
        if big.width < MAG or big.height < MAG:
            pad = Image.new("RGB", (MAG, MAG), (0, 0, 0))
            pad.paste(big, ((MAG - big.width) // 2, (MAG - big.height) // 2))
            big = pad
        nonlocal mag_photo
        mag_photo = ImageTk.PhotoImage(big)
        mag_label.configure(image=mag_photo)
        mag_coord.configure(text=f"({mon['left'] + mx}, {mon['top'] + my})")
        # 放大镜放在光标右下方，夹紧在显示器内避免出屏
        off = 24
        wx = min(mon["left"] + mx + off, mon["left"] + mon["width"] - MAG - 4)
        wy = min(mon["top"] + my + off, mon["top"] + mon["height"] - MAG - 20)
        mag_top.geometry(f"{MAG}x{MAG + 18}+{wx}+{wy}")

    # 顶部提示条
    canvas.create_rectangle(0, 0, mon["width"], 44, fill=c_bg, outline="", stipple="gray50")
    canvas.create_text(mon["width"] // 2, 22,
                       text=f"{title}      （Esc 取消）",
                       fill=c_text, font=("Microsoft YaHei UI", 14, "bold"))

    band = canvas.create_rectangle(0, 0, 0, 0, outline=c_accent, width=2)
    dimv = []  # 选区外的遮罩（四块），用于高亮选区
    sizetip = canvas.create_text(0, 0, text="", fill=c_accent,
                                 font=("Consolas", 11), anchor="nw")

    def _clear_dim():
        for d in dimv:
            canvas.delete(d)
        dimv.clear()

    def _draw_dim(x0, y0, x1, y1):
        _clear_dim()
        W, H = mon["width"], mon["height"]
        for box in ((0, 0, W, y0), (0, y1, W, H), (0, y0, x0, y1), (x1, y0, W, y1)):
            dimv.append(canvas.create_rectangle(*box, fill="#000000", stipple="gray50", outline=""))

    def on_down(e):
        state["start"] = (e.x, e.y)

    def on_drag(e):
        if not state["start"]:
            return
        x0, y0 = state["start"]
        x1, y1 = e.x, e.y
        lx, ly, rx, ry = min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)
        canvas.coords(band, lx, ly, rx, ry)
        canvas.tag_raise(band)
        _draw_dim(lx, ly, rx, ry)
        canvas.tag_raise(band)
        canvas.coords(sizetip, rx + 6, ly)
        canvas.itemconfigure(sizetip, text=f"{rx - lx} × {ry - ly}")
        canvas.tag_raise(sizetip)
        _update_magnifier(e.x, e.y)

    def on_up(e):
        if not state["start"]:
            return
        x0, y0 = state["start"]
        x1, y1 = e.x, e.y
        lx, ly, rx, ry = min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)
        w, h = rx - lx, ry - ly
        if w >= min_size and h >= min_size:
            state["rect"] = [mon["left"] + lx, mon["top"] + ly, w, h]
        _finish()

    def on_cancel(_e=None):
        state["rect"] = None
        _finish()

    def _finish():
        if state["done"]:
            return
        state["done"] = True
        try:
            mag_top.destroy()
        except Exception:
            pass
        try:
            top.grab_release()
        except Exception:
            pass
        top.destroy()

    def _on_motion(e):
        _update_magnifier(e.x, e.y)

    canvas.bind("<ButtonPress-1>", on_down)
    canvas.bind("<B1-Motion>", on_drag)
    canvas.bind("<Motion>", _on_motion)
    canvas.bind("<ButtonRelease-1>", on_up)
    top.bind("<Escape>", on_cancel)

    top.update_idletasks()
    top.lift()
    top.focus_force()
    try:
        top.grab_set()       # 模态：独占输入
    except Exception:
        pass
    master.wait_window(top)

    if not with_crop:
        return state["rect"]
    rect = state["rect"]
    if rect is None:
        return None, None
    # 从冻结的整屏截图里裁出选区（坐标要减去显示器原点），转成 OpenCV BGR
    lx = rect[0] - mon["left"]
    ly = rect[1] - mon["top"]
    crop_rgb = img.crop((lx, ly, lx + rect[2], ly + rect[3]))
    crop_bgr = cv2.cvtColor(np.array(crop_rgb), cv2.COLOR_RGB2BGR)
    return rect, crop_bgr
