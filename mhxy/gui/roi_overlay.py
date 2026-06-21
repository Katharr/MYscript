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
from PIL import Image, ImageTk


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

    # 顶部提示条
    canvas.create_rectangle(0, 0, mon["width"], 44, fill="#0e1014", outline="", stipple="gray50")
    canvas.create_text(mon["width"] // 2, 22,
                       text=f"{title}      （Esc 取消）",
                       fill="#e7eaf0", font=("Microsoft YaHei UI", 14, "bold"))

    band = canvas.create_rectangle(0, 0, 0, 0, outline="#4f8cff", width=2)
    dimv = []  # 选区外的遮罩（四块），用于高亮选区
    sizetip = canvas.create_text(0, 0, text="", fill="#4f8cff",
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
            top.grab_release()
        except Exception:
            pass
        top.destroy()

    canvas.bind("<ButtonPress-1>", on_down)
    canvas.bind("<B1-Motion>", on_drag)
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
