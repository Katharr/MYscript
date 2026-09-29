# -*- coding: utf-8 -*-
"""整理背包物品清单：单个或连续框选，逐件确认动作后保存。"""

from copy import deepcopy
from uuid import uuid4

import customtkinter as ctk
from PIL import Image

from . import theme as T
from ..core import calib_profiles as calib
from ..core import config as cfg_mod
from ..core import vision
from ..core.inventory import ACTION_LABELS, ACTION_ORDER

ACTION_VALUES = {v: k for k, v in ACTION_LABELS.items()}


def next_item_name(items):
    """只使用尚未占用的显示名；文件名与显示名无关。"""
    used = {it.get("name") for it in items}
    n = 1
    while f"物品 {n:03d}" in used:
        n += 1
    return f"物品 {n:03d}"


class ItemActionDialog(ctk.CTkToplevel):
    """确认当前裁图及动作。返回 (action, continue_batch) 或 None。"""

    def __init__(self, parent, crop, name, batch=False):
        super().__init__(parent)
        self.result = None
        self.batch = batch
        self.fonts = parent.fonts
        self.title("添加物品")
        self.geometry("390x340")
        self.minsize(390, 340)
        self.resizable(False, True)
        self.configure(fg_color=T.BG)
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        # 缩略图仅在确认窗存活期间使用；原始像素留给模板保存。
        rgb = Image.fromarray(crop[:, :, ::-1])
        rgb.thumbnail((320, 105))
        self.preview = ctk.CTkImage(light_image=rgb, dark_image=rgb, size=rgb.size)
        ctk.CTkLabel(self, text="", image=self.preview, height=110).pack(pady=(16, 4))
        ctk.CTkLabel(self, text=name, font=self.fonts["body_b"], text_color=T.TEXT).pack(pady=(0, 10))

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=22)
        ctk.CTkLabel(row, text="整理方式", font=self.fonts["body"], text_color=T.TEXT).pack(side="left")
        self.menu = ctk.CTkOptionMenu(
            row, width=170, height=34, font=self.fonts["body"],
            fg_color=T.BTN, button_color=T.BTN, button_hover_color=T.BTN_HOVER,
            text_color=T.TEXT, dropdown_fg_color=T.SURFACE_2, dropdown_text_color=T.TEXT,
            values=[ACTION_LABELS[a] for a in ACTION_ORDER], command=self._selected)
        self.menu.set("选择整理方式")
        self.menu.pack(side="right")
        self.action = None
        self.status = ctk.CTkLabel(self, text="", font=self.fonts["small"], text_color=T.DANGER)
        self.status.pack(fill="x", padx=22, pady=(4, 0))
        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.pack(fill="x", padx=22, pady=(8, 14))
        ctk.CTkButton(buttons, text="取消", width=70, height=34, font=self.fonts["body"],
                      fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      command=self._cancel).pack(side="left")
        if batch:
            ctk.CTkButton(buttons, text="添加并完成", width=104, height=34,
                          font=self.fonts["body"], command=lambda: self._accept(False)).pack(side="right")
            ctk.CTkButton(buttons, text="添加并继续", width=104, height=34,
                          font=self.fonts["body"], command=lambda: self._accept(True)).pack(side="right", padx=6)
        else:
            ctk.CTkButton(buttons, text="添加", width=100, height=34,
                          font=self.fonts["body"], command=lambda: self._accept(False)).pack(side="right")
        self.bind("<Escape>", self._cancel)
        self.update_idletasks()
        if not self.winfo_viewable():
            self.wait_visibility()
        self.lift()
        self.focus_force()
        self.grab_set()

    def _selected(self, label):
        self.action = ACTION_VALUES.get(label)
        self.menu.configure(text_color=T.TEXT if self.action == "use" else T.DANGER)
        self.status.configure(text="")

    def _accept(self, continue_batch):
        if self.action is None:
            self.status.configure(text="请先选择整理方式")
            return
        self.result = (self.action, continue_batch)
        self.destroy()

    def _cancel(self, _event=None):
        self.destroy()


