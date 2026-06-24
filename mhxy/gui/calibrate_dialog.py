# -*- coding: utf-8 -*-
"""
标定对话框（全部在 GUI 内完成，无黑窗、无子进程）。

按任务的 `CALIBRATION` spec 驱动渲染（见 tasks/base.py Task.CALIBRATION），三类卡片：
1. 区域与按钮：框选区域 → 存为相对游戏窗口的 [x,y,w,h]，写入 tc["regions"][key]。
2. 标志模板：框选并裁图 → 存成 templates/tm_<key>.png，写入 tc["templates"][key]。
3. 监控清单（仅秒装备等 watchlist=True 的任务）：框选装备图标+名字加入清单。

框选时把本助手窗口透明化，确保截图里只有游戏画面。新增任务无需改本文件，只要在 Task 上写 CALIBRATION。
"""

import time

import customtkinter as ctk

from . import theme as T
from .roi_overlay import select_roi_on_screen
from ..core import config as cfg_mod
from ..core import window as win_mod
from ..core import vision
from ..core.teaming import TEAM_CALIBRATION
from ..tasks import get_task

# 非注册的「共享命名空间」标定 spec（key = 写入 cfg.tasks.<key>）。
# 组队是跨任务共享资产、不是可运行任务，故 get_task("teaming") 拿不到，走这里。
_VIRTUAL_SPECS = {"teaming": ("组队（全局共享）", TEAM_CALIBRATION)}


