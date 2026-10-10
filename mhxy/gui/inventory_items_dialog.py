# -*- coding: utf-8 -*-
"""整理背包物品清单：框选后就地选择动作，连续录入结束后统一刷新。"""

from copy import deepcopy
from uuid import uuid4

import customtkinter as ctk

from . import theme as T
from ..core import calib_profiles as calib
from ..core import calibration_assets
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
        from .widgets import load_thumb
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
        """只删清单与归属，保留模板；保存成功后才发布独立草稿。"""
        items = self.tc.get("items", []) or []
        if not (0 <= idx < len(items)):
            return
        name = items[idx].get("name", "?")
        try:
            draft = deepcopy(self.cfg)
            # 初次加载的 tc 带有 task_config 补齐的默认值，可能与 cfg 分离。
            draft_tc = deepcopy(self.tc)
            draft_tc["items"].pop(idx)
            draft_tc.get("templates_calib", {}).pop(name, None)
            cfg_mod.set_task_config(draft, "organize_bag", draft_tc)
            cfg_mod.save_config(draft)
        except Exception as exc:
            self._toast(f"删除物品失败，本次修改未保留：{exc}", T.DANGER)
            return
        self.cfg, self.tc = draft, draft_tc
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
                draft = deepcopy(self.cfg)
                rel, crop, pid, action = grab_roi_on_app(
                    self.app, draft, "框选要整理的物品（图标+名字）", with_crop=True,
                    toast=self._toast, alpha_windows=(self.app, self), save_profile=False,
                    action_choices=[(a, ACTION_LABELS[a]) for a in ACTION_ORDER],
                    restore_windows=False)
                if rel is None:
                    break
                if crop is None or crop.size == 0:
                    self._toast("截图失败，请重试。", T.DANGER)
                    break
                if action not in ACTION_ORDER:
                    self._toast("未选择有效的整理方式，本次未添加。", T.DANGER)
                    break
                name = next_item_name(cfg_mod.task_config(draft, "organize_bag").get("items", []) or [])
                # 文件名用独立 ID：改名及删除后的编号复用都不会覆盖旧模板。
                rel_path = f"templates/ob_{uuid4().hex}.png"
                try:
                    encoded = vision.encode_png(crop)
                    draft_tc = cfg_mod.task_config(draft, "organize_bag")
                    draft_tc.setdefault("items", []).append(
                        {"name": name, "template": rel_path, "action": action})
                    cfg_mod.set_task_config(draft, "organize_bag", draft_tc)
                    calib.set_task_profile(draft, "organize_bag", pid)
                    calib.set_template_profile(draft, "organize_bag", name, pid)
                    calibration_assets.commit_image(draft, rel_path, encoded)
                except Exception as exc:
                    self._toast(f"添加物品失败：{exc}", T.DANGER)
                    break

                self.cfg = draft
                self.tc = draft["tasks"]["organize_bag"]
                added += 1
                self._toast(f"已添加 {added} 件；最新：{name}（{ACTION_LABELS[action]}）", T.SUCCESS)
                if not batch:
                    break
        except Exception as exc:
            self._toast(f"录入物品失败：{exc}", T.DANGER)
        finally:
            self._adding = False
            try:
                if added:
                    self._refresh()
            finally:
                self._restore_capture_windows()

    def _restore_capture_windows(self):
        """一次录入会话只恢复一次窗口；包括取消、截图或保存失败。"""
        for window in (self.app, self):
            try:
                window.attributes("-alpha", 1.0)
            except Exception:
                pass
        try:
            self.lift()
            self.focus_force()
        except Exception:
            pass

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