class InventoryItemsDialog(ctk.CTkToplevel):
    def __init__(self, app, on_done=None):
        super().__init__(app)
        self.app = app
        self.fonts = app.fonts
        self.on_done = on_done
        self.cfg = cfg_mod.load_config()
        self.tc = cfg_mod.task_config(self.cfg, "organize_bag")
        self._thumbs = []
        self._adding = False

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
        if self._adding:
            return
        try:
            self.lift()
            self.focus_force()
        except Exception:
            pass

    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 6))
        top.grid_columnconfigure(0, weight=1)
        ttxt = ctk.CTkFrame(top, fg_color="transparent")
        ttxt.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(ttxt, text="物品清单", font=self.fonts["title"], text_color=T.TEXT).pack(anchor="w")
        actions = ctk.CTkFrame(top, fg_color="transparent")
        actions.grid(row=0, column=1, sticky="e", padx=(12, 0))
        ctk.CTkButton(actions, text="添加一个", font=self.fonts["body"], width=90, height=34,
                      corner_radius=T.RADIUS_SM, fg_color=T.SUCCESS, hover_color=T.SUCCESS_HOVER,
                      text_color=T.BG, command=lambda: self._add_items(False)).pack(side="left")
        ctk.CTkButton(actions, text="批量添加", font=self.fonts["body"], width=90, height=34,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER,
                      text_color=T.TEXT, command=lambda: self._add_items(True)).pack(side="left", padx=(6, 0))

        self.list_frame = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.list_frame.grid(row=1, column=0, sticky="nsew", padx=12, pady=(4, 0))
        self.list_frame.grid_columnconfigure(0, weight=1)
        T.tune_scroll_speed(self.list_frame)

        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.grid(row=2, column=0, sticky="ew", padx=20, pady=(8, 16))
        bottom.grid_columnconfigure(0, weight=1)
        self.status_lbl = ctk.CTkLabel(bottom, text="", font=self.fonts["small"], text_color=T.TEXT_DIM)
        self.status_lbl.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        T.bind_wraplength(self.status_lbl)
        ctk.CTkButton(bottom, text="完成", font=self.fonts["btn"], width=110, height=38,
                      corner_radius=T.RADIUS_SM, fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER,
                      text_color=T.ON_ACCENT, command=self._close).grid(row=0, column=1, sticky="e")

    def _refresh(self):
        from .app import load_thumb
        self._thumbs.clear()
        for w in self.list_frame.winfo_children():
            w.destroy()
        items = self.tc.get("items", []) or []
        if not items:
            ctk.CTkLabel(self.list_frame, text="暂无物品", font=self.fonts["body"],
                         text_color=T.TEXT_DIM).grid(row=0, column=0, sticky="ew", padx=12, pady=20)
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
            ctk.CTkButton(row, text="改名", font=self.fonts["small"], height=30, width=48,
                          fg_color="transparent", hover_color=T.BTN_HOVER, text_color=T.TEXT,
                          command=lambda idx=i: self._rename_item(idx)).grid(row=0, column=2, padx=4)
            cur = it.get("action", "use")
            menu = ctk.CTkOptionMenu(
                row, width=92, height=30, font=self.fonts["small"],
                fg_color=T.BTN, button_color=T.BTN, button_hover_color=T.BTN_HOVER, text_color=T.TEXT,
                dropdown_fg_color=T.SURFACE_2, dropdown_text_color=T.TEXT,
                values=[ACTION_LABELS[a] for a in ACTION_ORDER],
                command=lambda label, idx=i: self._set_action(idx, label))
            menu.set(ACTION_LABELS.get(cur, "使用"))
            menu.grid(row=0, column=3, padx=6)
            ctk.CTkButton(row, text="删除", font=self.fonts["small"], height=30, width=52,
                          corner_radius=T.RADIUS_SM, fg_color="transparent", hover_color=T.DANGER,
                          text_color=T.TEXT, border_width=1, border_color=T.BORDER,
                          command=lambda idx=i: self._delete_item(idx)).grid(row=0, column=4, padx=(0, 10))

    def _apply_items_change(self, change):
        """保存失败时恢复内存清单；批量之前已保存的物品不回滚。"""
        previous_cfg, previous_tc = deepcopy(self.cfg), deepcopy(self.tc)
        try:
            change()
            self._save()
        except Exception:
            self.cfg, self.tc = previous_cfg, previous_tc
            self._refresh()
            self._toast("保存配置失败，本次修改未保留。", T.DANGER)
            return False
        return True

    def _set_action(self, idx, label):
        items = self.tc.get("items", []) or []
        if not (0 <= idx < len(items)) or label not in ACTION_VALUES:
            return
        if self._apply_items_change(lambda: items[idx].update(action=ACTION_VALUES[label])):
            self._toast(f"「{items[idx].get('name', '?')}」动作设为：{label}", T.SUCCESS)

    def _rename_item(self, idx):
        items = self.tc.get("items", []) or []
        if not (0 <= idx < len(items)):
            return
        raw = ctk.CTkInputDialog(text="物品名称：", title="改名").get_input()
        if raw is None:
            return
        name = raw.strip()
        if not name or any(it.get("name") == name for n, it in enumerate(items) if n != idx):
            self._toast("名称不能为空或与现有物品重名。", T.DANGER)
            return

        def change():
            old_name = items[idx].get("name")
            items[idx]["name"] = name
            profiles = self.tc.get("templates_calib", {})
            if old_name in profiles:
                profiles[name] = profiles.pop(old_name)

        if self._apply_items_change(change):
            self._refresh()

    def _delete_item(self, idx):
        items = self.tc.get("items", []) or []
        if 0 <= idx < len(items):
            name = items[idx].get("name", "?")

            def change():
                items.pop(idx)
                self.tc.get("templates_calib", {}).pop(name, None)

            if self._apply_items_change(change):
                self._refresh()
                self._toast(f"已删除：{name}", T.TEXT_DIM)

    def _add_items(self, batch):
        if self._adding:
            return
        from .calibrate_dialog import grab_roi_on_app
        self._adding = True
        added = 0
        try:
            while True:
                rel, crop, pid = grab_roi_on_app(
                    self.app, self.cfg, "框选要整理的物品（图标+名字）", with_crop=True,
                    toast=self._toast, alpha_windows=(self.app, self))
                if rel is None:
                    break
                if crop is None or crop.size == 0:
                    self._toast("截图失败，请重试。", T.DANGER)
                    break
                name = next_item_name(self.tc.get("items", []) or [])
                confirm = ItemActionDialog(self, crop, name, batch=batch)
                self.wait_window(confirm)
                if confirm.result is None:
                    break
                action, keep_going = confirm.result
                # 文件名用独立 ID：改名及删除后的编号复用都不会覆盖旧模板。
                rel_path = f"templates/ob_{uuid4().hex}.png"
                try:
                    image_saved = vision.save_image(rel_path, crop)
                except Exception:
                    image_saved = False
                if not image_saved:
                    self._toast("保存模板图失败。", T.DANGER)
                    break

                def change():
                    self.tc.setdefault("items", []).append(
                        {"name": name, "template": rel_path, "action": action})
                    # 标定指针必须写入同一配置块，避免 _save 覆盖掉新指针。
                    cfg_mod.set_task_config(self.cfg, "organize_bag", self.tc)
                    calib.set_task_profile(self.cfg, "organize_bag", pid)
                    calib.set_template_profile(self.cfg, "organize_bag", name, pid)

                if not self._apply_items_change(change):
                    break
                self._refresh()
                added += 1
                self._toast(f"已添加 {added} 件；最新：{name}（{ACTION_LABELS[action]}）", T.SUCCESS)
                if not batch or not keep_going:
                    break
        finally:
            self._adding = False

    def _save(self):
        cfg_mod.set_task_config(self.cfg, "organize_bag", self.tc)
        cfg_mod.save_config(self.cfg)

    def _toast(self, msg, color=T.TEXT_DIM):
        try:
            self.status_lbl.configure(text=msg, text_color=color)
        except Exception:
            pass

    def _close(self):
        if self._adding:
            return
        if callable(self.on_done):
            try:
                self.on_done()
            except Exception:
                pass
        self.destroy()
