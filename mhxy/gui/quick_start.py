# -*- coding: utf-8 -*-
"""紧凑启动形态的一键启动面板。"""

import os
import re
import time
import uuid
from tkinter import filedialog

import customtkinter as ctk

from . import theme as T
from ..core import accounts
from ..core import config as cfg_mod
from ..core import launcher
from ..core import window as win_mod
from ..core.runner import TaskRunner
from ..tasks.launch_login import LaunchLoginTask


_PROGRESS_RE = re.compile(r"^\[([^\]]+)\]\s*开始处理（(\d+)/(\d+)）。")
_LABEL_RE = re.compile(r"^\[([^\]]+)\]")


class QuickStartPanel(ctk.CTkFrame):
    """零游戏窗口时显示的档案选择与一键启动面板。"""

    LOG_SOURCE = "启动登录"
    _ROLE_REFRESH_SEC = 5.0      # 还没档案时，每隔这么久重读一次名册（先开脚本、后开游戏的场景）

    def __init__(self, master, app):
        super().__init__(master, fg_color=T.BG)
        self.app = app
        self.fonts = app.fonts
        self.runner = None
        self._launched = False
        self._profile_count = 0
        self._log_history = []
        self._cal_dialog = None
        self._dodged_at = 0.0
        self._roles_checked_at = 0.0
        self._roster_ids = set()

        self.grid_columnconfigure(0, weight=1)
        # 上半区保持紧凑，日志容器吃掉其后的全部空间。
        self.grid_rowconfigure(5, weight=1)
        self._build()
        self.refresh()

    def _build(self):
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=T.SP_4, pady=(T.SP_4, T.SP_1))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text="梦幻小助手", font=self.fonts["title"],
                     text_color=T.TEXT).grid(row=0, column=0, sticky="w")
        self.lbl_count = ctk.CTkLabel(head, text="", font=self.fonts["small"], text_color=T.TEXT_DIM)
        self.lbl_count.grid(row=0, column=1, sticky="e")

        ctk.CTkFrame(self, height=1, fg_color=T.BORDER).grid(
            row=1, column=0, sticky="ew", padx=T.SP_5, pady=(0, T.SP_2))

        # CTkScrollableFrame 会受内容请求尺寸影响，直接设 height 不能可靠限高。
        # 由禁止尺寸传播的外层承载，确保日志区域能取得窗口下半区。
        self.profile_host = ctk.CTkFrame(self, height=72, fg_color="transparent")
        self.profile_host.grid(row=2, column=0, sticky="ew", padx=T.SP_4, pady=(0, T.SP_2))
        self.profile_host.grid_propagate(False)
        self.profile_host.grid_columnconfigure(0, weight=1)
        self.profile_host.grid_rowconfigure(0, weight=1)
        self.profile_list = ctk.CTkScrollableFrame(
            self.profile_host, fg_color="transparent", corner_radius=0,
        )
        self.profile_list.grid(row=0, column=0, sticky="nsew")
        self.profile_list.grid_columnconfigure(0, weight=1)
        self.profile_list.grid_columnconfigure(1, weight=1)
        T.tune_scroll_speed(self.profile_list)

        controls = ctk.CTkFrame(self, fg_color="transparent")
        controls.grid(row=3, column=0, sticky="ew", padx=T.SP_5, pady=(T.SP_1, T.SP_2))
        controls.grid_columnconfigure(0, weight=1)
        self.btn_run = ctk.CTkButton(
            controls, text="⚡  一键启动", font=self.fonts["btn"], height=40,
            corner_radius=T.RADIUS_SM, fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER,
            text_color=T.ON_ACCENT, command=self._toggle_run)
        self.btn_run.grid(row=0, column=0, sticky="ew")
        # 零窗口时小窗是【唯一】入口，而流程标定原来只在完整界面的「启动登录」页——没有游戏窗口
        # 就进不去，标定按钮失效时用户彻底没救。故这里给一键启动配一个同源的标定入口。
        self.btn_calibrate = ctk.CTkButton(
            controls, text="标定", font=self.fonts["body"], height=40, width=84,
            corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
            border_width=1, border_color=T.BORDER, command=self._open_calibrate)
        self.btn_calibrate.grid(row=0, column=1, sticky="e", padx=(T.SP_2, 0))
        # 首次使用者没有任何线索可定位游戏目录，读不到角色名册 → 小窗（零窗口时唯一入口）
        # 必须能自己选一次启动器，并把路径记住（见 _browse_launcher / accounts.set_launcher_path）。
        self.btn_launcher = ctk.CTkButton(
            controls, text="启动器…", font=self.fonts["body"], height=40, width=84,
            corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
            border_width=1, border_color=T.BORDER, command=self._browse_launcher)
        self.btn_launcher.grid(row=0, column=2, sticky="e", padx=(T.SP_2, 0))

        ctk.CTkFrame(self, height=1, fg_color=T.BORDER).grid(
            row=4, column=0, sticky="ew", padx=T.SP_5, pady=(T.SP_1, T.SP_1))

        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.grid(row=5, column=0, sticky="nsew", padx=T.SP_5, pady=(0, T.SP_3))
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
        """从配置真源重建档案行，并补齐本机名册里的角色。"""
        cfg = cfg_mod.load_config()
        profiles = self._sync_roster_profiles(cfg)
        self.app.cfg = cfg
        self._profile_count = len(profiles)
        self.lbl_count.configure(text="（%d 个）" % len(profiles) if profiles else "")
        for child in self.profile_list.winfo_children():
            child.destroy()
        if not profiles:
            self._set_status("状态：先选择游戏启动器")
        else:
            for index, profile in enumerate(profiles):
                self._render_profile(index, profile)
            if not self._launched:
                self._set_status("状态：待启动")
        self._sync_run_button()

    @staticmethod
    def _merge_roster_profiles(profiles, roster):
        """将本机名册中尚未建档的角色追加为默认未勾选的档案。"""
        merged = list(profiles)
        known_ids = {str(profile.get("expected_role_id") or "") for profile in merged}
        added = False
        for role_id, record in (roster or {}).items():
            role_id = str(role_id or "")
            if not role_id or role_id in known_ids:
                continue
            name = (record.get("name") or "").strip()
            merged.append({
                "id": uuid.uuid4().hex,
                "label": name or role_id,
                "enabled": False,
                "expected_role_id": role_id,
                "expected_role_name": name,
            })
            known_ids.add(role_id)
            added = True
        return merged, added

    def _sync_roster_profiles(self, cfg):
        """把可读取的本机角色名册同步进启动档案；读到名册就顺手记住游戏目录。"""
        account_cfg = cfg.setdefault("account_launch", {})
        profiles = account_cfg.setdefault("profiles", [])
        try:
            roster = accounts.roster()
        except Exception:
            roster = {}
        self._roster_ids = {str(rid) for rid in (roster or {})}
        merged, added = self._merge_roster_profiles(profiles, roster)
        if added:
            account_cfg["profiles"] = merged
        remembered = bool(roster) and self._remember_game_dir(cfg)
        if added or remembered:
            cfg_mod.save_config(cfg)
        return merged

    def _remember_game_dir(self, cfg):
        """首次成功读到名册就把游戏目录记进 config.game_dir —— 以后游戏和启动器都不开也能读名册。

        与完整界面「启动登录」页同源（gui/launch_login_page._remember_game_dir）：原先只有那一页会记，
        而零窗口时小窗是唯一入口，于是首次使用者永远停在「看不到角色名」。返回 True=本次写入了。"""
        if (cfg.get("game_dir") or "").strip():
            return False
        found = accounts.data_dir()
        if not found:
            return False
        base = os.path.dirname(found) if os.path.basename(found).lower() == "localdata" else found
        cfg["game_dir"] = base
        accounts.set_game_dir(base)
        self.app.cfg = cfg
        return True

    def _browse_launcher(self):
        """选择游戏启动器：记住路径后即可在游戏没开时读名册拿角色名（首次使用者的唯一线索）。"""
        try:
            cfg = cfg_mod.load_config()
            current = ((cfg.get("account_launch") or {}).get("launcher_path") or "").strip()
            path = filedialog.askopenfilename(
                parent=self.app, initialdir=(os.path.dirname(current) or None),
                filetypes=[("启动程序", "*.exe *.lnk"), ("所有文件", "*.*")])
        except Exception as exc:
            self.append("打开文件选择失败：%s" % exc, "error")
            return
        if not path:
            return
        cfg.setdefault("account_launch", {})["launcher_path"] = path
        cfg_mod.save_config(cfg)
        self.app.cfg = cfg
        accounts.set_launcher_path(path)
        self.refresh()
        if self._profile_count:
            self.append("已记住启动器，读到 %d 个角色名。" % self._profile_count, "hit")
        else:
            self.append("已记住启动器，但没读到角色名册；请选游戏安装目录里的启动器。", "warn")
            self._set_status("状态：未读到角色名册")

    def _maybe_refresh_roles(self):
        """还没档案时定期重读名册：常见「先开脚本、后开游戏/启动器」，名册是后到的。"""
        if self._profile_count:
            return
        now = time.time()
        if now - self._roles_checked_at < self._ROLE_REFRESH_SEC:
            return
        self._roles_checked_at = now
        try:
            roster = accounts.roster()
        except Exception:
            return
        if {str(rid) for rid in (roster or {})} != self._roster_ids:
            self.refresh()

    def _render_profile(self, index, profile):
        """以双列紧凑复选项呈现档案，避免少量档案也占满启动窗。"""
        enabled = ctk.BooleanVar(value=profile.get("enabled", True))
        item = ctk.CTkCheckBox(
            self.profile_list,
            text=profile.get("label") or "未命名档案",
            variable=enabled,
            width=184,
            height=30,
            font=self.fonts["body"],
            text_color=T.TEXT,
            fg_color=T.ACCENT,
            hover_color=T.ACCENT_HOVER,
            command=lambda i=index, v=enabled: self._set_enabled(i, v.get()),
        )
        item.grid(row=index // 2, column=index % 2, sticky="ew", padx=T.SP_2, pady=T.SP_1)

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
            self._stop_and_wait_for_window()
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
        self.app.on_quick_start_started(self.runner)
        self._sync_calibrate_button()
        self._move_away_from_launcher()

    def _sync_calibrate_button(self):
        """运行中禁掉标定与选启动器：两者都要抢前台截图/改配置，和正在跑的启动流程会互相干扰。"""
        try:
            state = "disabled" if self._launched else "normal"
            self.btn_calibrate.configure(state=state)
            self.btn_launcher.configure(state=state)
        except Exception:
            pass

    def _open_calibrate(self):
        """一键启动的流程标定（与「启动登录」页的「流程标定」同源，写同一份配置）。

        标定「开始游戏」这类启动器按钮时，先把启动器摆到桌面上（没有就在下面提示），再点本按钮框选。
        """
        dialog = getattr(self, "_cal_dialog", None)
        if dialog is not None:
            try:
                if dialog.winfo_exists():
                    dialog.lift()
                    dialog.focus_force()
                    return
            except Exception:
                pass
        from .calibrate_dialog import CalibrateDialog

        def _done():
            self._cal_dialog = None
            self.refresh()
            self.append("标定完成，配置已更新。", "info")

        try:
            self._cal_dialog = CalibrateDialog(self.app, task_name=LaunchLoginTask.name, on_done=_done)
        except Exception as e:
            self._cal_dialog = None
            self.append("打开标定向导失败：%s" % e, "error")

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

    def _has_game_window(self):
        cfg = self.app.cfg
        try:
            wins = win_mod.locate_all(cfg.get("window_title", "梦幻西游"),
                                      cfg.get("window_offset", [0, 0]),
                                      include_minimized=True)
        except Exception:
            return False
        return bool(wins)

    def _enter_full_if_game_ready(self):
        if self._has_game_window():
            self.app.enter_full()
            self.app.after_idle(self._center_full_window)
            return True
        self._set_status("状态：未检测到游戏窗口")
        self._sync_run_button()
        return False

    def _center_full_window(self):
        """一键启动完成后把已展开的主界面居中到当前屏幕工作区。"""
        try:
            self.app.update_idletasks()
            width = max(1, self.app.winfo_width())
            height = max(1, self.app.winfo_height())
            work = win_mod.monitor_work_area(self.app.winfo_id())
            if work is None:
                return
            left, top, right, bottom = work
            x = left + max(0, (right - left - width) // 2)
            y = top + max(0, (bottom - top - height) // 2)
            self.app.geometry("+%d+%d" % (x, y))
        except Exception:
            pass

    def _stop_and_wait_for_window(self):
        if self.runner is not None and self.runner.is_running():
            self.runner.stop()
        self._launched = False
        self._enter_full_if_game_ready()

    def on_close(self):
        """零窗口小窗关闭时停止任务并退出程序，不绕过启动门槛。"""
        self.app.stop_all_tasks()
        self.app.destroy()

    def pump(self):
        self._maybe_refresh_roles()
        if self.runner is None:
            return
        while not self.runner.log_queue.empty():
            level, msg = self.runner.log_queue.get()
            self.append(msg, level)
            self._track_progress(msg)
        if self._launched:
            now = time.time()
            if now - getattr(self, "_dodged_at", 0.0) >= 0.8:
                self._dodged_at = now
                self._keep_behind_target()
        if self._launched and not self.runner.is_running():
            self._launched = False
            self._sync_calibrate_button()
            self._enter_full_if_game_ready()

    def refresh_state(self, state):
        """按 App 统一状态刷新紧凑形态；进度日志仍由 _track_progress 细化展示。"""
        if state.task_state == "stopping":
            self.btn_run.configure(text="停止中…", state="disabled")
        elif state.task_state == "failed" and not self._launched:
            self._set_status("状态：启动失败")
            self._sync_run_button()
        elif state.task_state == "stopped" and not self._launched:
            self._set_status("状态：已停止")
            self._sync_run_button()

    # ---- 自家小窗绝不许压住「脚本要看的东西」（启动器 / 游戏号）----
    def _target_hwnd(self):
        """当前该让路的窗口句柄：优先官方启动器，其次第一个可见游戏窗口；都没有返回 0。"""
        try:
            for w in win_mod.gw.getAllWindows():
                hwnd = int(w._hWnd)
                if not hwnd or w.isMinimized or win_mod.is_own_window(hwnd):
                    continue
                image = win_mod.proc_image_path(hwnd)
                if image and image.rsplit("\\", 1)[-1].lower() == launcher.LAUNCHER_EXE.lower():
                    return hwnd
        except Exception:
            pass
        try:
            wins = win_mod.locate_all(self.app.cfg.get("window_title", "梦幻西游"),
                                      self.app.cfg.get("window_offset", [0, 0]))
        except Exception:
            return 0
        return wins[0].hwnd() if wins else 0

    def _keep_behind_target(self):
        """任务运行期间，把小窗压到「要看的目标窗口」下面（只改 z 序，不动位置/尺寸/焦点）。

        压在它上面 = 那一片像素真的是我们的窗口，脚本再怎么标定都认不出按钮（用户实测：
        把窗口挪开一点就正常）。这里每 0.8 秒比一次 z 序，只有真压在上面时才压下去。
        """
        try:
            target = self._target_hwnd()
            if not target:
                return
            mine = win_mod.toplevel_hwnd(self.app.winfo_id())
            if mine and win_mod.is_window_above(mine, target):
                win_mod.place_below(mine, target)
        except Exception:
            pass

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
