# -*- coding: utf-8 -*-
"""紧凑启动形态的一键启动面板。"""

import re

import customtkinter as ctk

from . import theme as T
from ..core import config as cfg_mod
from ..core import window as win_mod
from ..core.runner import TaskRunner
from ..tasks.launch_login import LaunchLoginTask


_PROGRESS_RE = re.compile(r"^\[([^\]]+)\]\s*开始处理（(\d+)/(\d+)）。")
_LABEL_RE = re.compile(r"^\[([^\]]+)\]")


class QuickStartPanel(ctk.CTkFrame):
    """零游戏窗口时显示的档案选择与一键启动面板。"""

    LOG_SOURCE = "启动登录"

    def __init__(self, master, app):
        super().__init__(master, fg_color=T.BG)
        self.app = app
        self.fonts = app.fonts
        self.runner = None
        self._launched = False
        self._profile_count = 0
        self._log_history = []

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)
        self.grid_rowconfigure(5, weight=1)
        self._build()
        self.refresh()

    def _build(self):
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=T.SP_5, pady=(T.SP_5, T.SP_2))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text="梦幻 · 时空 助手", font=self.fonts["title"],
                     text_color=T.TEXT).grid(row=0, column=0, sticky="w")
        self.lbl_count = ctk.CTkLabel(head, text="", font=self.fonts["small"], text_color=T.TEXT_DIM)
        self.lbl_count.grid(row=0, column=1, sticky="e")

        ctk.CTkFrame(self, height=1, fg_color=T.BORDER).grid(
            row=1, column=0, sticky="ew", padx=T.SP_5, pady=(0, T.SP_2))

        self.profile_list = ctk.CTkScrollableFrame(self, fg_color="transparent", corner_radius=0)
        self.profile_list.grid(row=2, column=0, sticky="nsew", padx=T.SP_4, pady=(0, T.SP_2))
        self.profile_list.grid_columnconfigure(0, weight=1)
        T.tune_scroll_speed(self.profile_list)

        controls = ctk.CTkFrame(self, fg_color="transparent")
        controls.grid(row=3, column=0, sticky="ew", padx=T.SP_5, pady=(T.SP_2, T.SP_2))
        controls.grid_columnconfigure(0, weight=1)
        self.btn_run = ctk.CTkButton(
            controls, text="⚡  一键启动", font=self.fonts["btn"], height=46,
            corner_radius=T.RADIUS_SM, fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER,
            text_color=T.ON_ACCENT, command=self._toggle_run)
        self.btn_run.grid(row=0, column=0, sticky="ew", pady=(0, T.SP_2))
        self.btn_full = ctk.CTkButton(
            controls, text="进入主界面", font=self.fonts["body"], height=36,
            corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER,
            text_color=T.TEXT, border_width=1, border_color=T.BORDER, command=self._enter_full)
        self.btn_full.grid(row=1, column=0, sticky="ew")

        ctk.CTkFrame(self, height=1, fg_color=T.BORDER).grid(
            row=4, column=0, sticky="ew", padx=T.SP_5, pady=(T.SP_2, T.SP_2))

        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.grid(row=5, column=0, sticky="nsew", padx=T.SP_5, pady=(0, T.SP_4))
        bottom.grid_columnconfigure(0, weight=1)
        bottom.grid_rowconfigure(1, weight=1)
        self.lbl_status = ctk.CTkLabel(bottom, text="状态：待启动", font=self.fonts["small"],
                                       text_color=T.TEXT_DIM, justify="left")
        self.lbl_status.grid(row=0, column=0, sticky="ew", pady=(0, T.SP_1))
        T.bind_wraplength(self.lbl_status)
        self.log = ctk.CTkTextbox(bottom, font=self.fonts["mono"], fg_color=T.SURFACE_2,
                                  text_color=T.TEXT, corner_radius=T.RADIUS_SM, wrap="word", height=82)
        self.log.grid(row=1, column=0, sticky="nsew")
        T.apply_log_tags(self.log._textbox)
        self.log.configure(state="disabled")

    def refresh(self):
        """从配置真源重建档案行，避免维护小窗自己的副本。"""
        cfg = cfg_mod.load_config()
        self.app.cfg = cfg
        profiles = (cfg.get("account_launch") or {}).get("profiles") or []
        self._profile_count = len(profiles)
        self.lbl_count.configure(text="（%d 个）" % len(profiles) if profiles else "")
        for child in self.profile_list.winfo_children():
            child.destroy()
        if not profiles:
            self._set_status("状态：无档案")
        else:
            for index, profile in enumerate(profiles):
                self._render_profile(index, profile)
            if not self._launched:
                self._set_status("状态：待启动")
        self._sync_run_button()

    def _render_profile(self, index, profile):
        row = ctk.CTkFrame(self.profile_list, fg_color=T.SURFACE, corner_radius=T.RADIUS_SM,
                           border_width=1, border_color=T.BORDER)
        row.grid(row=index, column=0, sticky="ew", padx=T.SP_1, pady=T.SP_1)
        row.grid_columnconfigure(1, weight=1)
        enabled = ctk.BooleanVar(value=profile.get("enabled", True))
        ctk.CTkCheckBox(row, text="", variable=enabled, width=28,
                        command=lambda i=index, v=enabled: self._set_enabled(i, v.get())).grid(
                            row=0, column=0, sticky="w", padx=(T.SP_3, T.SP_1), pady=T.SP_2)
        label = ctk.CTkLabel(row, text=profile.get("label") or "未命名档案", font=self.fonts["body"],
                             text_color=T.TEXT, justify="left")
        label.grid(row=0, column=1, sticky="ew", padx=(0, T.SP_3), pady=T.SP_2)
        T.bind_wraplength(label)

    def _set_enabled(self, index, enabled):
        """直接写回 account_launch.profiles，不另建小窗状态。"""
        cfg = cfg_mod.load_config()
        profiles = (cfg.setdefault("account_launch", {}).setdefault("profiles", []))
        if 0 <= index < len(profiles):
            profiles[index]["enabled"] = bool(enabled)
            cfg_mod.save_config(cfg)
            self.app.cfg = cfg
        self.refresh()

    def _enabled_count(self):
        cfg = cfg_mod.load_config()
        profiles = (cfg.get("account_launch") or {}).get("profiles") or []
        return sum(1 for profile in profiles if profile.get("enabled", True))

    def _sync_run_button(self):
        if self._launched:
            return
        enabled = self._enabled_count() > 0
        self.btn_run.configure(state="normal" if enabled else "disabled", text="⚡  一键启动",
                               fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER)

    def _toggle_run(self):
        if self.runner is not None and self.runner.is_running():
            self._stop_and_enter_full()
            return
        cfg = cfg_mod.load_config()
        self.app.cfg = cfg
        self.runner = TaskRunner(LaunchLoginTask(), cfg)
        ok, problems = self.runner.start()
        if not ok:
            self.runner = None
            for problem in problems:
                self.append("无法启动：" + problem, "error")
            self._sync_run_button()
            return
        self._launched = True
        self.btn_run.configure(text="■  停止", state="normal", fg_color=T.DANGER, hover_color=T.DANGER_HOVER)
        self._move_away_from_launcher()

    def _move_away_from_launcher(self):
        """启动后靠右下且取消置顶，避免物理遮挡启动器按钮。"""
        try:
            self.app.update_idletasks()
            width = max(1, self.app.winfo_width())
            height = max(1, self.app.winfo_height())
            xy = win_mod.corner_left_top("bottom_right", width, height)
            if xy is not None:
                self.app.geometry("+%d+%d" % xy)
            self.app.attributes("-topmost", False)
        except Exception:
            pass

    def _stop_and_enter_full(self):
        if self.runner is not None and self.runner.is_running():
            self.runner.stop()
        self._launched = False
        self.app.enter_full()

    def _enter_full(self):
        self._stop_and_enter_full()

    def on_close(self):
        """紧凑窗口的 X 与“进入主界面”完全同义。"""
        self._enter_full()

    def pump(self):
        if self.runner is None:
            return
        while not self.runner.log_queue.empty():
            level, msg = self.runner.log_queue.get()
            self.append(msg, level)
            self._track_progress(msg)
        if self._launched and not self.runner.is_running():
            self._launched = False
            self.app.enter_full()

    def append(self, msg, level="info", source=None):
        self._log_history.append((msg, level, source))
        T.append_log(self.log, msg, level, source)

    def log_history(self):
        return tuple(self._log_history)

    def _set_status(self, text):
        self.lbl_status.configure(text=text)

    def _track_progress(self, msg):
        """兼容解析既有启动日志；格式变化时只尽力显示档案名，不影响流程。"""
        try:
            text = str(msg)
            progress = _PROGRESS_RE.match(text)
            if progress:
                self._set_status("状态：第 %s/%s 个" % (progress.group(2), progress.group(3)))
                return
            label = _LABEL_RE.match(text)
            if label:
                self._set_status("状态：%s" % label.group(1))
        except Exception:
            pass
