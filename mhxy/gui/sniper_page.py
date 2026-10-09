# -*- coding: utf-8 -*-
"""SniperPage：独立页面，保留既有布局与任务控制。"""
import os
import customtkinter as ctk
from . import theme as T
from ..core import config as cfg_mod
from ..core.runtime import get_runtime
from ..tasks import get_task
from .widgets import Card, Pill, load_thumb, bind_wraplength


class SniperPage(ctk.CTkFrame):
    TASK_NAME = "sniper"
    LOG_SOURCE = "秒装备"

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.fonts = app.fonts
        self.runner = None
        self._cal_dialog = None
        self._thumbs = []  # 防止缩略图被 GC

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        self._build_header()
        self._build_control()
        self._build_body()
        self.refresh()

    # ---- 头部：标题 + 状态 ----
    def _build_header(self):
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=4, pady=(2, 14))
        bar.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(bar, text="秒装备", font=self.fonts["title"], text_color=T.TEXT).grid(
            row=0, column=0, sticky="w")

        right = ctk.CTkFrame(bar, fg_color="transparent")
        right.grid(row=0, column=1, sticky="e")
        self.pill_game = Pill(right, self.fonts)
        self.pill_game.pack(side="left", padx=(0, 8))
        self.pill_mode = Pill(right, self.fonts)
        self.pill_mode.pack(side="left")

    # ---- 控制区：三段式（运行按钮 + 横排工具 / 分隔线 / 模式开关），与其它任务页一致 ----
    def _build_control(self):
        card = Card(self)
        card.grid(row=1, column=0, sticky="ew", padx=4, pady=(0, 14))
        card.grid_columnconfigure(0, weight=1)

        # 第一行：开始按钮（左） + 工具按钮（右，横排）
        top = ctk.CTkFrame(card, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 10))
        top.grid_columnconfigure(1, weight=1)
        self.btn_run = ctk.CTkButton(top, text="▶  开始秒装备", font=self.fonts["btn"],
                                     height=46, width=200, corner_radius=T.RADIUS_SM,
                                     fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER, text_color=T.ON_ACCENT,
                                     command=self._toggle_run)
        self.btn_run.grid(row=0, column=0, sticky="w")
        self.lbl_start_hint = ctk.CTkLabel(
            top, text="打开对应商品页面后点击开始按钮。", font=self.fonts["small"],
            text_color=T.TEXT_DIM, width=1, height=26, anchor="w", justify="left")
        self.lbl_start_hint.grid(row=0, column=1, sticky="ew", padx=(16, 0))
        bind_wraplength(self.lbl_start_hint)
        tools = ctk.CTkFrame(top, fg_color="transparent")
        tools.grid(row=1, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ctk.CTkButton(tools, text="选择窗口", font=self.fonts["body"], height=36, width=104,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=lambda: self.app.open_window_picker(self.refresh)).pack(side="left", padx=(0, 8))
        self.btn_calibrate = ctk.CTkButton(tools, text="标定", font=self.fonts["body"], height=36, width=104,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self._open_calibrate)
        self.btn_calibrate.pack(side="left", padx=(0, 8))
        ctk.CTkButton(tools, text="刷新配置", font=self.fonts["body"], height=36, width=104,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self.refresh).pack(side="left")

        # 分隔线
        ctk.CTkFrame(card, fg_color=T.BORDER, height=1).grid(
            row=1, column=0, sticky="ew", padx=16, pady=(0, 4))

        # 第二行：模式开关
        opts = ctk.CTkFrame(card, fg_color="transparent")
        opts.grid(row=2, column=0, sticky="ew", padx=16, pady=(8, 16))
        box1 = ctk.CTkFrame(opts, fg_color="transparent")
        box1.pack(anchor="w")
        self.switch_mode = ctk.CTkSwitch(box1, text="实战模式", font=self.fonts["body"],
                                         progress_color=T.DANGER, command=self._toggle_mode)
        self.switch_mode.pack(anchor="w")
        self._build_quantity(opts)

    # ---- 主体：模板与各号进度 ----
    def _build_quantity(self, opts):
        row = ctk.CTkFrame(opts, fg_color="transparent")
        row.pack(anchor="w", pady=(12, 0))
        ctk.CTkLabel(row, text="购买数量", font=self.fonts["body"], text_color=T.TEXT).pack(side="left", padx=(0, 12))
        self.entry_count = ctk.CTkEntry(row, width=100, font=self.fonts["body"])
        self.entry_count.pack(side="left")

    # ---- 主体：模板与各号进度 ----
    def _build_body(self):
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=2, column=0, sticky="nsew", padx=4)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(1, weight=1)
        assets = Card(body)
        assets.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        assets.grid_columnconfigure((0, 1), weight=1)
        self.template_slots = {}
        for column, (key, name) in enumerate((
                ("sniper_product", "商品模板"),
                ("sniper_buy", "第一次购买按钮模板"),
                ("sniper_buy_confirm", "第二次购买按钮模板"),
                ("sniper_interference_close", "干扰关闭叉叉模板"),
                ("sniper_success", "购买成功模板"))):
            slot = ctk.CTkFrame(assets, fg_color="transparent")
            slot.grid(row=column // 2, column=column % 2, sticky="ew", padx=16, pady=14)
            slot.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(slot, text=name, font=self.fonts["h2"], text_color=T.TEXT).grid(row=0, column=0, sticky="w")
            image = ctk.CTkLabel(slot, text="未标定", height=64, text_color=T.TEXT_DIM)
            image.grid(row=1, column=0, sticky="ew", pady=8)
            status = ctk.CTkLabel(slot, text="", font=self.fonts["small"], text_color=T.TEXT_DIM)
            status.grid(row=2, column=0, sticky="ew")
            bind_wraplength(status)
            self.template_slots[key] = (image, status)
        self.lbl_regions = ctk.CTkLabel(assets, text="", font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left")
        self.lbl_regions.grid(row=2, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 14))
        bind_wraplength(self.lbl_regions)
        progress = Card(body)
        progress.grid(row=1, column=0, sticky="nsew")
        progress.grid_columnconfigure(0, weight=1)
        progress.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(progress, text="购买进度", font=self.fonts["h2"], text_color=T.TEXT).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 8))
        self.list_frame = ctk.CTkScrollableFrame(progress, fg_color="transparent")
        self.list_frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 12))
        self.list_frame.grid_columnconfigure(0, weight=1)
        T.tune_scroll_speed(self.list_frame)
        self._render_progress(())

    # ------------------------------------------------------------------
    # 数据刷新
    # ------------------------------------------------------------------
    def refresh(self):
        self.app.cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(self.app.cfg, self.TASK_NAME)
        dry = tc.get("dry_run", True)
        if not (self.runner and get_runtime().is_running(self.runner)):
            (self.switch_mode.select if not dry else self.switch_mode.deselect)()
            self.entry_count.delete(0, "end")
            self.entry_count.insert(0, str(tc.get("target_count", 1)))
            self._render_mode_pill(dry)
        self._render_templates(tc)

    def _render_templates(self, tc):
        templates = tc.get("templates", {})
        sig = tuple((key, templates.get(key), self._template_stamp(templates.get(key))) for key in self.template_slots)
        if sig != getattr(self, "_template_sig", None):
            self._template_sig = sig
            self._thumbs.clear()
            for key, (image, status) in self.template_slots.items():
                thumb = load_thumb(templates.get(key), self._thumbs, max_h=64)
                image.configure(image=thumb, text="" if thumb else "未标定")
                status.configure(text="已标定" if thumb else "未标定", text_color=T.SUCCESS if thumb else T.WARN)
        regions = tc.get("regions", {})
        self.lbl_regions.configure(text="  ·  ".join(
            f"{label}：{'已标定' if regions.get(key) else ('整窗' if key != 'buy_button' else '未标定')}"
            for key, label in (("listing", "商品区域"), ("purchase", "购买按钮区域"), ("success", "成功提示区域"))))

    @staticmethod
    def _template_stamp(path):
        if not path:
            return None
        try:
            absolute = path if os.path.isabs(path) else str(cfg_mod.PROJECT_ROOT / path)
            return os.stat(absolute).st_mtime_ns
        except OSError:
            return None

    def _render_progress(self, snapshot):
        if snapshot == getattr(self, "_progress_sig", None):
            return
        self._progress_sig = snapshot
        for widget in self.list_frame.winfo_children():
            widget.destroy()
        if not snapshot:
            ctk.CTkLabel(self.list_frame, text="待启动", font=self.fonts["body"], text_color=T.TEXT_DIM).grid(row=0, column=0, sticky="w", padx=12, pady=16)
            return
        for i, (label, count, target, state) in enumerate(snapshot):
            row = ctk.CTkFrame(self.list_frame, fg_color=T.SURFACE_2, corner_radius=T.RADIUS_SM)
            row.grid(row=i, column=0, sticky="ew", padx=4, pady=4)
            row.grid_columnconfigure(0, weight=1)
            name = ctk.CTkLabel(row, text=str(label), font=self.fonts["body_b"], text_color=T.TEXT, anchor="w")
            name.grid(row=0, column=0, sticky="ew", padx=12, pady=(8, 0))
            bind_wraplength(name)
            ctk.CTkLabel(row, text=f"{count} / {target}", font=self.fonts["body_b"], text_color=T.TEXT).grid(row=0, column=1, padx=12, pady=(8, 0))
            status = ctk.CTkLabel(row, text=str(state), font=self.fonts["small"], text_color=T.TEXT_DIM, anchor="w")
            status.grid(row=1, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 8))
            bind_wraplength(status)

    def _set_editable(self, editable):
        state = "normal" if editable else "disabled"
        for widget in (self.entry_count, self.switch_mode, self.btn_calibrate):
            widget.configure(state=state)

    # ------------------------------------------------------------------
    # 运行控制
    # ------------------------------------------------------------------
    def _toggle_run(self):
        if self.runner and get_runtime().is_running(self.runner):
            get_runtime().stop(self.runner)
            self._log_line("正在停止…", "warn")
            self.btn_run.configure(text="停止中…", state="disabled")
            return
        # 启动
        if self._cal_dialog is not None:
            try:
                if self._cal_dialog.winfo_exists():
                    self._log_line("无法启动：请先关闭标定窗口", "error")
                    return
            except Exception:
                pass
        raw_count = self.entry_count.get().strip()
        if not raw_count.isascii() or not raw_count.isdecimal() or int(raw_count) <= 0:
            self._log_line("无法启动：购买数量必须是正整数", "error")
            self.entry_count.focus_set()
            return
        self.app.cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(self.app.cfg, self.TASK_NAME)
        tc["target_count"] = int(raw_count)
        tc["dry_run"] = not bool(self.switch_mode.get())
        cfg_mod.set_task_config(self.app.cfg, self.TASK_NAME, tc)
        try:
            cfg_mod.save_config(self.app.cfg)
        except Exception as e:
            self._log_line(f"无法启动：配置保存失败：{e}", "error")
            return
        task_cls = get_task(self.TASK_NAME)
        self.runner = get_runtime().create_runner(task_cls(), self.app.cfg)
        self._set_editable(False)
        ok, problems = get_runtime().start(self.runner)
        if not ok:
            for p in problems:
                self._log_line("无法启动：" + p, "error")
            self.runner = None
            self._set_editable(True)
            return
        self._render_progress(getattr(self.runner.task, "progress", ()))
        self.btn_run.configure(text="■  停止", fg_color=T.DANGER, hover_color=T.DANGER_HOVER, state="normal")
        self.app.on_task_started("秒装备", self._toggle_run, self.runner)

    def _on_runner_finished(self):
        self._set_editable(True)
        self.btn_run.configure(text="▶  开始秒装备", fg_color=T.ACCENT,
                               hover_color=T.ACCENT_HOVER, state="normal")

    def _toggle_mode(self):
        if self.runner and get_runtime().is_running(self.runner):
            dry = cfg_mod.task_config(self.runner.cfg, self.TASK_NAME).get("dry_run", True)
            (self.switch_mode.deselect if dry else self.switch_mode.select)()
            return
        self.app.cfg = cfg_mod.load_config()
        live = bool(self.switch_mode.get())  # 1=实战
        tc = cfg_mod.task_config(self.app.cfg, self.TASK_NAME)
        tc["dry_run"] = not live
        cfg_mod.set_task_config(self.app.cfg, self.TASK_NAME, tc)
        cfg_mod.save_config(self.app.cfg)
        self._render_mode_pill(not live)
        if live:
            self._log_line("⚠ 实战模式：命中会真买", "warn")
        else:
            self._log_line("演练模式（只识别不下单）", "info")

    def _render_mode_pill(self, dry):
        if dry:
            self.pill_mode.configure(text="演练", fg_color=T.PILL_OK_BG, text_color=T.SUCCESS)
        else:
            self.pill_mode.configure(text="实战", fg_color=T.PILL_DANGER_BG, text_color=T.DANGER)

    def _open_calibrate(self):
        if self.runner and get_runtime().is_running(self.runner):
            return
        # 全程在 GUI 内完成，不再开黑窗子进程
        if getattr(self, "_cal_dialog", None) is not None:
            try:
                if self._cal_dialog.winfo_exists():
                    self._cal_dialog.lift()
                    self._cal_dialog.focus_force()
                    return
            except Exception:
                pass
        from .calibrate_dialog import CalibrateDialog

        def _after():
            self._cal_dialog = None
            self.refresh()
            self._log_line("标定完成，配置已更新。", "info")

        try:
            self._cal_dialog = CalibrateDialog(self.app, task_name=self.TASK_NAME, on_done=_after)
        except Exception as e:
            self._cal_dialog = None
            self._log_line(f"打开标定向导失败：{e}", "error")

    # ------------------------------------------------------------------
    # 日志与状态（由 App 的定时器驱动）
    # ------------------------------------------------------------------
    def pump(self):
        """被 App._tick 周期调用：抽干日志队列、检测运行结束、读取进度快照。"""
        if self.runner:
            self._render_progress(getattr(self.runner.task, "progress", ()))
            if not get_runtime().is_running(self.runner) and self.btn_run.cget("text") != "▶  开始秒装备":
                self._on_runner_finished()

    def update_game_pill(self, connected, summary=""):
        if connected:
            self.pill_game.configure(text="● " + (summary or "目标窗口已连接"),
                                     fg_color=T.PILL_OK_BG, text_color=T.SUCCESS)
        else:
            self.pill_game.configure(text="○ 未检测到目标窗口", fg_color=T.SURFACE_2, text_color=T.TEXT_DIM)

    def _log_line(self, msg, level="info"):
        # 日志统一汇到 App 右侧全局面板，按本页 LOG_SOURCE 打来源标签。
        self.app.log_line(msg, level, getattr(self, "LOG_SOURCE", None))

    def _clear_log(self):
        self.app.clear_log()
