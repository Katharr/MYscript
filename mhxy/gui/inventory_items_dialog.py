# -*- coding: utf-8 -*-
"""
整理背包·物品清单管理弹窗。

列出共享命名空间 tasks.organize_bag.items 的每件物品，逐行可：
- 看缩略图 + 名字；
- 用下拉改它的动作（使用/丢弃/出售，内部值 use/discard/sell），改动即写回 item.action 并存盘；
- 删除该物品。
顶部「＋ 框选添加物品」：在当前屏幕框选物品（图标+名字）裁图存成 templates/ob_<安全名>.png，
取名后默认动作 use 加入清单。

物品图固定存 templates/ob_<name>.png；模板键/动作内部值由契约写死，本文件不改 core/config。
窗口风格/主题色照搬 calibrate_dialog；可能超一行的说明文字一律 T.bind_wraplength。
"""

import time

import customtkinter as ctk

from . import theme as T
from ..core import config as cfg_mod
from ..core import vision

# 动作内部值 <-> 界面显示（契约固定：use/discard/sell ↔ 使用/丢弃/出售）
ACTION_LABELS = {"use": "使用", "discard": "丢弃", "sell": "出售"}
ACTION_VALUES = {v: k for k, v in ACTION_LABELS.items()}
ACTION_ORDER = ["use", "discard", "sell"]