class CalibrateDialog(ctk.CTkToplevel):
    def __init__(self, app, task_name="sniper", on_done=None):
        super().__init__(app)
        self.app = app
        self.fonts = app.fonts
        self.on_done = on_done
        self.task_name = task_name

        task_cls = get_task(task_name)
        if task_cls is not None:
            self.spec = getattr(task_cls, "CALIBRATION",
                                {"regions": [], "templates": [], "watchlist": False})
            title_name = getattr(task_cls, "title", task_name)
        elif task_name in _VIRTUAL_SPECS:
            title_name, self.spec = _VIRTUAL_SPECS[task_name]
        else:
            self.spec = {"regions": [], "templates": [], "watchlist": False}
            title_name = task_name

        self.title(f"标定 · {title_name}")
        self.geometry("680x640")
        self.minsize(560, 420)
        self.configure(fg_color=T.BG)
        self.transient(app)

        self.cfg = cfg_mod.load_config()
        self.tc = cfg_mod.task_config(self.cfg, task_name)

        self.region_rows = {}
        self.template_rows = {}
        self._full_window_keys = set()   # 支持「留空=整窗」的大检测区 key
        self.item_list = None

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
        self.grid_rowconfigure(0, weight=1)

        body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        body.grid(row=0, column=0, sticky="nsew", padx=4, pady=(4, 0))
        body.grid_columnconfigure(0, weight=1)
        T.tune_scroll_speed(body)

        row = 0
        top = ctk.CTkFrame(body, fg_color="transparent")
        top.grid(row=row, column=0, sticky="ew", padx=16, pady=(14, 8)); row += 1
        ctk.CTkLabel(top, text="标定向导", font=self.fonts["title"], text_color=T.TEXT).pack(anchor="w")
        ctk.CTkLabel(top, text="先把游戏切到对应界面，再按提示逐项框选。框选时本助手会临时隐身。",
                     justify="left", font=self.fonts["small"], text_color=T.TEXT_DIM).pack(anchor="w", pady=(4, 0))

        # ① 区域与按钮
        regions = self.spec.get("regions", [])
        if regions:
            rcard = self._card(body, row); row += 1
            ctk.CTkLabel(rcard, text="① 区域与按钮", font=self.fonts["h2"], text_color=T.TEXT).grid(
                row=0, column=0, columnspan=3, sticky="w", padx=16, pady=(14, 6))
            for i, item in enumerate(regions):
                key, name, desc = item[0], item[1], item[2]
                full_window = len(item) > 3 and bool(item[3])   # 第4元素=True 表示可整窗
                if full_window:
                    self._full_window_keys.add(key)
                self._spec_row(rcard, 1 + i, key, name, desc, self.region_rows,
                               lambda k=key, n=name: self._calibrate_region(k, n),
                               full_window=full_window)
            ctk.CTkFrame(rcard, fg_color="transparent", height=8).grid(row=99, column=0)

        # ② 标志模板（框选裁图）
        templates = self.spec.get("templates", [])
        if templates:
            tcard = self._card(body, row); row += 1
            ctk.CTkLabel(tcard, text="② 标志模板（框选裁图）", font=self.fonts["h2"], text_color=T.TEXT).grid(
                row=0, column=0, columnspan=3, sticky="w", padx=16, pady=(14, 6))
            ctk.CTkLabel(tcard, text="框小而独特的区域（按钮/文字/图标），别框会变的数字或背景。",
                         font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left").grid(
                             row=1, column=0, columnspan=3, sticky="w", padx=16, pady=(0, 4))
            for i, (key, name, desc) in enumerate(templates):
                self._spec_row(tcard, 2 + i, key, name, desc, self.template_rows,
                               lambda k=key, n=name: self._calibrate_template(k, n))
            ctk.CTkFrame(tcard, fg_color="transparent", height=8).grid(row=99, column=0)

        # ③ 监控清单（仅 watchlist=True 的任务）
        if self.spec.get("watchlist"):
            self._build_watchlist_card(body, row); row += 1

        # 底部固定「完成」
        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.grid(row=1, column=0, sticky="ew", padx=20, pady=(4, 16))
        bottom.grid_columnconfigure(0, weight=1)
        self.status_lbl = ctk.CTkLabel(bottom, text="", font=self.fonts["small"], text_color=T.TEXT_DIM)
        self.status_lbl.grid(row=0, column=0, sticky="w")
        ctk.CTkButton(bottom, text="完成", font=self.fonts["btn"], width=110, height=38,
                      corner_radius=T.RADIUS_SM, fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER, text_color=T.ON_ACCENT,
                      command=self._close).grid(row=0, column=1, sticky="e")

    def _card(self, parent, grid_row):
        c = ctk.CTkFrame(parent, fg_color=T.SURFACE, corner_radius=T.RADIUS,
                         border_width=1, border_color=T.BORDER)
        c.grid(row=grid_row, column=0, sticky="ew", padx=16, pady=(8, 8))
        c.grid_columnconfigure(0, weight=1)
        return c

    def _spec_row(self, card, grid_row, key, name, desc, store, command, full_window=False):
        row = ctk.CTkFrame(card, fg_color=T.SURFACE_2, corner_radius=T.RADIUS_SM)
        row.grid(row=grid_row, column=0, sticky="ew", padx=12, pady=4)
        row.grid_columnconfigure(0, weight=1)
        txt = ctk.CTkFrame(row, fg_color="transparent")
        txt.grid(row=0, column=0, sticky="w", padx=12, pady=8)
        ctk.CTkLabel(txt, text=name, font=self.fonts["body_b"], text_color=T.TEXT).pack(anchor="w")
        ctk.CTkLabel(txt, text=desc, font=self.fonts["small"], text_color=T.TEXT_DIM).pack(anchor="w")
        status = ctk.CTkLabel(row, text="", font=self.fonts["small"], width=90)
        status.grid(row=0, column=1, padx=6)
        # 可整窗的大检测区：多给一个「用整窗」按钮（把该区清空＝整窗检测）
        if full_window:
            ctk.CTkButton(row, text="用整窗", font=self.fonts["small"], width=58, height=30,
                          corner_radius=T.RADIUS_SM, fg_color="transparent", hover_color=T.BORDER, text_color=T.TEXT,
                          border_width=1, border_color=T.BORDER,
                          command=lambda k=key, n=name: self._use_full_window(k, n)).grid(
                              row=0, column=2, padx=(0, 4))
        ctk.CTkButton(row, text="框选", font=self.fonts["body"], width=72, height=30,
                      corner_radius=T.RADIUS_SM, fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER, text_color=T.ON_ACCENT,
                      command=command).grid(row=0, column=3, padx=(6, 12))
        store[key] = status

    def _use_full_window(self, key, name):
        """把某个可整窗的大检测区清空 → 运行时用整个窗口当检测区。"""
        self.tc.setdefault("regions", {})[key] = None
        self._save()
        self._refresh()
        self._toast(f"「{name}」已设为整窗检测（无需框选）", T.SUCCESS)

    def _build_watchlist_card(self, parent, grid_row):
        icard = self._card(parent, grid_row)
        head = ctk.CTkFrame(icard, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 6))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text="③ 要抢的装备", font=self.fonts["h2"], text_color=T.TEXT).grid(
            row=0, column=0, sticky="w")
        ctk.CTkButton(head, text="＋ 框选添加", font=self.fonts["body"], width=110, height=32,
                      corner_radius=T.RADIUS_SM, fg_color=T.SUCCESS, hover_color=T.SUCCESS_HOVER,
                      text_color=T.BG, command=self._add_item).grid(row=0, column=1, sticky="e")
        ctk.CTkLabel(icard, text="提示：连「图标 + 名字」一起框，别框价格（价格会变，框了反而认不出）。",
                     font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left").grid(
                         row=1, column=0, sticky="w", padx=16, pady=(0, 6))
        self.item_list = ctk.CTkFrame(icard, fg_color="transparent")
        self.item_list.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 12))
        self.item_list.grid_columnconfigure(0, weight=1)

    # ------------------------------------------------------------------
    # 框选：藏起自己 -> 激活游戏 -> 截图框选 -> 复原。返回 (rel_roi, crop)
    # ------------------------------------------------------------------
    def _set_alpha(self, a):
        for w in (self.app, self):
            try:
                w.attributes("-alpha", a)
            except Exception:
                pass

    def _grab_roi(self, prompt, with_crop=False):
        title = self.cfg.get("window_title", "梦幻西游")
        offset = self.cfg.get("window_offset", [0, 0])
        # 不激活/不切前台：标定只在【当前屏幕】上框选——之前强制 activate 会把配置选中的那个号顶到
        # 前面、盖住你真正想标的窗口（“老是和想标的不一样”）。这里不动窗口层级，框完再按
        # “框选区域落在哪个游戏窗口”反查参照窗口来算相对坐标，所以你把哪个号摆在前面、就标到哪个。
        hint_wins = win_mod.locate_all(title, offset)
        if not hint_wins:
            self._toast(f"没找到游戏窗口（标题含「{title}」），请先打开游戏。", T.WARN)
            return None, None
        # around_point 仅用于决定在哪块显示器弹出框选层（不改变前后层级），取任一游戏窗口中心即可。
        hr = hint_wins[0].rect()
        center = (hr[0] + hr[2] // 2, hr[1] + hr[3] // 2) if hr else None

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

        # 用「框选区域中心落在哪个游戏窗口」定参照窗口（= 你实际标的那个号），相对坐标/基准尺寸都按它算。
        # 多开各号同尺寸共用标定（项目约定），故框任意一个号都通用。
        cx = roi_abs[0] + roi_abs[2] // 2
        cy = roi_abs[1] + roi_abs[3] // 2
        ref = win_mod.window_at_point(title, offset, cx, cy)
        wr = ref.rect() if ref else None
        if wr is None:
            self._toast("框选区域不在任何游戏窗口内：请把要标的窗口移到前面，框在它的画面里。", T.WARN)
            return None, None
        # 记录基准尺寸：标定时的窗口尺寸即「还原尺寸」按钮要拉回的目标（与模板/点位天然一致）。
        # base_size 在顶层 targets，单独存 self.cfg（_save() 只存 task_config）。
        self.cfg.setdefault("targets", {})["base_size"] = [wr[2], wr[3]]
        cfg_mod.save_config(self.cfg)
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

    # ---- 标志模板标定（裁图存盘）----
    def _calibrate_template(self, key, name):
        rel, crop = self._grab_roi(f"框选「{name}」（会裁下来存成模板图）", with_crop=True)
        if rel is None:
            return
        if crop is None or crop.size == 0:
            self._toast("截图失败，请重试。", T.DANGER)
            return
        rel_path = f"templates/tm_{key}.png"
        if not vision.save_image(rel_path, crop):
            self._toast("保存模板图失败。", T.DANGER)
            return
        self.tc.setdefault("templates", {})[key] = rel_path
        self._save()
        self._refresh()
        self._toast(f"已记录模板 {name}", T.SUCCESS)

    # ---- 添加装备（watchlist）----
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
        regions = self.tc.get("regions", {})
        for key, status in self.region_rows.items():
            if regions.get(key):
                status.configure(text="● 已框选", text_color=T.SUCCESS)
            elif key in self._full_window_keys:
                status.configure(text="○ 整窗(默认)", text_color=T.TEXT_DIM)
            else:
                status.configure(text="○ 未标定", text_color=T.TEXT_DIM)

        templates = self.tc.get("templates", {})
        for key, status in self.template_rows.items():
            ok = bool(templates.get(key)) and vision.load_template(templates.get(key)) is not None
            status.configure(text="● 已裁图" if ok else "○ 未标定",
                             text_color=T.SUCCESS if ok else T.TEXT_DIM)

        if self.item_list is not None:
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
                                  corner_radius=T.RADIUS_SM, fg_color="transparent", hover_color=T.DANGER, text_color=T.TEXT,
                                  border_width=1, border_color=T.BORDER,
                                  command=lambda idx=i: self._delete_item(idx)).grid(
                                      row=0, column=1, padx=10)

    def _save(self):
        cfg_mod.set_task_config(self.cfg, self.task_name, self.tc)
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
