# -*- coding: utf-8 -*-
"""启动登录页面。账号档案与通用流程标定分开管理。"""

import os
import time
import uuid
from tkinter import filedialog

import customtkinter as ctk

from . import theme as T
from .calibrate_dialog import CalibrateDialog
from ..core import accounts
from ..core import config as cfg_mod
from ..core import launcher
from ..core import window as win_mod
from ..core.runner import TaskRunner
from ..tasks import get_task


def _card(master, **kw):
    opts = dict(fg_color=T.SURFACE, corner_radius=T.RADIUS, border_width=1, border_color=T.BORDER)
    opts.update(kw)
    return ctk.CTkFrame(master, **opts)


class LaunchLoginPage(ctk.CTkFrame):
    TASK_NAME = "launch_login"
    LOG_SOURCE = "启动登录"
    _ROLE_REFRESH_SEC = 5.0      # 名册重读节流（进程枚举不便宜，别每帧做）

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.fonts = app.fonts
        self.runner = None
        self._cal_dialog = None
        self._profile_role_values = {}
        self._roles_checked_at = 0.0
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)
        self._build_header()
        self._build_controls()
        self._build_profiles()
        self.refresh()

    def _build_header(self):
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=4, pady=(2, 14))
        bar.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(bar, text="启动登录", font=self.fonts["title"], text_color=T.TEXT).grid(
            row=0, column=0, sticky="w")
        self.pill_game = ctk.CTkLabel(bar, text="", font=self.fonts["small"], corner_radius=T.RADIUS_PILL,
                                      fg_color=T.SURFACE_2, text_color=T.TEXT_DIM, padx=12, pady=4)
        self.pill_game.grid(row=0, column=1, sticky="e")

    def _build_controls(self):
        card = _card(self)
        card.grid(row=1, column=0, sticky="ew", padx=4, pady=(0, 14))
        card.grid_columnconfigure(1, weight=1)

        self.btn_run = ctk.CTkButton(card, text="▶  开始启动登录", font=self.fonts["btn"], height=44,
                                     width=190, corner_radius=T.RADIUS_SM, fg_color=T.ACCENT,
                                     hover_color=T.ACCENT_HOVER, text_color=T.ON_ACCENT,
                                     command=self._toggle_run)
        self.btn_run.grid(row=0, column=0, sticky="w", padx=(16, 12), pady=(16, 10))
        tools = ctk.CTkFrame(card, fg_color="transparent")
        tools.grid(row=0, column=2, sticky="e", padx=(0, 16), pady=(16, 10))
        ctk.CTkButton(tools, text="流程标定", font=self.fonts["body"], height=34, width=96,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER,
                      text_color=T.TEXT, border_width=1, border_color=T.BORDER,
                      command=self._open_calibrate).pack(side="left")

        ctk.CTkFrame(card, fg_color=T.BORDER, height=1).grid(
            row=1, column=0, columnspan=3, sticky="ew", padx=16, pady=(0, 10))
        ctk.CTkLabel(card, text="启动器", font=self.fonts["body_b"], text_color=T.TEXT).grid(
            row=2, column=0, sticky="w", padx=(16, 12), pady=(0, 12))
        self.var_path = ctk.StringVar()
        self.entry_path = ctk.CTkEntry(card, textvariable=self.var_path, font=self.fonts["small"], height=34,
                                       fg_color=T.SURFACE_2, border_color=T.BORDER, text_color=T.TEXT)
        self.entry_path.grid(row=2, column=1, sticky="ew", pady=(0, 12))
        self.entry_path.bind("<FocusOut>", lambda _e: self._save_path())
        source = ctk.CTkFrame(card, fg_color="transparent")
        source.grid(row=2, column=2, sticky="e", padx=(12, 16), pady=(0, 12))
        ctk.CTkButton(source, text="浏览…", font=self.fonts["small"], height=34, width=68,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER,
                      text_color=T.TEXT, border_width=1, border_color=T.BORDER,
                      command=self._browse).pack(side="left", padx=(0, 6))
        ctk.CTkButton(source, text="自动检测", font=self.fonts["small"], height=34, width=82,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER,
                      text_color=T.TEXT, border_width=1, border_color=T.BORDER,
                      command=self._detect).pack(side="left")

        self.switch_live = ctk.CTkSwitch(card, text="实战模式", font=self.fonts["body"],
                                         progress_color=T.DANGER, command=self._toggle_mode)
        self.switch_live.grid(row=3, column=0, columnspan=3, sticky="w", padx=16, pady=(0, 16))

    def _build_profiles(self):
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=2, column=0, sticky="ew", padx=4, pady=(0, 8))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text="启动队列", font=self.fonts["h2"], text_color=T.TEXT).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(head, text="＋ 新增档案", font=self.fonts["small"], height=32, width=100,
                      corner_radius=T.RADIUS_SM, fg_color=T.SUCCESS, hover_color=T.SUCCESS_HOVER,
                      text_color=T.ON_ACCENT, command=self._add_profile).grid(row=0, column=1, sticky="e")
        self.profile_list = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.profile_list.grid(row=3, column=0, sticky="nsew", padx=4)
        self.profile_list.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)
        T.tune_scroll_speed(self.profile_list)

    def refresh(self):
        self.app.cfg = cfg_mod.load_config()
        account_cfg = self.app.cfg.get("account_launch") or {}
        self.var_path.set(account_cfg.get("launcher_path") or "")
        dry = cfg_mod.task_config(self.app.cfg, self.TASK_NAME).get("dry_run", True)
        (self.switch_live.select if not dry else self.switch_live.deselect)()
        self._render_profiles(account_cfg.get("profiles") or [])

    def _roles(self):
        try:
            wins = win_mod.locate_all(self.app.cfg.get("window_title", "梦幻西游"),
                                      self.app.cfg.get("window_offset", [0, 0]))
            records = accounts.roster(wins)
        except Exception:
            records = {}
        out = {}
        for rid, rec in records.items():
            display = accounts.display_name(rec) or rec.get("name") or str(rid)
            value = "%s · %s" % (display, rid)
            out[value] = (str(rid), rec.get("name") or "")
        return out

    def _maybe_refresh_roles(self, force=False):
        """名册可能【后到】（常见：先开脚本、后登录游戏），故定期重读；只有真的变了才重渲染。

        不重读就会一直卡在「未读取到角色名册」，即使游戏已经跑起来、名册文件已经写好。
        """
        now = time.time()
        if not force and now - self._roles_checked_at < self._ROLE_REFRESH_SEC:
            return
        self._roles_checked_at = now
        try:
            roles = self._roles()
        except Exception:
            return
        if roles != self._profile_role_values:
            self.refresh()

    def _refresh_targets_labels(self):
        """App 在「游戏连接状态变化」时回调本方法（见 app._apply_game_state）。"""
        self._maybe_refresh_roles(force=True)

    def _render_profiles(self, profiles):
        for child in self.profile_list.winfo_children():
            child.destroy()
        self._profile_role_values = self._roles()
        if not profiles:
            ctk.CTkLabel(self.profile_list, text="暂无启动档案", font=self.fonts["body"],
                         text_color=T.TEXT_DIM).grid(row=0, column=0, sticky="ew", padx=12, pady=24)
            return
        has_roster = bool(self._profile_role_values)
        values = list(self._profile_role_values) or ["未读取到角色名册"]
        for index, profile in enumerate(profiles):
            row = _card(self.profile_list, fg_color=T.SURFACE_2, corner_radius=T.RADIUS_SM)
            row.grid(row=index, column=0, sticky="ew", padx=4, pady=5)
            row.grid_columnconfigure(2, weight=1)
            enabled = ctk.BooleanVar(value=profile.get("enabled", True))
            ctk.CTkCheckBox(row, text="", variable=enabled, width=26,
                            command=lambda p=profile, v=enabled: self._set_enabled(p["id"], v.get())).grid(
                                row=0, column=0, rowspan=2, padx=(12, 6), pady=10)
            ctk.CTkLabel(row, text=profile.get("label") or "未命名档案", font=self.fonts["body_b"],
                         text_color=T.TEXT).grid(row=0, column=1, sticky="w", padx=(0, 10), pady=(10, 2))
            role_var = ctk.StringVar(value=self._value_for_profile(profile, values[0]))
            menu = ctk.CTkOptionMenu(row, values=values, variable=role_var, height=30, width=240,
                                     font=self.fonts["small"], fg_color=T.BTN, button_color=T.SURFACE,
                                     button_hover_color=T.BTN_HOVER, text_color=T.TEXT,
                                     command=lambda value, pid=profile["id"]: self._set_role(pid, value))
            menu.grid(row=0, column=2, sticky="ew", padx=(0, 10), pady=(10, 2))
            role_name = profile.get("expected_role_name") or "未选择角色"
            ctk.CTkLabel(row, text=role_name, font=self.fonts["small"], text_color=T.TEXT_DIM).grid(
                row=1, column=1, columnspan=2, sticky="w", padx=(0, 10), pady=(0, 10))
            buttons = ctk.CTkFrame(row, fg_color="transparent")
            buttons.grid(row=0, column=3, rowspan=2, sticky="e", padx=(4, 10))
            if not has_roster:
                ctk.CTkButton(buttons, text="手填", font=self.fonts["small"], height=30, width=48,
                              corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER,
                              text_color=T.TEXT, border_width=1, border_color=T.BORDER,
                              command=lambda p=profile: self._prompt_role_name(p)).pack(side="left", padx=2)
            ctk.CTkButton(buttons, text="↑", font=self.fonts["body_b"], height=30, width=30,
                          corner_radius=T.RADIUS_SM, fg_color="transparent", hover_color=T.BTN_HOVER,
                          text_color=T.TEXT, command=lambda i=index: self._move(i, -1)).pack(side="left", padx=2)
            ctk.CTkButton(buttons, text="↓", font=self.fonts["body_b"], height=30, width=30,
                          corner_radius=T.RADIUS_SM, fg_color="transparent", hover_color=T.BTN_HOVER,
                          text_color=T.TEXT, command=lambda i=index: self._move(i, 1)).pack(side="left", padx=2)
            ctk.CTkButton(buttons, text="删除", font=self.fonts["small"], height=30, width=48,
                          corner_radius=T.RADIUS_SM, fg_color="transparent", hover_color=T.DANGER,
                          text_color=T.TEXT, border_width=1, border_color=T.BORDER,
                          command=lambda pid=profile["id"]: self._delete_profile(pid)).pack(side="left", padx=2)

    def _value_for_profile(self, profile, fallback):
        role_id = str(profile.get("expected_role_id") or "")
        for value, pair in self._profile_role_values.items():
            if pair[0] == role_id:
                return value
        return fallback

    def _account_cfg(self):
        cfg = cfg_mod.load_config()
        cfg.setdefault("account_launch", {})
        cfg["account_launch"].setdefault("profiles", [])
        return cfg

    def _save_path(self):
        cfg = self._account_cfg()
        cfg["account_launch"]["launcher_path"] = self.var_path.get().strip()
        cfg_mod.save_config(cfg)
        self.app.cfg = cfg

    def _browse(self):
        initial = os.path.dirname(self.var_path.get()) if self.var_path.get() else None
        path = filedialog.askopenfilename(parent=self.app, initialdir=initial,
                                          filetypes=[("启动程序", "*.exe *.lnk"), ("所有文件", "*.*")])
        if path:
            self.var_path.set(path)
            self._save_path()
            self._log_line("已选择启动器：%s" % path)

    def _detect(self):
        candidates = launcher.discover_candidates()
        if not candidates:
            self._log_line("未发现候选启动器，请使用“浏览...”选择。", "warn")
            return
        self.var_path.set(candidates[0])
        self._save_path()
        self._log_line("自动发现候选：%s；请确认路径正确。" % candidates[0], "warn")

    def _toggle_mode(self):
        cfg = self._account_cfg()
        tc = cfg_mod.task_config(cfg, self.TASK_NAME)
        tc["dry_run"] = not bool(self.switch_live.get())
        cfg_mod.set_task_config(cfg, self.TASK_NAME, tc)
        cfg_mod.save_config(cfg)
        self.app.cfg = cfg

    def _add_profile(self):
        dialog = ctk.CTkInputDialog(text="档案名称：", title="新增启动档案")
        label = (dialog.get_input() or "").strip()
        if not label:
            return
        cfg = self._account_cfg()
        cfg["account_launch"]["profiles"].append({"id": uuid.uuid4().hex, "label": label,
                                                      "enabled": True, "expected_role_id": "",
                                                      "expected_role_name": ""})
        cfg_mod.save_config(cfg)
        self.refresh()

    def _set_enabled(self, profile_id, enabled):
        self._update_profile(profile_id, enabled=bool(enabled))

    def _prompt_role_name(self, profile):
        dialog = ctk.CTkInputDialog(text="目标角色名：", title="手填角色名")
        name = (dialog.get_input() or "").strip()
        if name:
            self._update_profile(profile["id"], expected_role_id="", expected_role_name=name)

    def _set_role(self, profile_id, value):
        pair = self._profile_role_values.get(value)
        if pair is None:
            return
        self._update_profile(profile_id, expected_role_id=pair[0], expected_role_name=pair[1])

    def _update_profile(self, profile_id, **updates):
        cfg = self._account_cfg()
        for profile in cfg["account_launch"]["profiles"]:
            if profile.get("id") == profile_id:
                profile.update(updates)
                break
        cfg_mod.save_config(cfg)
        self.refresh()

    def _delete_profile(self, profile_id):
        cfg = self._account_cfg()
        cfg["account_launch"]["profiles"] = [p for p in cfg["account_launch"]["profiles"]
                                                 if p.get("id") != profile_id]
        cfg_mod.save_config(cfg)
        self.refresh()

    def _move(self, index, delta):
        cfg = self._account_cfg()
        profiles = cfg["account_launch"]["profiles"]
        target = index + delta
        if not (0 <= index < len(profiles) and 0 <= target < len(profiles)):
            return
        profiles[index], profiles[target] = profiles[target], profiles[index]
        cfg_mod.save_config(cfg)
        self.refresh()

    def _open_calibrate(self):
        if self._cal_dialog is not None:
            try:
                if self._cal_dialog.winfo_exists():
                    self._cal_dialog.lift()
                    return
            except Exception:
                pass
        self._cal_dialog = CalibrateDialog(self.app, task_name=self.TASK_NAME,
                                            on_done=lambda: (setattr(self, "_cal_dialog", None), self.refresh()))

    def _toggle_run(self):
        if self.runner and self.runner.is_running():
            self.runner.stop()
            self.btn_run.configure(text="停止中…", state="disabled")
            return
        self.app.cfg = cfg_mod.load_config()
        task_cls = get_task(self.TASK_NAME)
        self.runner = TaskRunner(task_cls(), self.app.cfg)
        ok, problems = self.runner.start()
        if not ok:
            for problem in problems:
                self._log_line("无法启动：" + problem, "error")
            self.runner = None
            return
        self.btn_run.configure(text="■  停止", fg_color=T.DANGER, hover_color=T.DANGER_HOVER)
        self.app.on_task_started("启动登录", self._toggle_run, self.runner)

    def pump(self):
        if self.runner:
            while not self.runner.log_queue.empty():
                level, msg = self.runner.log_queue.get()
                self._log_line(msg, level)
            if not self.runner.is_running() and self.btn_run.cget("text") != "▶  开始启动登录":
                self.btn_run.configure(text="▶  开始启动登录", fg_color=T.ACCENT,
                                       hover_color=T.ACCENT_HOVER, state="normal")
        self._maybe_refresh_roles()     # 名册后到（先开脚本、后登录游戏）也能自动出现

    def update_game_pill(self, connected, summary=""):
        if connected:
            self.pill_game.configure(text="● " + (summary or "游戏窗口已连接"),
                                     fg_color=T.PILL_OK_BG, text_color=T.SUCCESS)
        else:
            self.pill_game.configure(text="○ 未检测到游戏窗口", fg_color=T.SURFACE_2, text_color=T.TEXT_DIM)

    def _log_line(self, msg, level="info"):
        self.app.log_line(msg, level, self.LOG_SOURCE)