class InventoryItemsDialog(ctk.CTkToplevel):
    def __init__(self, app, on_done=None):
        super().__init__(app)
        self.app = app
        self.fonts = app.fonts
        self.on_done = on_done

        self.cfg = cfg_mod.load_config()
        self.tc = cfg_mod.task_config(self.cfg, "organize_bag")

        self._thumbs = []   # 防缩略图被 GC（_refresh 开头清空再重建）

        self.title("整理背包 · 物品清单")
        self.geometry("620x600")
        self.minsize(520, 420)
        self.configure(fg_color=T.BG)
        self.transient(app)
        self.protocol("WM_DELETE_WINDOW", self._close)

        self._build()
        self._refresh()
        self.after(120, self._center_on_app)

    def _center_on_app(self):
        try:
            self.lift()
            self.focus_force()
        except Exception:
            pass

    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # 顶部：标题 + 说明 + 添加按钮
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 6))
        top.grid_columnconfigure(0, weight=1)
        ttxt = ctk.CTkFrame(top, fg_color="transparent")
        ttxt.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(ttxt, text="物品清单", font=self.fonts["title"], text_color=T.TEXT).pack(anchor="w")
        sub = ctk.CTkLabel(ttxt, text="整理时会翻包裹找到这些物品，按各自动作逐个使用/丢弃/出售。"
                                      "连「图标 + 名字」一起框，别框会变的数字。",
                           font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left")
        sub.pack(fill="x", pady=(4, 0))
        T.bind_wraplength(sub)
        ctk.CTkButton(top, text="＋ 框选添加物品", font=self.fonts["body"], width=140, height=34,
                      corner_radius=T.RADIUS_SM, fg_color=T.SUCCESS, hover_color=T.SUCCESS_HOVER,
                      text_color=T.BG, command=self._add_item).grid(row=0, column=1, sticky="e", padx=(12, 0))

        # 物品列表（可滚动）
        self.list_frame = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.list_frame.grid(row=1, column=0, sticky="nsew", padx=12, pady=(4, 0))
        self.list_frame.grid_columnconfigure(0, weight=1)
        T.tune_scroll_speed(self.list_frame)

        # 底部：状态 + 完成
        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.grid(row=2, column=0, sticky="ew", padx=20, pady=(8, 16))
        bottom.grid_columnconfigure(0, weight=1)
        self.status_lbl = ctk.CTkLabel(bottom, text="", font=self.fonts["small"], text_color=T.TEXT_DIM)
        self.status_lbl.grid(row=0, column=0, sticky="w")
        ctk.CTkButton(bottom, text="完成", font=self.fonts["btn"], width=110, height=38,
                      corner_radius=T.RADIUS_SM, fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER,
                      text_color=T.ON_ACCENT, command=self._close).grid(row=0, column=1, sticky="e")

    # ------------------------------------------------------------------
    def _refresh(self):
        from .app import load_thumb     # 延迟导入避免循环依赖
        self._thumbs.clear()
        for w in self.list_frame.winfo_children():
            w.destroy()
        items = self.tc.get("items", []) or []
        if not items:
            empty = ctk.CTkLabel(self.list_frame, text="还没有物品。点右上「＋ 框选添加物品」加入要整理的物品。",
                                 font=self.fonts["body"], text_color=T.TEXT_DIM, justify="left")
            empty.grid(row=0, column=0, sticky="ew", padx=12, pady=20)
            T.bind_wraplength(empty)
            return
        for i, it in enumerate(items):
            row = ctk.CTkFrame(self.list_frame, fg_color=T.SURFACE_2, corner_radius=T.RADIUS_SM)
            row.grid(row=i, column=0, sticky="ew", pady=4, padx=4)
            row.grid_columnconfigure(1, weight=1)

            thumb = load_thumb(it.get("template"), self._thumbs, max_h=40)
            if thumb is not None:
                ctk.CTkLabel(row, text="", image=thumb).grid(row=0, column=0, padx=(10, 8), pady=8)
            else:
                ctk.CTkLabel(row, text="🎒", font=self.fonts["h2"]).grid(row=0, column=0, padx=(10, 8), pady=8)

            nm = ctk.CTkLabel(row, text=it.get("name", "?"), font=self.fonts["body_b"],
                              text_color=T.TEXT, justify="left", anchor="w")
            nm.grid(row=0, column=1, sticky="ew", padx=(0, 8))
            T.bind_wraplength(nm)

            cur = it.get("action", "use")
            menu = ctk.CTkOptionMenu(
                row, width=92, height=30, font=self.fonts["small"],
                fg_color=T.BTN, button_color=T.BTN, button_hover_color=T.BTN_HOVER, text_color=T.TEXT,
                dropdown_fg_color=T.SURFACE_2, dropdown_text_color=T.TEXT,
                values=[ACTION_LABELS[a] for a in ACTION_ORDER],
                command=lambda label, idx=i: self._set_action(idx, label))
            menu.set(ACTION_LABELS.get(cur, "使用"))
            menu.grid(row=0, column=2, padx=6)

            ctk.CTkButton(row, text="删除", font=self.fonts["small"], height=30, width=52,
                          corner_radius=T.RADIUS_SM, fg_color="transparent", hover_color=T.DANGER,
                          text_color=T.TEXT, border_width=1, border_color=T.BORDER,
                          command=lambda idx=i: self._delete_item(idx)).grid(row=0, column=3, padx=(0, 10))

    # ------------------------------------------------------------------
    def _set_action(self, idx, label):
        """改某件物品的动作（界面显示 -> 内部值 use/discard/sell），写回并存盘。"""
        items = self.tc.get("items", []) or []
        if not (0 <= idx < len(items)):
            return
        val = ACTION_VALUES.get(label, "use")
        items[idx]["action"] = val
        self._save()
        self._toast(f"「{items[idx].get('name', '?')}」动作设为：{label}", T.SUCCESS)

    def _delete_item(self, idx):
        items = self.tc.get("items", []) or []
        if 0 <= idx < len(items):
            removed = items.pop(idx)
            self._save()
            self._refresh()
            self._toast(f"已删除：{removed.get('name', '?')}", T.TEXT_DIM)

    def _add_item(self):
        from .calibrate_dialog import grab_roi_on_app   # 延迟导入避免循环依赖
        rel, crop = grab_roi_on_app(self.app, self.cfg, "框选要整理的物品（图标+名字）",
                                    with_crop=True, toast=self._toast)
        if rel is None:
            return
        if crop is None or crop.size == 0:
            self._toast("截图失败，请重试。", T.DANGER)
            return
        name = self._ask_name()
        if not name:
            return
        rel_path = f"templates/ob_{name}.png"
        if not vision.save_image(rel_path, crop):
            self._toast("保存模板图失败。", T.DANGER)
            return
        self.tc.setdefault("items", []).append(
            {"name": name, "template": rel_path, "action": "use"})
        self._save()
        self._refresh()
        self._toast(f"已添加物品：{name}（默认动作：使用）", T.SUCCESS)

    def _ask_name(self):
        """取名字并清洗（去 / \\ 空格、重名加后缀），参考 calibrate_dialog._ask_name。"""
        dlg = ctk.CTkInputDialog(text="给这件物品起个名字（中英文均可）：", title="命名")
        raw = dlg.get_input()
        if raw is None:
            return None
        name = raw.strip().replace("/", "_").replace("\\", "_").replace(" ", "_")
        if not name:
            name = f"item_{int(time.time())}"
        existing = {it.get("name") for it in self.tc.get("items", []) or []}
        base, n = name, 2
        while name in existing:
            name = f"{base}_{n}"
            n += 1
        return name

    # ------------------------------------------------------------------
    def _save(self):
        cfg_mod.set_task_config(self.cfg, "organize_bag", self.tc)
        cfg_mod.save_config(self.cfg)

    def _toast(self, msg, color=T.TEXT_DIM):
        try:
            self.status_lbl.configure(text=msg, text_color=color)
        except Exception:
            pass

    def _close(self):
        if callable(self.on_done):
            try:
                self.on_done()
            except Exception:
                pass
        self.destroy()
