# -*- coding: utf-8 -*-
"""SettingsPage：独立页面，保留既有布局与任务控制。"""
import customtkinter as ctk
from . import theme as T
from ..core import config as cfg_mod
from ..tasks.sniper import SniperTask
from .widgets import Card, bind_wraplength
from .shortcuts import _compose_stop_hotkey, _split_stop_hotkey, HOTKEY_NAMES, DEFAULT_STOP_HOTKEY, FAILSAFE_CORNERS, FAILSAFE_LABELS, FAILSAFE_VALUE_OF, DEFAULT_FAILSAFE


class SettingsPage(ctk.CTkFrame):
    """设置页：基础项 + 「速度与节奏（手速）」一组滑块，全部可在界面里调。"""

    # 速度/节奏滑块定义：(存储位置, 键, 标签, 下限, 上限, 步数, 小数位)
    #   loc: "humanize" 存到 cfg["humanize"]；"loop" 存到 tasks.sniper.loop
    SPEED_FIELDS = [
        ("humanize", "speed", "整体速度倍率", 0.5, 3.0, 25, 2),
        ("humanize", "snipe_speed", "命中下单极速倍率", 1.0, 6.0, 25, 1),
        ("humanize", "px_per_step", "鼠标移动步长(px)", 6, 40, 34, 0),
        ("loop", "shelf_load_wait_sec", "商城打开等待(秒)", 0.2, 3.0, 28, 2),
        ("loop", "shop_close_wait_sec", "商城关闭等待(秒)", 0.1, 1.5, 28, 2),
        ("loop", "second_buy_load_wait_sec", "二次购买加载等待(秒)", 0.02, 0.3, 28, 2),
        ("loop", "second_buy_retry_interval_sec", "二次购买重试间隔(秒)", 0.01, 0.1, 18, 2),
        ("loop", "second_buy_timeout_sec", "二次购买匹配超时(秒)", 0.2, 3.0, 28, 2),
        ("loop", "purchase_timeout_sec", "购买结果等待(秒)", 0.5, 5.0, 45, 2),
        ("humanize", "idle_chance", "走神概率", 0.0, 0.10, 20, 3),
        ("humanize", "click_radius", "落点随机半径(px)", 0, 12, 12, 0),
        ("humanize", "interval_jitter", "间隔抖动比例", 0.0, 0.8, 16, 2),
    ]

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.fonts = app.fonts
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(self, text="设置", font=self.fonts["title"], text_color=T.TEXT).grid(
            row=0, column=0, sticky="w", padx=4, pady=(2, 14))

        # 参数多了，内容区做成可滚动
        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        scroll.grid(row=1, column=0, sticky="nsew", padx=0, pady=0)
        scroll.grid_columnconfigure(0, weight=1)
        T.tune_scroll_speed(scroll)

        self._value_labels = {}   # key -> 数值显示 Label
        self.speed_vars = {}      # key -> DoubleVar

        self._build_basic_card(scroll)
        self._build_speed_card(scroll)

        ctk.CTkButton(scroll, text="保存设置", font=self.fonts["btn"], height=42, width=160,
                      corner_radius=T.RADIUS_SM, fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER, text_color=T.ON_ACCENT,
                      command=self._save).grid(row=2, column=0, padx=4, pady=(4, 20), sticky="w")

    # ---- 基础卡片 ----
    def _build_basic_card(self, parent):
        card = Card(parent)
        card.grid(row=0, column=0, sticky="ew", padx=4, pady=(0, 14))
        card.grid_columnconfigure(1, weight=1)

        cfg = self.app.cfg
        self.var_title = ctk.StringVar(value=cfg.get("window_title", "梦幻西游"))
        self.var_backend = ctk.StringVar(value=cfg.get("input_backend", "sendinput"))
        sc, sa, ss, sk = _split_stop_hotkey(cfg.get("hotkey_stop", DEFAULT_STOP_HOTKEY))
        self.var_hk_ctrl = ctk.BooleanVar(value=sc)
        self.var_hk_alt = ctk.BooleanVar(value=sa)
        self.var_hk_shift = ctk.BooleanVar(value=ss)
        self.var_hk_key = ctk.StringVar(value=sk)
        fc = cfg.get("failsafe_corner", DEFAULT_FAILSAFE)
        self.var_failsafe = ctk.StringVar(value=FAILSAFE_CORNERS.get(fc, "右上角"))
        self.var_threshold = ctk.DoubleVar(value=self._get_threshold())

        ctk.CTkLabel(card, text="基础", font=self.fonts["h2"], text_color=T.TEXT).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=16, pady=(14, 4))
        self._row(card, 1, "游戏窗口标题关键字",
                  ctk.CTkEntry(card, textvariable=self.var_title, font=self.fonts["body"],
                               fg_color=T.SURFACE_2, border_color=T.BORDER, width=240))
        self._row(card, 2, "鼠标输入后端",
                  ctk.CTkOptionMenu(card, variable=self.var_backend,
                                    values=["sendinput", "pyautogui", "pydirectinput"],
                                    font=self.fonts["body"], fg_color=T.SURFACE_2,
                                    button_color=T.BORDER, button_hover_color=T.ACCENT, text_color=T.TEXT, dropdown_text_color=T.TEXT, width=240))
        self._row(card, 3, "急停 快捷键（停止一切）", self._build_hotkey(card), sticky="ew")
        self._row(card, 4, "失控急停（甩鼠标到屏幕角）", self._build_failsafe(card), sticky="ew")
        self._row(card, 5, "识别置信度（匹配阈值）", self._build_threshold(card), sticky="ew")

    def _build_hotkey(self, parent):
        box = ctk.CTkFrame(parent, fg_color="transparent")
        row = ctk.CTkFrame(box, fg_color="transparent")
        row.pack(anchor="w")
        for txt, var in (("Ctrl", self.var_hk_ctrl), ("Alt", self.var_hk_alt), ("Shift", self.var_hk_shift)):
            ctk.CTkCheckBox(row, text=txt, variable=var, font=self.fonts["body"],
                            text_color=T.TEXT, fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER,
                            checkbox_width=20, checkbox_height=20).pack(side="left", padx=(0, 14))
        ctk.CTkOptionMenu(row, variable=self.var_hk_key, values=HOTKEY_NAMES,
                          font=self.fonts["body"], fg_color=T.SURFACE_2,
                          button_color=T.BORDER, button_hover_color=T.ACCENT, text_color=T.TEXT,
                          dropdown_text_color=T.TEXT, width=120).pack(side="left")
        return box

    def _build_failsafe(self, parent):
        box = ctk.CTkFrame(parent, fg_color="transparent")
        ctk.CTkOptionMenu(box, variable=self.var_failsafe, values=FAILSAFE_LABELS,
                          font=self.fonts["body"], fg_color=T.SURFACE_2,
                          button_color=T.BORDER, button_hover_color=T.ACCENT, text_color=T.TEXT,
                          dropdown_text_color=T.TEXT, width=160).pack(anchor="w")
        return box

    # ---- 速度与节奏卡片 ----
    def _build_speed_card(self, parent):
        card = Card(parent)
        card.grid(row=1, column=0, sticky="ew", padx=4, pady=(0, 14))
        card.grid_columnconfigure(0, weight=1)

        head = ctk.CTkFrame(card, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 2))
        ctk.CTkLabel(head, text="速度与节奏（手速）", font=self.fonts["h2"],
                     text_color=T.TEXT).pack(anchor="w")
        warn = ctk.CTkLabel(head, text="越快越像机器，封号风险越高。",
                     font=self.fonts["small"], text_color=T.WARN, justify="left")
        warn.pack(fill="x", pady=(2, 0))
        bind_wraplength(warn)

        for i, (loc, key, label, lo, hi, steps, dec) in enumerate(self.SPEED_FIELDS):
            self._slider_row(card, i + 1, loc, key, label, lo, hi, steps, dec)

    def _slider_row(self, parent, row, loc, key, label, lo, hi, steps, dec):
        box = ctk.CTkFrame(parent, fg_color="transparent")
        box.grid(row=row, column=0, sticky="ew", padx=16, pady=(10, 6))
        box.grid_columnconfigure(1, weight=1)

        # 标签 + 滑块 + 数值
        ctk.CTkLabel(box, text=label, font=self.fonts["body_b"], text_color=T.TEXT,
                     width=150, anchor="w").grid(row=0, column=0, sticky="w")

        var = ctk.DoubleVar(value=self._get_value(loc, key))
        self.speed_vars[key] = var
        fmt = f"{{:.{dec}f}}"
        val_lbl = ctk.CTkLabel(box, text=fmt.format(var.get()), font=self.fonts["body_b"],
                               text_color=T.ACCENT, width=56, anchor="e")
        val_lbl.grid(row=0, column=2, sticky="e", padx=(8, 0))
        self._value_labels[key] = (val_lbl, fmt)

        slider = ctk.CTkSlider(box, from_=lo, to=hi, number_of_steps=steps, variable=var,
                               command=lambda v, k=key: self._on_slider(k, v),
                               progress_color=T.ACCENT, button_color=T.ACCENT,
                               button_hover_color=T.ACCENT_HOVER)
        slider.grid(row=0, column=1, sticky="ew", padx=10)

    def _on_slider(self, key, val):
        lbl, fmt = self._value_labels[key]
        lbl.configure(text=fmt.format(float(val)))

    # ---- 取值/存值助手 ----
    def _get_value(self, loc, key):
        if loc == "humanize":
            src = self.app.cfg.get("humanize", {})
            default = cfg_mod.DEFAULT_CONFIG["humanize"].get(key, 0)
        else:  # loop
            src = cfg_mod.task_config(self.app.cfg, SniperTask.name).get("loop", {})
            default = cfg_mod.DEFAULT_CONFIG["tasks"]["sniper"]["loop"].get(key, 0)
        try:
            v = src.get(key, default)
            return float(default if v is None else v)
        except (TypeError, ValueError):
            return float(default)

    def _row(self, parent, r, label, widget, sticky="w"):
        ctk.CTkLabel(parent, text=label, font=self.fonts["body"], text_color=T.TEXT).grid(
            row=r, column=0, sticky="w", padx=16, pady=12)
        widget.grid(row=r, column=1, sticky=sticky, padx=16, pady=12)

    # ---- 置信度滑块 ----
    def _get_threshold(self):
        tc = cfg_mod.task_config(self.app.cfg, SniperTask.name)
        try:
            return float(tc.get("loop", {}).get("match_threshold", 0.85))
        except (TypeError, ValueError):
            return 0.85

    def _build_threshold(self, parent):
        box = ctk.CTkFrame(parent, fg_color="transparent")
        top = ctk.CTkFrame(box, fg_color="transparent")
        top.pack(anchor="w", fill="x")
        slider = ctk.CTkSlider(top, from_=0.60, to=0.99, number_of_steps=39, width=240,
                               variable=self.var_threshold, command=self._on_threshold,
                               progress_color=T.ACCENT, button_color=T.ACCENT,
                               button_hover_color=T.ACCENT_HOVER)
        slider.pack(side="left")
        self.thr_value = ctk.CTkLabel(top, text=f"{self.var_threshold.get():.2f}",
                                      font=self.fonts["body_b"], text_color=T.ACCENT, width=48)
        self.thr_value.pack(side="left", padx=(12, 0))
        return box

    def _on_threshold(self, val):
        self.thr_value.configure(text=f"{float(val):.2f}")

    def _save(self):
        # 重新读盘再改，避免覆盖掉标定向导刚写入的 regions/watchlist。
        cfg = cfg_mod.load_config()
        cfg["window_title"] = self.var_title.get().strip() or "梦幻西游"
        cfg["input_backend"] = self.var_backend.get()
        cfg["hotkey_stop"] = _compose_stop_hotkey(
            self.var_hk_ctrl.get(), self.var_hk_alt.get(),
            self.var_hk_shift.get(), self.var_hk_key.get())
        cfg["failsafe_corner"] = FAILSAFE_VALUE_OF.get(self.var_failsafe.get(), DEFAULT_FAILSAFE)

        hz = cfg.setdefault("humanize", {})
        tc = cfg_mod.task_config(cfg, SniperTask.name)
        loop = tc.setdefault("loop", {})
        loop["match_threshold"] = round(float(self.var_threshold.get()), 2)

        # 速度/节奏滑块：按来源写回 humanize 或 loop；px_per_step 存整数
        for loc, key, *_ in self.SPEED_FIELDS:
            val = float(self.speed_vars[key].get())
            if key in ("px_per_step", "click_radius"):
                val = int(round(val))
            else:
                val = round(val, 3)
            (hz if loc == "humanize" else loop)[key] = val

        cfg_mod.set_task_config(cfg, SniperTask.name, tc)
        cfg_mod.save_config(cfg)
        self.app.cfg = cfg
        self.app.toast("设置已保存")

    def refresh(self):
        self.var_title.set(self.app.cfg.get("window_title", "梦幻西游"))
        self.var_backend.set(self.app.cfg.get("input_backend", "sendinput"))
        sc, sa, ss, sk = _split_stop_hotkey(self.app.cfg.get("hotkey_stop", DEFAULT_STOP_HOTKEY))
        self.var_hk_ctrl.set(sc)
        self.var_hk_alt.set(sa)
        self.var_hk_shift.set(ss)
        self.var_hk_key.set(sk)
        fc = self.app.cfg.get("failsafe_corner", DEFAULT_FAILSAFE)
        self.var_failsafe.set(FAILSAFE_CORNERS.get(fc, "右上角"))
        thr = self._get_threshold()
        self.var_threshold.set(thr)
        if hasattr(self, "thr_value"):
            self.thr_value.configure(text=f"{thr:.2f}")
        for loc, key, *_ in self.SPEED_FIELDS:
            if key in self.speed_vars:
                v = self._get_value(loc, key)
                self.speed_vars[key].set(v)
                lbl, fmt = self._value_labels[key]
                lbl.configure(text=fmt.format(v))
