# -*- coding: utf-8 -*-
"""
标定对话框（全部在 GUI 内完成，无黑窗、无子进程）。

做两件事：
1. 框选四个区域（物品列表 / 刷新 / 购买 / 确认）—— 存为相对游戏窗口的 [x,y,w,h]。
2. 添加要抢的装备 —— 框选装备图标+名字，裁下来存成模板图，加入监控清单。

框选时会临时把本助手的所有窗口藏起来，确保截图里只有游戏画面。
"""

import time

import customtkinter as ctk

from . import theme as T
from .roi_overlay import select_roi_on_screen
from ..core import config as cfg_mod
from ..core import window as win_mod
from ..core import vision


REGION_ITEMS = [
    ("listing", "货架/列表区域", "进货架后要识别的那片区域，框大一点把整列摊位都包进去"),
    ("category_button", "商品类别按钮", "左侧侧边栏里的类别，如「奇珍异宝」——刷新第①步点它"),
    ("product_entry", "商品条目", "右侧信息框里要进的那个商品——刷新第②步点它进货架"),
    ("buy_button", "购买按钮", "选中摊位后出现的「购买」按钮"),
    ("confirm_button", "确认购买按钮", "二次确认弹窗的按钮，没有可不标"),
]

TASK = "sniper"


class CalibrateDialog(ctk.CTkToplevel):
    def __init__(self, app, on_done=None):
        super().__init__(app)
        self.app = app
        self.fonts = app.fonts
        self.on_done = on_done

        self.title("标定 / 加装备")
        self.geometry("680x640")
        self.minsize(560, 420)
        self.configure(fg_color=T.BG)
        self.transient(app)

        self.cfg = cfg_mod.load_config()
        self.tc = cfg_mod.task_config(self.cfg, TASK)

        self._build()
        self._refresh()
        self.after(120, self._center_on_app)

    # ------------------------------------------------------------------
    def _center_on_app(self):
        try:
            self.lift()
            self.focus_force()
        except Exception:
            pass

    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)  # 滚动区占满，底部按钮固定

        # 整个内容区放进一个滚动容器：项目再多 / DPI 再高都不会把下面顶出界面。
        body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        body.grid(row=0, column=0, sticky="nsew", padx=4, pady=(4, 0))
        body.grid_columnconfigure(0, weight=1)
        T.tune_scroll_speed(body)

        # 顶部提示
        top = ctk.CTkFrame(body, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 8))
        ctk.CTkLabel(top, text="标定向导", font=self.fonts["title"], text_color=T.TEXT).pack(anchor="w")
        self.hint = ctk.CTkLabel(
            top, text="先打开市场并进到某个货架界面。刷新靠①类别+②商品两步重进货架，按提示逐项框选。",
            justify="left",
            font=self.fonts["small"], text_color=T.TEXT_DIM)
        self.hint.pack(anchor="w", pady=(4, 0))

        # 区域标定卡片
        rcard = ctk.CTkFrame(body, fg_color=T.SURFACE, corner_radius=T.RADIUS,
                             border_width=1, border_color=T.BORDER)
        rcard.grid(row=1, column=0, sticky="ew", padx=16, pady=(8, 8))
        rcard.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(rcard, text="① 区域与按钮", font=self.fonts["h2"], text_color=T.TEXT).grid(
            row=0, column=0, columnspan=3, sticky="w", padx=16, pady=(14, 6))

        self.region_rows = {}
        for i, (key, name, desc) in enumerate(REGION_ITEMS):
            row = ctk.CTkFrame(rcard, fg_color=T.SURFACE_2, corner_radius=T.RADIUS_SM)
            row.grid(row=1 + i, column=0, sticky="ew", padx=12, pady=4)
            row.grid_columnconfigure(0, weight=1)
            txt = ctk.CTkFrame(row, fg_color="transparent")
            txt.grid(row=0, column=0, sticky="w", padx=12, pady=8)
            ctk.CTkLabel(txt, text=name, font=self.fonts["body_b"], text_color=T.TEXT).pack(anchor="w")
            ctk.CTkLabel(txt, text=desc, font=self.fonts["small"], text_color=T.TEXT_DIM).pack(anchor="w")
            status = ctk.CTkLabel(row, text="", font=self.fonts["small"], width=90)
            status.grid(row=0, column=1, padx=6)
            ctk.CTkButton(row, text="框选", font=self.fonts["body"], width=72, height=30,
                          corner_radius=T.RADIUS_SM, fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER,
                          command=lambda k=key, n=name: self._calibrate_region(k, n)).grid(
                              row=0, column=2, padx=(6, 12))
            self.region_rows[key] = status

        ctk.CTkFrame(rcard, fg_color="transparent", height=8).grid(row=99, column=0)

        # 装备卡片
        icard = ctk.CTkFrame(body, fg_color=T.SURFACE, corner_radius=T.RADIUS,
                             border_width=1, border_color=T.BORDER)
        icard.grid(row=2, column=0, sticky="ew", padx=16, pady=(8, 8))
        icard.grid_columnconfigure(0, weight=1)
        head = ctk.CTkFrame(icard, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 6))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text="② 要抢的装备", font=self.fonts["h2"], text_color=T.TEXT).grid(
            row=0, column=0, sticky="w")
        ctk.CTkButton(head, text="＋ 框选添加", font=self.fonts["body"], width=110, height=32,
                      corner_radius=T.RADIUS_SM, fg_color=T.SUCCESS, hover_color="#34b87c",
                      text_color="#0e1014", command=self._add_item).grid(row=0, column=1, sticky="e")

        ctk.CTkLabel(icard, text="提示：连「图标 + 名字」一起框，别框价格（价格会变，框了反而认不出）。",
                     font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left").grid(
                         row=1, column=0, sticky="w", padx=16, pady=(0, 6))

        # 装备列表：用普通框（外层 body 已可滚动，避免嵌套滚动相互抢事件）。
        self.item_list = ctk.CTkFrame(icard, fg_color="transparent")
        self.item_list.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 12))
        self.item_list.grid_columnconfigure(0, weight=1)

        # 底部（固定在窗口底，不随内容滚动，「完成」永远可见）
        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.grid(row=1, column=0, sticky="ew", padx=20, pady=(4, 16))
        bottom.grid_columnconfigure(0, weight=1)
        self.status_lbl = ctk.CTkLabel(bottom, text="", font=self.fonts["small"], text_color=T.TEXT_DIM)
        self.status_lbl.grid(row=0, column=0, sticky="w")
        ctk.CTkButton(bottom, text="完成", font=self.fonts["btn"], width=110, height=38,
                      corner_radius=T.RADIUS_SM, fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER,
                      command=self._close).grid(row=0, column=1, sticky="e")

    # ------------------------------------------------------------------
    # 框选：藏起自己 -> 激活游戏 -> 截图框选 -> 复原。返回 (rel_roi, abs_roi, win)
    # ------------------------------------------------------------------
    def _set_alpha(self, a):
        """同时设置本对话框与主窗口的透明度（截图前隐形、截图后复原）。"""
        for w in (self.app, self):
            try:
                w.attributes("-alpha", a)
            except Exception:
                pass

    def _grab_roi(self, prompt, with_crop=False):
        """返回 (rel_roi, crop_bgr)；取消或没游戏时返回 (None, None)。"""
        title = self.cfg.get("window_title", "梦幻西游")
        win = win_mod.GameWindow(title, self.cfg.get("window_offset", [0, 0]))
        if not win.locate():
            self._toast(f"没找到游戏窗口（标题含「{title}」），请先打开游戏。", T.WARN)
            return None, None
        win.activate()

        wr = win.rect()
        center = (wr[0] + wr[2] // 2, wr[1] + wr[3] // 2)

        # 把本助手窗口「透明化」而不是 withdraw：alpha=0 的窗口不会被截图捕获，
        # 但省掉了 withdraw/deiconify 带来的整窗重新映射 + 所有圆角控件重绘（那正是“像重新渲染”的卡顿来源）。
        self._set_alpha(0.0)
        self.update_idletasks()
        time.sleep(0.12)

        try:
            result = select_roi_on_screen(self.app, prompt, around_point=center, with_crop=with_crop)
        finally:
            self._set_alpha(1.0)
            self.lift()
            self.focus_force()

        if with_crop:
            roi_abs, crop = result
        else:
            roi_abs, crop = result, None
        if roi_abs is None:
            return None, None
        # 重新定位一次窗口（激活后位置可能微调），用同一个 rect() 口径换算
        win.locate()
        wr = win.rect()
        rel = [roi_abs[0] - wr[0], roi_abs[1] - wr[1], roi_abs[2], roi_abs[3]]
        return rel, crop

    # ---- 区域标定 ----
    def _calibrate_region(self, key, name):
        rel, _crop = self._grab_roi(f"框选「{name}」")
        if rel is None:
            return
        self.tc.setdefault("regions", {})[key] = rel
        self._save()
        self._refresh()
        self._toast(f"已记录 {name}：{rel}", T.SUCCESS)

    # ---- 添加装备 ----
    def _add_item(self):
        rel, crop = self._grab_roi("框选要抢的装备（图标+名字）", with_crop=True)
        if rel is None:
            return
        if crop is None or crop.size == 0:
            self._toast("截图失败，请重试。", T.DANGER)
            return

        name = self._ask_name()
        if not name:
            return
        rel_path = f"templates/{name}.png"
        if not vision.save_image(rel_path, crop):
            self._toast("保存模板图失败。", T.DANGER)
            return
        self.tc.setdefault("watchlist", []).append(
            {"name": name, "template": rel_path, "max_price": None})
        self._save()
        self._refresh()
        self._toast(f"已添加装备：{name}", T.SUCCESS)

    def _ask_name(self):
        dlg = ctk.CTkInputDialog(text="给这件装备起个名字（中英文均可）：", title="命名")
        raw = dlg.get_input()
        if raw is None:
            return None
        name = raw.strip().replace("/", "_").replace("\\", "_").replace(" ", "_")
        if not name:
            name = f"item_{int(time.time())}"
        # 避免重名覆盖
        existing = {it["name"] for it in self.tc.get("watchlist", [])}
        base, n = name, 2
        while name in existing:
            name = f"{base}_{n}"
            n += 1
        return name

    def _delete_item(self, idx):
        wl = self.tc.get("watchlist", [])
        if 0 <= idx < len(wl):
            removed = wl.pop(idx)
            self._save()
            self._refresh()
            self._toast(f"已删除：{removed.get('name', '?')}", T.TEXT_DIM)

    # ------------------------------------------------------------------
    def _refresh(self):
        # 区域状态
        regions = self.tc.get("regions", {})
        for key, status in self.region_rows.items():
            if regions.get(key):
                status.configure(text="● 已标定", text_color=T.SUCCESS)
            else:
                status.configure(text="○ 未标定", text_color=T.TEXT_DIM)

        # 装备清单
        for w in self.item_list.winfo_children():
            w.destroy()
        wl = self.tc.get("watchlist", [])
        if not wl:
            ctk.CTkLabel(self.item_list, text="还没有装备，点右上「＋ 框选添加」。",
                         font=self.fonts["body"], text_color=T.TEXT_DIM).grid(
                             row=0, column=0, sticky="w", padx=12, pady=16)
        else:
            for i, it in enumerate(wl):
                row = ctk.CTkFrame(self.item_list, fg_color=T.SURFACE_2, corner_radius=T.RADIUS_SM)
                row.grid(row=i, column=0, sticky="ew", pady=3, padx=4)
                row.grid_columnconfigure(0, weight=1)
                ctk.CTkLabel(row, text=f"🗡  {it.get('name', '?')}", font=self.fonts["body"],
                             text_color=T.TEXT).grid(row=0, column=0, sticky="w", padx=12, pady=8)
                ctk.CTkButton(row, text="删除", font=self.fonts["small"], width=52, height=28,
                              corner_radius=T.RADIUS_SM, fg_color="transparent", hover_color=T.DANGER,
                              border_width=1, border_color=T.BORDER,
                              command=lambda idx=i: self._delete_item(idx)).grid(
                                  row=0, column=1, padx=10)

    def _save(self):
        cfg_mod.set_task_config(self.cfg, TASK, self.tc)
        cfg_mod.save_config(self.cfg)

    def _toast(self, msg, color=T.TEXT_DIM):
        self.status_lbl.configure(text=msg, text_color=color)

    def _close(self):
        if callable(self.on_done):
            try:
                self.on_done()
            except Exception:
                pass
        self.destroy()
