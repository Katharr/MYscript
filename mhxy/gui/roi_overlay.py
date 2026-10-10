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

from ..core import vision
from PIL import Image, ImageTk

from . import theme as T


def select_roi_on_screen(master, title="拖动鼠标框住目标，松开完成", around_point=None,
                         min_size=6, with_crop=False, action_choices=None):
    """
    在屏幕上框选一个矩形。

    master       : 任意已存在的 tk 窗口（用于挂载 Toplevel）。
    title        : 顶部提示文字。
    around_point : (x,y) 屏幕坐标，用来决定在哪块显示器上弹出（一般传游戏窗口中心）。
    with_crop    : True 时额外返回选区裁剪图（OpenCV BGR，取自冻结截图，绝不含本助手窗口）。
    action_choices: 可选 [(action_value, 中文标签), ...]；松开后点击动作才完成，支持重新框选。
    返回           : 不传 action_choices 时保持原接口；传入时在返回值末尾增加 action，
                    with_crop=False -> (rect, action)，with_crop=True -> (rect, crop, action)，
                    取消时各项均为 None。裁图始终来自原冻结截图。
    """
    mon, size, pixels = vision.capture_monitor_rgb(around_point)
    img = Image.frombytes("RGB", size, pixels)
    choices = list(action_choices) if action_choices is not None else None

    state = {"rect": None, "start": None, "done": False, "action": None}
    toolbar = None

    top = tk.Toplevel(master)

    def _finish():
        if state["done"]:
            return
        state["done"] = True
        try:
            top.grab_release()
        except Exception:
            pass
        top.destroy()

    try:
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

        # 顶部提示条
        canvas.create_rectangle(0, 0, mon["width"], 44, fill=c_bg, outline="", stipple="gray50")
        canvas.create_text(mon["width"] // 2, 22,
                           text=f"{title}      （Esc 取消）",
                           fill=c_text, font=("Microsoft YaHei UI", 14, "bold"))

        band = canvas.create_rectangle(0, 0, 0, 0, outline=c_accent, width=2)
        dimv = []  # 选区外的遮罩（四块），用于高亮选区
        sizetip = canvas.create_text(0, 0, text="", fill=c_accent,
                                     font=("Consolas", 11), anchor="nw")
    except Exception:
        _finish()
        raise

    def _clear_dim():
        for d in dimv:
            canvas.delete(d)
        dimv.clear()

    def _draw_dim(x0, y0, x1, y1):
        _clear_dim()
        W, H = mon["width"], mon["height"]
        for box in ((0, 0, W, y0), (0, y1, W, H), (0, y0, x0, y1), (x1, y0, W, y1)):
            dimv.append(canvas.create_rectangle(*box, fill="#000000", stipple="gray50", outline=""))

    def _point(e):
        return (max(0, min(mon["width"], e.x)),
                max(0, min(mon["height"], e.y)))

    def _hide_toolbar():
        nonlocal toolbar
        if toolbar is not None:
            toolbar.destroy()
            toolbar = None

    def on_retry():
        _hide_toolbar()
        state.update(rect=None, start=None, action=None)
        _clear_dim()
        canvas.coords(band, 0, 0, 0, 0)
        canvas.itemconfigure(sizetip, text="")

    def on_action(action):
        state["action"] = action
        _finish()

    def _show_toolbar(lx, ly, rx, ry):
        nonlocal toolbar
        toolbar = tk.Frame(top, bg=c_bg, bd=1, relief="solid", cursor="arrow")
        entries = [(label, lambda a=value: on_action(a)) for value, label in choices]
        entries += [("重新框选", on_retry), ("取消", on_cancel)]
        # 按屏幕宽度分行，避免靠右的选区或窄屏裁掉操作按钮。
        row, col, row_width = 0, 0, 0
        for label, command in entries:
            button = tk.Button(toolbar, text=label, command=command, bg=c_bg, fg=c_text,
                               activebackground=c_accent, cursor="arrow",
                               font=("Microsoft YaHei UI", 10), padx=8, pady=4)
            button.update_idletasks()
            width = button.winfo_reqwidth() + 4
            if col and row_width + width > mon["width"] - 12:
                row, col, row_width = row + 1, 0, 0
            button.grid(row=row, column=col, padx=2, pady=2)
            col += 1
            row_width += width
        toolbar.update_idletasks()
        width, height = toolbar.winfo_reqwidth(), toolbar.winfo_reqheight()
        x = max(0, min(lx, mon["width"] - width))
        y = ry + 8
        if y + height > mon["height"]:
            y = ly - height - 8
        y = max(0, min(y, mon["height"] - height))
        toolbar.place(x=x, y=y)
        toolbar.lift()

    def on_down(e):
        if state["rect"] is not None:
            return
        state["start"] = _point(e)

    def on_drag(e):
        if state["start"] is None:
            return
        x0, y0 = state["start"]
        x1, y1 = _point(e)
        lx, ly, rx, ry = min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)
        canvas.coords(band, lx, ly, rx, ry)
        canvas.tag_raise(band)
        _draw_dim(lx, ly, rx, ry)
        canvas.tag_raise(band)
        canvas.coords(sizetip, rx + 6, ly)
        canvas.itemconfigure(sizetip, text=f"{rx - lx} × {ry - ly}")
        canvas.tag_raise(sizetip)

    def on_up(e):
        if state["start"] is None:
            return
        on_drag(e)
        x0, y0 = state["start"]
        x1, y1 = _point(e)
        state["start"] = None
        lx, ly, rx, ry = min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)
        w, h = rx - lx, ry - ly
        if w >= min_size and h >= min_size:
            state["rect"] = [mon["left"] + lx, mon["top"] + ly, w, h]
            if choices is not None:
                try:
                    _show_toolbar(lx, ly, rx, ry)
                except Exception:
                    on_cancel()
                    raise
                return
        elif choices is not None:
            on_retry()
            return
        _finish()

    def on_cancel(_e=None):
        state["rect"] = None
        state["action"] = None
        _finish()

    try:
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
    finally:
        _finish()

    if not with_crop:
        return (state["rect"], state["action"]) if choices is not None else state["rect"]
    rect = state["rect"]
    if rect is None:
        return (None, None, None) if choices is not None else (None, None)
    # 从冻结的整屏截图里裁出选区（坐标要减去显示器原点），转成 OpenCV BGR
    lx = rect[0] - mon["left"]
    ly = rect[1] - mon["top"]
    crop_rgb = img.crop((lx, ly, lx + rect[2], ly + rect[3]))
    crop_bgr = vision.rgb_to_bgr(crop_rgb)
    return (rect, crop_bgr, state["action"]) if choices is not None else (rect, crop_bgr)
