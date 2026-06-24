# -*- coding: utf-8 -*-
"""
主界面。深色简约风，左侧导航 + 右侧分页。
每个任务对应一个页面：以后加新任务，写个 *Page 并在 PAGES 注册即可。
"""

import os
import ctypes
import datetime
import threading

import customtkinter as ctk

from . import theme as T
from ..core import config as cfg_mod
from ..core import window as win_mod
from ..core.runner import TaskRunner
from ..tasks import get_task
from ..tasks.daily import CHAINABLE
from ..core.teaming import TEAM_REQUIRED_REGIONS, TEAM_REQUIRED_TEMPLATES


# ----------------------------------------------------------------------
# 全局快捷键（GetAsyncKeyState 轮询，无需额外依赖，游戏在前台也能触发）
# ----------------------------------------------------------------------
# 可选键 -> Windows 虚拟键码。只放不易和游戏冲突的功能键。
HOTKEY_VK = {
    "F1": 0x70, "F2": 0x71, "F3": 0x72, "F4": 0x73, "F5": 0x74, "F6": 0x75,
    "F7": 0x76, "F8": 0x77, "F9": 0x78, "F10": 0x79, "F11": 0x7A, "F12": 0x7B,
    "Pause": 0x13, "ScrollLock": 0x91, "Home": 0x24, "End": 0x23,
    "Insert": 0x2D, "Delete": 0x2E, "`(~)": 0xC0,
}
HOTKEY_NAMES = list(HOTKEY_VK.keys())


def _vk_down(vk):
    """该虚拟键当前是否按下（最高位为按下状态）。"""
    try:
        return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)
    except Exception:
        return False


# ----------------------------------------------------------------------
# 通用小组件
# ----------------------------------------------------------------------
def Card(master, **kw):
    """一张卡片容器。"""
    opts = dict(fg_color=T.SURFACE, corner_radius=T.RADIUS, border_width=1, border_color=T.BORDER)
    opts.update(kw)
    return ctk.CTkFrame(master, **opts)


def Pill(master, fonts):
    """状态小药丸（圆角标签）。"""
    lbl = ctk.CTkLabel(master, text="", font=fonts["small"], corner_radius=T.RADIUS_PILL,
                       fg_color=T.SURFACE_2, text_color=T.TEXT_DIM,
                       padx=12, pady=4)
    return lbl


# bind_wraplength 现统一定义在 theme 里（window_picker / calibrate_dialog 也复用，避免循环依赖）。
bind_wraplength = T.bind_wraplength


# ----------------------------------------------------------------------
# 秒装备页面
# ----------------------------------------------------------------------
class SniperPage(ctk.CTkFrame):
    TASK_NAME = "sniper"

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

        sub = ctk.CTkLabel(bar, text="盯市场列表，目标装备一出现立刻秒下单",
                           font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left", anchor="w")
        sub.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        bind_wraplength(sub)

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
        tools = ctk.CTkFrame(top, fg_color="transparent")
        tools.grid(row=0, column=2, sticky="e")
        ctk.CTkButton(tools, text="选择窗口", font=self.fonts["body"], height=36, width=104,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=lambda: self.app.open_window_picker(self.refresh)).pack(side="left", padx=(0, 8))
        ctk.CTkButton(tools, text="标定 / 加装备", font=self.fonts["body"], height=36, width=104,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self._open_calibrate).pack(side="left", padx=(0, 8))
        ctk.CTkButton(tools, text="刷新配置", font=self.fonts["body"], height=36, width=104,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self.refresh).pack(side="left")

        # 分隔线
        ctk.CTkFrame(card, fg_color=T.BORDER, height=1).grid(
            row=1, column=0, sticky="ew", padx=16, pady=(0, 4))

        # 第二行：模式开关 + 说明
        opts = ctk.CTkFrame(card, fg_color="transparent")
        opts.grid(row=2, column=0, sticky="ew", padx=16, pady=(8, 16))
        box1 = ctk.CTkFrame(opts, fg_color="transparent")
        box1.pack(anchor="w")
        self.switch_mode = ctk.CTkSwitch(box1, text="实战模式（命中会真买）", font=self.fonts["body"],
                                         progress_color=T.DANGER, command=self._toggle_mode)
        self.switch_mode.pack(anchor="w")
        ctk.CTkLabel(box1, text="关 = 演练（只识别不下单，安全）",
                     font=self.fonts["small"], text_color=T.TEXT_DIM).pack(anchor="w", pady=(5, 0))

    # ---- 主体：左监控清单 + 右日志 ----
    def _build_body(self):
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=2, column=0, sticky="nsew", padx=4)
        body.grid_columnconfigure(0, weight=2, uniform="b")
        body.grid_columnconfigure(1, weight=3, uniform="b")
        body.grid_rowconfigure(0, weight=1)

        # 监控清单卡片
        left = Card(body)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        left.grid_rowconfigure(1, weight=1)
        left.grid_columnconfigure(0, weight=1)
        head = ctk.CTkFrame(left, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 8))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text="监控清单", font=self.fonts["h2"], text_color=T.TEXT).grid(row=0, column=0, sticky="w")
        self.lbl_count = ctk.CTkLabel(head, text="", font=self.fonts["small"], text_color=T.TEXT_DIM)
        self.lbl_count.grid(row=0, column=1, sticky="e")
        self.list_frame = ctk.CTkScrollableFrame(left, fg_color="transparent")
        self.list_frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 12))
        self.list_frame.grid_columnconfigure(0, weight=1)
        T.tune_scroll_speed(self.list_frame)

        # 日志卡片
        right = Card(body)
        right.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        right.grid_rowconfigure(1, weight=1)
        right.grid_columnconfigure(0, weight=1)
        rhead = ctk.CTkFrame(right, fg_color="transparent")
        rhead.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 8))
        rhead.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(rhead, text="运行日志", font=self.fonts["h2"], text_color=T.TEXT).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(rhead, text="清空", font=self.fonts["small"], height=26, width=56,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self._clear_log).grid(row=0, column=1, sticky="e")
        self.log = ctk.CTkTextbox(right, font=self.fonts["mono"], fg_color=T.SURFACE_2,
                                  text_color=T.TEXT, corner_radius=T.RADIUS_SM, wrap="word")
        self.log.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        T.apply_log_tags(self.log._textbox)
        self.log.configure(state="disabled")
        self._log_line("界面就绪。第一次使用请先「标定 / 加装备」。", "info")

    # ------------------------------------------------------------------
    # 数据刷新
    # ------------------------------------------------------------------
    def refresh(self):
        self.app.cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(self.app.cfg, self.TASK_NAME)
        # 模式开关
        dry = tc.get("dry_run", True)
        (self.switch_mode.select if not dry else self.switch_mode.deselect)()
        self._render_mode_pill(dry)
        # 清单
        self._render_watchlist(tc.get("watchlist", []))

    def _render_watchlist(self, items):
        # 内容没变就别重建：切页时 refresh 会反复调到这里，整段拆了重画是「切页卡顿」的来源之一。
        sig = [(it.get("name"), it.get("template"), it.get("max_price")) for it in items]
        if sig == getattr(self, "_wl_sig", None):
            return
        self._wl_sig = sig
        for w in self.list_frame.winfo_children():
            w.destroy()
        self._thumbs.clear()
        self.lbl_count.configure(text=f"{len(items)} 件")

        if not items:
            empty = ctk.CTkLabel(self.list_frame, text="还没有要抢的装备。\n点上方「标定 / 加装备」添加。",
                                 font=self.fonts["body"], text_color=T.TEXT_DIM, justify="left")
            empty.grid(row=0, column=0, sticky="ew", padx=12, pady=20)
            bind_wraplength(empty)
            return

        for i, it in enumerate(items):
            row = ctk.CTkFrame(self.list_frame, fg_color=T.SURFACE_2, corner_radius=T.RADIUS_SM)
            row.grid(row=i, column=0, sticky="ew", pady=4, padx=4)
            row.grid_columnconfigure(1, weight=1)

            thumb = self._load_thumb(it.get("template"))
            if thumb is not None:
                ctk.CTkLabel(row, text="", image=thumb).grid(row=0, column=0, padx=(10, 8), pady=8)
            else:
                ctk.CTkLabel(row, text="🗡", font=self.fonts["h2"]).grid(row=0, column=0, padx=(10, 8), pady=8)

            info = ctk.CTkFrame(row, fg_color="transparent")
            info.grid(row=0, column=1, sticky="w")
            ctk.CTkLabel(info, text=it.get("name", "?"), font=self.fonts["body_b"],
                         text_color=T.TEXT).pack(anchor="w")
            price = it.get("max_price")
            ptxt = "不限价（命中即抢）" if price is None else f"参考价 ≤ {price}"
            ctk.CTkLabel(info, text=ptxt, font=self.fonts["small"], text_color=T.TEXT_DIM).pack(anchor="w")

            ctk.CTkButton(row, text="删除", font=self.fonts["small"], height=28, width=52,
                          corner_radius=T.RADIUS_SM, fg_color="transparent", hover_color=T.DANGER, text_color=T.TEXT,
                          border_width=1, border_color=T.BORDER,
                          command=lambda idx=i: self._delete_item(idx)).grid(row=0, column=2, padx=10)

    def _load_thumb(self, template_rel):
        if not template_rel:
            return None
        try:
            from PIL import Image
            path = template_rel if os.path.isabs(template_rel) else str(cfg_mod.PROJECT_ROOT / template_rel)
            if not os.path.exists(path):
                return None
            img = Image.open(path)
            w, h = img.size
            scale = 40 / max(1, h)
            size = (max(1, int(w * scale)), 40)
            cimg = ctk.CTkImage(light_image=img, dark_image=img, size=size)
            self._thumbs.append(cimg)
            return cimg
        except Exception:
            return None

    def _delete_item(self, idx):
        tc = cfg_mod.task_config(self.app.cfg, self.TASK_NAME)
        wl = tc.get("watchlist", [])
        if 0 <= idx < len(wl):
            removed = wl.pop(idx)
            cfg_mod.set_task_config(self.app.cfg, self.TASK_NAME, tc)
            cfg_mod.save_config(self.app.cfg)
            self._log_line(f"已删除监控：{removed.get('name','?')}", "info")
            self._render_watchlist(wl)

    # ------------------------------------------------------------------
    # 运行控制
    # ------------------------------------------------------------------
    def _toggle_run(self):
        if self.runner and self.runner.is_running():
            self.runner.stop()
            self._log_line("正在停止…", "warn")
            self.btn_run.configure(text="停止中…", state="disabled")
            return
        # 启动
        self.app.cfg = cfg_mod.load_config()
        task_cls = get_task(self.TASK_NAME)
        self.runner = TaskRunner(task_cls(), self.app.cfg)
        ok, problems = self.runner.start()
        if not ok:
            for p in problems:
                self._log_line("无法启动：" + p, "error")
            self.runner = None
            return
        self.btn_run.configure(text="■  停止", fg_color=T.DANGER, hover_color=T.DANGER_HOVER, state="normal")

    def _on_runner_finished(self):
        self.btn_run.configure(text="▶  开始秒装备", fg_color=T.ACCENT,
                               hover_color=T.ACCENT_HOVER, state="normal")

    def _toggle_mode(self):
        live = bool(self.switch_mode.get())  # 1=实战
        tc = cfg_mod.task_config(self.app.cfg, self.TASK_NAME)
        tc["dry_run"] = not live
        cfg_mod.set_task_config(self.app.cfg, self.TASK_NAME, tc)
        cfg_mod.save_config(self.app.cfg)
        self._render_mode_pill(not live)
        if live:
            self._log_line("⚠ 已切到实战模式：命中会真正花钱购买，请谨慎！", "warn")
        else:
            self._log_line("已切回演练模式（安全）。", "info")

    def _render_mode_pill(self, dry):
        if dry:
            self.pill_mode.configure(text="演练", fg_color=T.PILL_OK_BG, text_color=T.SUCCESS)
        else:
            self.pill_mode.configure(text="实战", fg_color=T.PILL_DANGER_BG, text_color=T.DANGER)

    def _open_calibrate(self):
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
        """被 App._tick 周期调用：抽干日志队列、检测运行结束、刷新游戏连接药丸。"""
        if self.runner:
            q = self.runner.log_queue
            while not q.empty():
                level, msg = q.get()
                self._log_line(msg, level)
            if not self.runner.is_running() and self.btn_run.cget("text") != "▶  开始秒装备":
                self._on_runner_finished()

    def update_game_pill(self, connected, summary=""):
        if connected:
            self.pill_game.configure(text="● " + (summary or "目标窗口已连接"),
                                     fg_color=T.PILL_OK_BG, text_color=T.SUCCESS)
        else:
            self.pill_game.configure(text="○ 未检测到目标窗口", fg_color=T.SURFACE_2, text_color=T.TEXT_DIM)

    def _log_line(self, msg, level="info"):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self.log.configure(state="normal")
        try:
            self.log._textbox.insert("end", f"[{ts}] {msg}\n", level)
        except Exception:
            self.log.insert("end", f"[{ts}] {msg}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")


# ----------------------------------------------------------------------
# 刷副本·宝图 页面
# ----------------------------------------------------------------------
class TreasureMapPage(ctk.CTkFrame):
    TASK_NAME = "treasure_map"
    RUN_LABEL = "▶  开始刷宝图"

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.fonts = app.fonts
        self.runner = None
        self._cal_dialog = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        self._build_header()
        self._build_control()
        self._build_body()
        self.refresh()

    def _build_header(self):
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=4, pady=(2, 14))
        bar.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(bar, text="刷副本 · 宝图", font=self.fonts["title"], text_color=T.TEXT).grid(
            row=0, column=0, sticky="w")
        right = ctk.CTkFrame(bar, fg_color="transparent")
        right.grid(row=0, column=1, sticky="e")
        self.pill_game = Pill(right, self.fonts)
        self.pill_game.pack(side="left", padx=(0, 8))
        self.pill_mode = Pill(right, self.fonts)
        self.pill_mode.pack(side="left")
        sub = ctk.CTkLabel(bar, text="自动开活动→收藏宝图→挖宝→领奖，战斗交给游戏自动（支持多开逐号轮转）",
                           font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left", anchor="w")
        sub.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        bind_wraplength(sub)

    def _build_control(self):
        card = Card(self)
        card.grid(row=1, column=0, sticky="ew", padx=4, pady=(0, 14))
        card.grid_columnconfigure(0, weight=1)

        # 第一行：开始按钮（左） + 标定/刷新（右）
        top = ctk.CTkFrame(card, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 10))
        top.grid_columnconfigure(1, weight=1)
        self.btn_run = ctk.CTkButton(top, text=self.RUN_LABEL, font=self.fonts["btn"],
                                     height=46, width=200, corner_radius=T.RADIUS_SM,
                                     fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER, text_color=T.ON_ACCENT,
                                     command=self._toggle_run)
        self.btn_run.grid(row=0, column=0, sticky="w")
        tools = ctk.CTkFrame(top, fg_color="transparent")
        tools.grid(row=0, column=2, sticky="e")
        ctk.CTkButton(tools, text="选择窗口", font=self.fonts["body"], height=36, width=104,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=lambda: self.app.open_window_picker(self.refresh)).pack(side="left", padx=(0, 8))
        ctk.CTkButton(tools, text="标定", font=self.fonts["body"], height=36, width=104,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self._open_calibrate).pack(side="left", padx=(0, 8))
        ctk.CTkButton(tools, text="刷新配置", font=self.fonts["body"], height=36, width=104,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self.refresh).pack(side="left")

        # 分隔线
        ctk.CTkFrame(card, fg_color=T.BORDER, height=1).grid(
            row=1, column=0, sticky="ew", padx=16, pady=(0, 4))

        # 第二行：两个开关左右分布，各带一行说明
        opts = ctk.CTkFrame(card, fg_color="transparent")
        opts.grid(row=2, column=0, sticky="ew", padx=16, pady=(8, 16))
        opts.grid_columnconfigure(0, weight=1, uniform="o")
        opts.grid_columnconfigure(1, weight=1, uniform="o")

        box1 = ctk.CTkFrame(opts, fg_color="transparent")
        box1.grid(row=0, column=0, sticky="ew")
        self.switch_mode = ctk.CTkSwitch(box1, text="实战模式", font=self.fonts["body"],
                                         progress_color=T.DANGER, command=self._toggle_mode)
        self.switch_mode.pack(anchor="w")
        desc_mode = ctk.CTkLabel(box1, text="关 = 演练（只识别自检，安全）　开 = 真开活动/用宝图/领奖",
                                 font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left")
        desc_mode.pack(fill="x", pady=(5, 0))
        bind_wraplength(desc_mode)

        box2 = ctk.CTkFrame(opts, fg_color="transparent")
        box2.grid(row=0, column=1, sticky="ew", padx=(16, 0))
        self.switch_skip = ctk.CTkSwitch(box2, text="已有宝图", font=self.fonts["body"],
                                         progress_color=T.ACCENT, command=self._toggle_skip)
        self.switch_skip.pack(anchor="w")
        desc_skip = ctk.CTkLabel(box2, text="跳过领取，复位后直接挖包裹里的藏宝图",
                                 font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left")
        desc_skip.pack(fill="x", pady=(5, 0))
        bind_wraplength(desc_skip)

    def _build_body(self):
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=2, column=0, sticky="nsew", padx=4)
        body.grid_columnconfigure(0, weight=2, uniform="b")
        body.grid_columnconfigure(1, weight=3, uniform="b")
        body.grid_rowconfigure(0, weight=1)

        # 左：运行参数 + 标定状态
        left = Card(body)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        left.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(left, text="运行参数", font=self.fonts["h2"], text_color=T.TEXT).grid(
            row=0, column=0, sticky="w", padx=16, pady=(14, 6))

        lim = ctk.CTkFrame(left, fg_color="transparent")
        lim.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 6))
        ctk.CTkLabel(lim, text="时间上限(分钟，0=不限)", font=self.fonts["body"],
                     text_color=T.TEXT).pack(side="left")
        self.var_limit = ctk.StringVar(value="30")
        ctk.CTkEntry(lim, textvariable=self.var_limit, width=70, font=self.fonts["body"],
                     fg_color=T.SURFACE_2, border_color=T.BORDER).pack(side="left", padx=(8, 0))

        sd = ctk.CTkFrame(left, fg_color="transparent")
        sd.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 6))
        ctk.CTkLabel(sd, text="静止判定阈值(收集/挖宝完成)", font=self.fonts["body"],
                     text_color=T.TEXT).pack(side="left")
        self.var_still = ctk.StringVar(value="8")
        ctk.CTkEntry(sd, textvariable=self.var_still, width=70, font=self.fonts["body"],
                     fg_color=T.SURFACE_2, border_color=T.BORDER).pack(side="left", padx=(8, 0))

        hint = ctk.CTkLabel(left, text="主终止条件是背包藏宝图挖空；时间上限只是安全网。\n"
                               "“静止判定阈值”太小会一直判不到收集完成→看日志里的实时“帧差”，"
                               "把阈值设到“静止时帧差”之上、“走动时帧差”之下。\n"
                               "鼠标甩到屏幕左上角可紧急停止。",
                     font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left")
        hint.grid(row=3, column=0, sticky="ew", padx=16, pady=(2, 8))
        bind_wraplength(hint)

        self.lbl_calib = ctk.CTkLabel(left, text="", font=self.fonts["small"], text_color=T.TEXT_DIM,
                                      justify="left")
        self.lbl_calib.grid(row=4, column=0, sticky="ew", padx=16, pady=(2, 14))
        bind_wraplength(self.lbl_calib)

        # 右：日志
        right = Card(body)
        right.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        right.grid_rowconfigure(1, weight=1)
        right.grid_columnconfigure(0, weight=1)
        rhead = ctk.CTkFrame(right, fg_color="transparent")
        rhead.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 8))
        rhead.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(rhead, text="运行日志", font=self.fonts["h2"], text_color=T.TEXT).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(rhead, text="清空", font=self.fonts["small"], height=26, width=56,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self._clear_log).grid(row=0, column=1, sticky="e")
        self.log = ctk.CTkTextbox(right, font=self.fonts["mono"], fg_color=T.SURFACE_2,
                                  text_color=T.TEXT, corner_radius=T.RADIUS_SM, wrap="word")
        self.log.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        T.apply_log_tags(self.log._textbox)
        self.log.configure(state="disabled")
        self._log_line("界面就绪。第一次使用请先「标定」(区域 + 各标志模板)，并核对快捷键。", "info")

    # ---- 刷新 / 状态 ----
    def refresh(self):
        self.app.cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(self.app.cfg, self.TASK_NAME)
        dry = tc.get("dry_run", True)
        (self.switch_mode.select if not dry else self.switch_mode.deselect)()
        self._render_mode_pill(dry)
        skip = tc.get("skip_collect", False)
        (self.switch_skip.select if skip else self.switch_skip.deselect)()
        loopc = tc.get("loop", {})
        self.var_limit.set(str(loopc.get("time_limit_min", 30)))
        self.var_still.set(str(loopc.get("still_diff", 8.0)))
        # 标定完成度概览（已有宝图时只需挖宝相关项）
        regions = tc.get("regions", {})
        templates = tc.get("templates", {})
        need_r = ["scene", "bag_list"] if skip else ["scene", "activity_list", "bag_list"]
        need_t = (["flag_next_map", "treasure_item"] if skip else
                  ["flag_treasure_entry", "flag_join", "flag_tingting",
                   "flag_next_map", "treasure_item"])
        rdone = sum(1 for k in need_r if regions.get(k))
        tdone = sum(1 for k in need_t if templates.get(k))
        self.lbl_calib.configure(
            text=f"标定：必要区域 {rdone}/{len(need_r)}，必要模板 {tdone}/{len(need_t)}"
                 + ("　✓ 可运行" if rdone == len(need_r) and tdone == len(need_t) else "　（还需标定）"))

    def _render_mode_pill(self, dry):
        if dry:
            self.pill_mode.configure(text="演练", fg_color=T.PILL_OK_BG, text_color=T.SUCCESS)
        else:
            self.pill_mode.configure(text="实战", fg_color=T.PILL_DANGER_BG, text_color=T.DANGER)

    def update_game_pill(self, connected, summary=""):
        if connected:
            self.pill_game.configure(text="● " + (summary or "目标窗口已连接"),
                                     fg_color=T.PILL_OK_BG, text_color=T.SUCCESS)
        else:
            self.pill_game.configure(text="○ 未检测到目标窗口", fg_color=T.SURFACE_2, text_color=T.TEXT_DIM)

    # ---- 运行控制 ----
    def _toggle_run(self):
        if self.runner and self.runner.is_running():
            self.runner.stop()
            self._log_line("正在停止…", "warn")
            self.btn_run.configure(text="停止中…", state="disabled")
            return
        # 启动前把时间上限写回配置
        self._apply_time_limit()
        self.app.cfg = cfg_mod.load_config()
        task_cls = get_task(self.TASK_NAME)
        self.runner = TaskRunner(task_cls(), self.app.cfg)
        ok, problems = self.runner.start()
        if not ok:
            for p in problems:
                self._log_line("无法启动：" + p, "error")
            self.runner = None
            return
        self.btn_run.configure(text="■  停止", fg_color=T.DANGER, hover_color=T.DANGER_HOVER, state="normal")

    def _apply_time_limit(self):
        """启动前把「运行参数」里可调项（时间上限 / 静止判定阈值）写回配置。"""
        cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(cfg, self.TASK_NAME)
        loopc = tc.setdefault("loop", {})
        try:
            loopc["time_limit_min"] = max(0.0, round(float(self.var_limit.get()), 1))
        except (TypeError, ValueError):
            pass
        try:
            loopc["still_diff"] = max(0.5, round(float(self.var_still.get()), 1))
        except (TypeError, ValueError):
            pass
        cfg_mod.set_task_config(cfg, self.TASK_NAME, tc)
        cfg_mod.save_config(cfg)
        self.app.cfg = cfg

    def _on_runner_finished(self):
        self.btn_run.configure(text=self.RUN_LABEL, fg_color=T.ACCENT,
                               hover_color=T.ACCENT_HOVER, state="normal")

    def _toggle_mode(self):
        live = bool(self.switch_mode.get())
        cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(cfg, self.TASK_NAME)
        tc["dry_run"] = not live
        cfg_mod.set_task_config(cfg, self.TASK_NAME, tc)
        cfg_mod.save_config(cfg)
        self.app.cfg = cfg
        self._render_mode_pill(not live)
        if live:
            self._log_line("⚠ 已切到实战：会真开活动、真用宝图、真领奖，请用小号！", "warn")
        else:
            self._log_line("已切回演练（只识别自检，安全）。", "info")

    def _toggle_skip(self):
        skip = bool(self.switch_skip.get())
        cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(cfg, self.TASK_NAME)
        tc["skip_collect"] = skip
        cfg_mod.set_task_config(cfg, self.TASK_NAME, tc)
        cfg_mod.save_config(cfg)
        self.app.cfg = cfg
        self.refresh()
        if skip:
            self._log_line("已开启「已有宝图」：将跳过开活动领取，复位后直接开背包挖图。", "info")
        else:
            self._log_line("已关闭「已有宝图」：恢复完整流程（先开活动领宝图）。", "info")

    def _open_calibrate(self):
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

    # ---- 日志（由 App._tick 驱动）----
    def pump(self):
        if self.runner:
            q = self.runner.log_queue
            while not q.empty():
                level, msg = q.get()
                self._log_line(msg, level)
            if not self.runner.is_running() and self.btn_run.cget("text") != self.RUN_LABEL:
                self._on_runner_finished()

    def _log_line(self, msg, level="info"):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self.log.configure(state="normal")
        try:
            self.log._textbox.insert("end", f"[{ts}] {msg}\n", level)
        except Exception:
            self.log.insert("end", f"[{ts}] {msg}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")


# ----------------------------------------------------------------------
# 运镖 页面
# ----------------------------------------------------------------------
class EscortPage(ctk.CTkFrame):
    TASK_NAME = "escort"
    RUN_LABEL = "▶  开始运镖"

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.fonts = app.fonts
        self.runner = None
        self._cal_dialog = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        self._build_header()
        self._build_control()
        self._build_body()
        self.refresh()

    def _build_header(self):
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=4, pady=(2, 14))
        bar.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(bar, text="运镖", font=self.fonts["title"], text_color=T.TEXT).grid(
            row=0, column=0, sticky="w")
        right = ctk.CTkFrame(bar, fg_color="transparent")
        right.grid(row=0, column=1, sticky="e")
        self.pill_game = Pill(right, self.fonts)
        self.pill_game.pack(side="left", padx=(0, 8))
        self.pill_mode = Pill(right, self.fonts)
        self.pill_mode.pack(side="left")
        sub = ctk.CTkLabel(bar, text="自动开活动→参加运镖→押送普通镖银→循环押满次数，战斗交给游戏自动",
                           font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left", anchor="w")
        sub.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        bind_wraplength(sub)

    def _build_control(self):
        card = Card(self)
        card.grid(row=1, column=0, sticky="ew", padx=4, pady=(0, 14))
        card.grid_columnconfigure(0, weight=1)

        top = ctk.CTkFrame(card, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 10))
        top.grid_columnconfigure(1, weight=1)
        self.btn_run = ctk.CTkButton(top, text=self.RUN_LABEL, font=self.fonts["btn"],
                                     height=46, width=200, corner_radius=T.RADIUS_SM,
                                     fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER, text_color=T.ON_ACCENT,
                                     command=self._toggle_run)
        self.btn_run.grid(row=0, column=0, sticky="w")
        tools = ctk.CTkFrame(top, fg_color="transparent")
        tools.grid(row=0, column=2, sticky="e")
        ctk.CTkButton(tools, text="选择窗口", font=self.fonts["body"], height=36, width=104,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=lambda: self.app.open_window_picker(self.refresh)).pack(side="left", padx=(0, 8))
        ctk.CTkButton(tools, text="标定", font=self.fonts["body"], height=36, width=104,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self._open_calibrate).pack(side="left", padx=(0, 8))
        ctk.CTkButton(tools, text="刷新配置", font=self.fonts["body"], height=36, width=104,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self.refresh).pack(side="left")

        ctk.CTkFrame(card, fg_color=T.BORDER, height=1).grid(
            row=1, column=0, sticky="ew", padx=16, pady=(0, 4))

        opts = ctk.CTkFrame(card, fg_color="transparent")
        opts.grid(row=2, column=0, sticky="ew", padx=16, pady=(8, 16))
        box1 = ctk.CTkFrame(opts, fg_color="transparent")
        box1.pack(anchor="w", fill="x")
        self.switch_mode = ctk.CTkSwitch(box1, text="实战模式", font=self.fonts["body"],
                                         progress_color=T.DANGER, command=self._toggle_mode)
        self.switch_mode.pack(anchor="w")
        desc_mode = ctk.CTkLabel(box1, text="关 = 演练（只识别自检，安全）　开 = 真开活动/真参加/真押镖",
                                 font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left")
        desc_mode.pack(fill="x", pady=(5, 0))
        bind_wraplength(desc_mode)

    def _build_body(self):
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=2, column=0, sticky="nsew", padx=4)
        body.grid_columnconfigure(0, weight=2, uniform="b")
        body.grid_columnconfigure(1, weight=3, uniform="b")
        body.grid_rowconfigure(0, weight=1)

        # 左：运行参数 + 标定状态
        left = Card(body)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        left.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(left, text="运行参数", font=self.fonts["h2"], text_color=T.TEXT).grid(
            row=0, column=0, sticky="w", padx=16, pady=(14, 6))

        cnt = ctk.CTkFrame(left, fg_color="transparent")
        cnt.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 6))
        ctk.CTkLabel(cnt, text="运镖次数（押满即停）", font=self.fonts["body"],
                     text_color=T.TEXT).pack(side="left")
        self.var_count = ctk.StringVar(value="3")
        ctk.CTkEntry(cnt, textvariable=self.var_count, width=70, font=self.fonts["body"],
                     fg_color=T.SURFACE_2, border_color=T.BORDER).pack(side="left", padx=(8, 0))

        lim = ctk.CTkFrame(left, fg_color="transparent")
        lim.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 6))
        ctk.CTkLabel(lim, text="时间上限(分钟，0=不限)", font=self.fonts["body"],
                     text_color=T.TEXT).pack(side="left")
        self.var_limit = ctk.StringVar(value="30")
        ctk.CTkEntry(lim, textvariable=self.var_limit, width=70, font=self.fonts["body"],
                     fg_color=T.SURFACE_2, border_color=T.BORDER).pack(side="left", padx=(8, 0))

        hint = ctk.CTkLabel(left, text="靠「运镖中」标志判断在不在运镖：只要它在 或 在战斗中就绝不停。\n"
                               "主终止条件是押满设定趟数后「运镖中」标志消失且不再弹对话框；\n"
                               "时间上限只是安全网。鼠标甩到屏幕左上角可紧急停止。",
                     font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left")
        hint.grid(row=3, column=0, sticky="ew", padx=16, pady=(2, 8))
        bind_wraplength(hint)

        self.lbl_calib = ctk.CTkLabel(left, text="", font=self.fonts["small"], text_color=T.TEXT_DIM,
                                      justify="left")
        self.lbl_calib.grid(row=4, column=0, sticky="ew", padx=16, pady=(2, 14))
        bind_wraplength(self.lbl_calib)

        # 右：日志
        right = Card(body)
        right.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        right.grid_rowconfigure(1, weight=1)
        right.grid_columnconfigure(0, weight=1)
        rhead = ctk.CTkFrame(right, fg_color="transparent")
        rhead.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 8))
        rhead.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(rhead, text="运行日志", font=self.fonts["h2"], text_color=T.TEXT).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(rhead, text="清空", font=self.fonts["small"], height=26, width=56,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self._clear_log).grid(row=0, column=1, sticky="e")
        self.log = ctk.CTkTextbox(right, font=self.fonts["mono"], fg_color=T.SURFACE_2,
                                  text_color=T.TEXT, corner_radius=T.RADIUS_SM, wrap="word")
        self.log.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        T.apply_log_tags(self.log._textbox)
        self.log.configure(state="disabled")
        self._log_line("界面就绪。第一次使用请先「标定」(区域 + 各标志模板)，并核对快捷键。", "info")

    # ---- 刷新 / 状态 ----
    def refresh(self):
        self.app.cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(self.app.cfg, self.TASK_NAME)
        dry = tc.get("dry_run", True)
        (self.switch_mode.select if not dry else self.switch_mode.deselect)()
        self._render_mode_pill(dry)
        loopc = tc.get("loop", {})
        self.var_count.set(str(loopc.get("max_escorts", 3)))
        self.var_limit.set(str(loopc.get("time_limit_min", 30)))
        regions = tc.get("regions", {})
        templates = tc.get("templates", {})
        need_r = ["scene", "activity_list"]
        need_t = ["escort_entry", "escort_join", "escort_silver", "escort_confirm", "escort_ongoing"]
        rdone = sum(1 for k in need_r if regions.get(k))
        tdone = sum(1 for k in need_t if templates.get(k))
        self.lbl_calib.configure(
            text=f"标定：必要区域 {rdone}/{len(need_r)}，必要模板 {tdone}/{len(need_t)}"
                 + ("　✓ 可运行" if rdone == len(need_r) and tdone == len(need_t) else "　（还需标定）"))

    def _render_mode_pill(self, dry):
        if dry:
            self.pill_mode.configure(text="演练", fg_color=T.PILL_OK_BG, text_color=T.SUCCESS)
        else:
            self.pill_mode.configure(text="实战", fg_color=T.PILL_DANGER_BG, text_color=T.DANGER)

    def update_game_pill(self, connected, summary=""):
        if connected:
            self.pill_game.configure(text="● " + (summary or "目标窗口已连接"),
                                     fg_color=T.PILL_OK_BG, text_color=T.SUCCESS)
        else:
            self.pill_game.configure(text="○ 未检测到目标窗口", fg_color=T.SURFACE_2, text_color=T.TEXT_DIM)

    # ---- 运行控制 ----
    def _toggle_run(self):
        if self.runner and self.runner.is_running():
            self.runner.stop()
            self._log_line("正在停止…", "warn")
            self.btn_run.configure(text="停止中…", state="disabled")
            return
        self._apply_params()
        self.app.cfg = cfg_mod.load_config()
        task_cls = get_task(self.TASK_NAME)
        self.runner = TaskRunner(task_cls(), self.app.cfg)
        ok, problems = self.runner.start()
        if not ok:
            for p in problems:
                self._log_line("无法启动：" + p, "error")
            self.runner = None
            return
        self.btn_run.configure(text="■  停止", fg_color=T.DANGER, hover_color=T.DANGER_HOVER, state="normal")

    def _apply_params(self):
        """启动前把「运行参数」里可调项（运镖次数 / 时间上限 / 静止判定阈值）写回配置。"""
        cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(cfg, self.TASK_NAME)
        loopc = tc.setdefault("loop", {})
        try:
            loopc["max_escorts"] = max(1, int(float(self.var_count.get())))
        except (TypeError, ValueError):
            pass
        try:
            loopc["time_limit_min"] = max(0.0, round(float(self.var_limit.get()), 1))
        except (TypeError, ValueError):
            pass
        cfg_mod.set_task_config(cfg, self.TASK_NAME, tc)
        cfg_mod.save_config(cfg)
        self.app.cfg = cfg

    def _on_runner_finished(self):
        self.btn_run.configure(text=self.RUN_LABEL, fg_color=T.ACCENT,
                               hover_color=T.ACCENT_HOVER, state="normal")

    def _toggle_mode(self):
        live = bool(self.switch_mode.get())
        cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(cfg, self.TASK_NAME)
        tc["dry_run"] = not live
        cfg_mod.set_task_config(cfg, self.TASK_NAME, tc)
        cfg_mod.save_config(cfg)
        self.app.cfg = cfg
        self._render_mode_pill(not live)
        if live:
            self._log_line("⚠ 已切到实战：会真开活动、真参加、真押镖，请用小号！", "warn")
        else:
            self._log_line("已切回演练（只识别自检，安全）。", "info")

    def _open_calibrate(self):
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

    # ---- 日志（由 App._tick 驱动）----
    def pump(self):
        if self.runner:
            q = self.runner.log_queue
            while not q.empty():
                level, msg = q.get()
                self._log_line(msg, level)
            if not self.runner.is_running() and self.btn_run.cget("text") != self.RUN_LABEL:
                self._on_runner_finished()

    def _log_line(self, msg, level="info"):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self.log.configure(state="normal")
        try:
            self.log._textbox.insert("end", f"[{ts}] {msg}\n", level)
        except Exception:
            self.log.insert("end", f"[{ts}] {msg}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")


# ----------------------------------------------------------------------
# 刷副本 页面（第一版：只做组队）
# ----------------------------------------------------------------------
class DungeonPage(ctk.CTkFrame):
    TASK_NAME = "dungeon"
    RUN_LABEL = "▶  开始刷副本"

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.fonts = app.fonts
        self.runner = None
        self._win_count = 0

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        self._build_header()
        self._build_control()
        self._build_body()
        self.refresh()

    def _build_header(self):
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=4, pady=(2, 14))
        bar.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(bar, text="刷副本", font=self.fonts["title"], text_color=T.TEXT).grid(
            row=0, column=0, sticky="w")
        right = ctk.CTkFrame(bar, fg_color="transparent")
        right.grid(row=0, column=1, sticky="e")
        self.pill_game = Pill(right, self.fonts)
        self.pill_game.pack(side="left", padx=(0, 8))
        self.pill_mode = Pill(right, self.fonts)
        self.pill_mode.pack(side="left")
        sub = ctk.CTkLabel(bar, text="第一版只做组队：指定一个号当队长、其余当队员，自动建队→申请→接受→关窗，"
                                     "组队成功即结束。需多开 3~5 个号、同尺寸。",
                           font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left", anchor="w")
        sub.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        bind_wraplength(sub)

    def _build_control(self):
        card = Card(self)
        card.grid(row=1, column=0, sticky="ew", padx=4, pady=(0, 14))
        card.grid_columnconfigure(0, weight=1)

        top = ctk.CTkFrame(card, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 10))
        top.grid_columnconfigure(1, weight=1)
        self.btn_run = ctk.CTkButton(top, text=self.RUN_LABEL, font=self.fonts["btn"],
                                     height=46, width=200, corner_radius=T.RADIUS_SM,
                                     fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER, text_color=T.ON_ACCENT,
                                     command=self._toggle_run)
        self.btn_run.grid(row=0, column=0, sticky="w")
        tools = ctk.CTkFrame(top, fg_color="transparent")
        tools.grid(row=0, column=2, sticky="e")
        ctk.CTkButton(tools, text="选择窗口", font=self.fonts["body"], height=36, width=104,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=lambda: self.app.open_window_picker(self.refresh)).pack(side="left", padx=(0, 8))
        ctk.CTkButton(tools, text="刷新配置", font=self.fonts["body"], height=36, width=104,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self.refresh).pack(side="left")

        ctk.CTkFrame(card, fg_color=T.BORDER, height=1).grid(
            row=1, column=0, sticky="ew", padx=16, pady=(0, 4))

        opts = ctk.CTkFrame(card, fg_color="transparent")
        opts.grid(row=2, column=0, sticky="ew", padx=16, pady=(8, 16))
        box1 = ctk.CTkFrame(opts, fg_color="transparent")
        box1.pack(anchor="w", fill="x")
        self.switch_mode = ctk.CTkSwitch(box1, text="实战模式", font=self.fonts["body"],
                                         progress_color=T.DANGER, command=self._toggle_mode)
        self.switch_mode.pack(anchor="w")
        desc_mode = ctk.CTkLabel(box1, text="关 = 演练（只识别自检，安全）　开 = 真开队伍/真申请/真接受",
                                 font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left")
        desc_mode.pack(fill="x", pady=(5, 0))
        bind_wraplength(desc_mode)

    def _build_body(self):
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=2, column=0, sticky="nsew", padx=4)
        body.grid_columnconfigure(0, weight=2, uniform="b")
        body.grid_columnconfigure(1, weight=3, uniform="b")
        body.grid_rowconfigure(0, weight=1)

        # 左：组队设置（队长下拉）+ 标定状态
        left = Card(body)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        left.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(left, text="组队设置", font=self.fonts["h2"], text_color=T.TEXT).grid(
            row=0, column=0, sticky="w", padx=16, pady=(14, 6))

        cap = ctk.CTkFrame(left, fg_color="transparent")
        cap.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 6))
        ctk.CTkLabel(cap, text="谁当队长", font=self.fonts["body"], text_color=T.TEXT).pack(side="left")
        self.var_captain = ctk.StringVar(value="号1")
        self.opt_captain = ctk.CTkOptionMenu(cap, variable=self.var_captain, values=["号1"],
                                             font=self.fonts["body"], fg_color=T.SURFACE_2,
                                             button_color=T.BORDER, button_hover_color=T.ACCENT,
                                             text_color=T.TEXT, dropdown_text_color=T.TEXT, width=120,
                                             command=self._on_captain)
        self.opt_captain.pack(side="left", padx=(8, 0))

        hint = ctk.CTkLabel(left, text="从已选的多开窗口里指定队长（按从左到右/上到下编号），其余号自动当队员。\n"
                               "组队的模板/区域在「通用」页统一标定，所有组队任务共享。\n"
                               "鼠标甩到屏幕左上角可紧急停止。",
                     font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left")
        hint.grid(row=2, column=0, sticky="ew", padx=16, pady=(2, 8))
        bind_wraplength(hint)

        self.lbl_calib = ctk.CTkLabel(left, text="", font=self.fonts["small"], text_color=T.TEXT_DIM,
                                      justify="left")
        self.lbl_calib.grid(row=3, column=0, sticky="ew", padx=16, pady=(2, 14))
        bind_wraplength(self.lbl_calib)

        # 右：日志
        right = Card(body)
        right.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        right.grid_rowconfigure(1, weight=1)
        right.grid_columnconfigure(0, weight=1)
        rhead = ctk.CTkFrame(right, fg_color="transparent")
        rhead.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 8))
        rhead.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(rhead, text="运行日志", font=self.fonts["h2"], text_color=T.TEXT).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(rhead, text="清空", font=self.fonts["small"], height=26, width=56,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self._clear_log).grid(row=0, column=1, sticky="e")
        self.log = ctk.CTkTextbox(right, font=self.fonts["mono"], fg_color=T.SURFACE_2,
                                  text_color=T.TEXT, corner_radius=T.RADIUS_SM, wrap="word")
        self.log.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        T.apply_log_tags(self.log._textbox)
        self.log.configure(state="disabled")
        self._log_line("界面就绪。请先「选择窗口」(多开 3~5 号)、指定队长；组队标定在「通用」页完成。", "info")

    # ---- 刷新 / 状态 ----
    def refresh(self):
        self.app.cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(self.app.cfg, self.TASK_NAME)
        dry = tc.get("dry_run", True)
        (self.switch_mode.select if not dry else self.switch_mode.deselect)()
        self._render_mode_pill(dry)
        # 先用上次已知的号数即时渲染（窗口枚举很慢，不能每次切页都同步卡住）；再后台枚举刷新号数。
        self._render_team_status(tc)
        self._kick_count_windows()

    def _render_team_status(self, tc):
        """据当前 self._win_count + teaming 标定渲染队长下拉与就绪状态（纯本地数据，秒回）。"""
        n = self._win_count
        opts = [f"号{i + 1}" for i in range(n)] or ["（未选窗口）"]
        self.opt_captain.configure(values=opts)
        cap = tc.get("captain_index", 0)
        if not (0 <= cap < n):
            cap = 0
        self.var_captain.set(f"号{cap + 1}" if n else "（未选窗口）")

        team_tc = cfg_mod.task_config(self.app.cfg, "teaming")
        treg, ttpl = team_tc.get("regions", {}), team_tc.get("templates", {})
        rdone = sum(1 for k in TEAM_REQUIRED_REGIONS if treg.get(k))
        tdone = sum(1 for k in TEAM_REQUIRED_TEMPLATES if ttpl.get(k))
        ready = (rdone == len(TEAM_REQUIRED_REGIONS) and tdone == len(TEAM_REQUIRED_TEMPLATES)
                 and n >= 3)
        self.lbl_calib.configure(
            text=f"组队标定：必要区域 {rdone}/{len(TEAM_REQUIRED_REGIONS)}，"
                 f"必要模板 {tdone}/{len(TEAM_REQUIRED_TEMPLATES)}\n"
                 f"已选 {n} 个号，队长=号{cap + 1}"
                 + ("　✓ 可运行" if ready else "　（需多开≥3 且标定齐全）"))

    def _kick_count_windows(self):
        """后台枚举已选窗口数，变了再回主线程重渲染。token 丢弃过期结果。"""
        title = self.app.cfg.get("window_title", "梦幻西游")
        offset = self.app.cfg.get("window_offset", [0, 0])
        targets = self.app.cfg.get("targets", {})
        token = object()
        self._count_token = token

        def work():
            try:
                n = len(win_mod.resolve_targets(title, offset, targets))
            except Exception:
                n = 0

            def apply():
                if token is not getattr(self, "_count_token", None):
                    return
                if n != self._win_count:
                    self._win_count = n
                    tc = cfg_mod.task_config(self.app.cfg, self.TASK_NAME)
                    self._render_team_status(tc)

            try:
                self.app.after(0, apply)
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

    def _on_captain(self, choice):
        if not self._win_count:
            return
        try:
            idx = int(str(choice).replace("号", "")) - 1
        except (TypeError, ValueError):
            idx = 0
        idx = max(0, min(self._win_count - 1, idx))
        cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(cfg, self.TASK_NAME)
        tc["captain_index"] = idx
        cfg_mod.set_task_config(cfg, self.TASK_NAME, tc)
        cfg_mod.save_config(cfg)
        self.app.cfg = cfg

    def _render_mode_pill(self, dry):
        if dry:
            self.pill_mode.configure(text="演练", fg_color=T.PILL_OK_BG, text_color=T.SUCCESS)
        else:
            self.pill_mode.configure(text="实战", fg_color=T.PILL_DANGER_BG, text_color=T.DANGER)

    def update_game_pill(self, connected, summary=""):
        if connected:
            self.pill_game.configure(text="● " + (summary or "目标窗口已连接"),
                                     fg_color=T.PILL_OK_BG, text_color=T.SUCCESS)
        else:
            self.pill_game.configure(text="○ 未检测到目标窗口", fg_color=T.SURFACE_2, text_color=T.TEXT_DIM)

    # ---- 运行控制 ----
    def _toggle_run(self):
        if self.runner and self.runner.is_running():
            self.runner.stop()
            self._log_line("正在停止…", "warn")
            self.btn_run.configure(text="停止中…", state="disabled")
            return
        self._apply_params()
        self.app.cfg = cfg_mod.load_config()
        task_cls = get_task(self.TASK_NAME)
        self.runner = TaskRunner(task_cls(), self.app.cfg)
        ok, problems = self.runner.start()
        if not ok:
            for p in problems:
                self._log_line("无法启动：" + p, "error")
            self.runner = None
            return
        self.btn_run.configure(text="■  停止", fg_color=T.DANGER, hover_color=T.DANGER_HOVER, state="normal")

    def _apply_params(self):
        """启动前把队长序号写回配置（下拉框变更时已即时存，这里兜底再存一次）。"""
        cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(cfg, self.TASK_NAME)
        if self._win_count:
            try:
                idx = int(self.var_captain.get().replace("号", "")) - 1
                tc["captain_index"] = max(0, min(self._win_count - 1, idx))
            except (TypeError, ValueError):
                pass
        cfg_mod.set_task_config(cfg, self.TASK_NAME, tc)
        cfg_mod.save_config(cfg)
        self.app.cfg = cfg

    def _on_runner_finished(self):
        self.btn_run.configure(text=self.RUN_LABEL, fg_color=T.ACCENT,
                               hover_color=T.ACCENT_HOVER, state="normal")

    def _toggle_mode(self):
        live = bool(self.switch_mode.get())
        cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(cfg, self.TASK_NAME)
        tc["dry_run"] = not live
        cfg_mod.set_task_config(cfg, self.TASK_NAME, tc)
        cfg_mod.save_config(cfg)
        self.app.cfg = cfg
        self._render_mode_pill(not live)
        if live:
            self._log_line("⚠ 已切到实战：会真开队伍、真申请、真接受，请用小号！", "warn")
        else:
            self._log_line("已切回演练（只识别自检，安全）。", "info")

    # ---- 日志（由 App._tick 驱动）----
    def pump(self):
        if self.runner:
            q = self.runner.log_queue
            while not q.empty():
                level, msg = q.get()
                self._log_line(msg, level)
            if not self.runner.is_running() and self.btn_run.cget("text") != self.RUN_LABEL:
                self._on_runner_finished()

    def _log_line(self, msg, level="info"):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self.log.configure(state="normal")
        try:
            self.log._textbox.insert("end", f"[{ts}] {msg}\n", level)
        except Exception:
            self.log.insert("end", f"[{ts}] {msg}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")


# ----------------------------------------------------------------------
# 秘境降妖 页面
# ----------------------------------------------------------------------
class SecretRealmPage(ctk.CTkFrame):
    TASK_NAME = "secret_realm"
    RUN_LABEL = "▶  开始秘境降妖"

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.fonts = app.fonts
        self.runner = None
        self._cal_dialog = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        self._build_header()
        self._build_control()
        self._build_body()
        self.refresh()

    def _build_header(self):
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=4, pady=(2, 14))
        bar.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(bar, text="秘境降妖", font=self.fonts["title"], text_color=T.TEXT).grid(
            row=0, column=0, sticky="w")
        right = ctk.CTkFrame(bar, fg_color="transparent")
        right.grid(row=0, column=1, sticky="e")
        self.pill_game = Pill(right, self.fonts)
        self.pill_game.pack(side="left", padx=(0, 8))
        self.pill_mode = Pill(right, self.fonts)
        self.pill_mode.pack(side="left")
        sub = ctk.CTkLabel(bar, text="开活动→参加→秘境降妖→选副本/确定/继续挑战/挑战→盯进入战斗续战，失败超时自动离开（支持多开逐号轮转）",
                           font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left", anchor="w")
        sub.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        bind_wraplength(sub)

    def _build_control(self):
        card = Card(self)
        card.grid(row=1, column=0, sticky="ew", padx=4, pady=(0, 14))
        card.grid_columnconfigure(0, weight=1)

        top = ctk.CTkFrame(card, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 10))
        top.grid_columnconfigure(1, weight=1)
        self.btn_run = ctk.CTkButton(top, text=self.RUN_LABEL, font=self.fonts["btn"],
                                     height=46, width=200, corner_radius=T.RADIUS_SM,
                                     fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER, text_color=T.ON_ACCENT,
                                     command=self._toggle_run)
        self.btn_run.grid(row=0, column=0, sticky="w")
        tools = ctk.CTkFrame(top, fg_color="transparent")
        tools.grid(row=0, column=2, sticky="e")
        ctk.CTkButton(tools, text="选择窗口", font=self.fonts["body"], height=36, width=104,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=lambda: self.app.open_window_picker(self.refresh)).pack(side="left", padx=(0, 8))
        ctk.CTkButton(tools, text="标定", font=self.fonts["body"], height=36, width=104,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self._open_calibrate).pack(side="left", padx=(0, 8))
        ctk.CTkButton(tools, text="刷新配置", font=self.fonts["body"], height=36, width=104,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self.refresh).pack(side="left")

        ctk.CTkFrame(card, fg_color=T.BORDER, height=1).grid(
            row=1, column=0, sticky="ew", padx=16, pady=(0, 4))

        opts = ctk.CTkFrame(card, fg_color="transparent")
        opts.grid(row=2, column=0, sticky="ew", padx=16, pady=(8, 16))
        box1 = ctk.CTkFrame(opts, fg_color="transparent")
        box1.pack(anchor="w", fill="x")
        self.switch_mode = ctk.CTkSwitch(box1, text="实战模式", font=self.fonts["body"],
                                         progress_color=T.DANGER, command=self._toggle_mode)
        self.switch_mode.pack(anchor="w")
        desc_mode = ctk.CTkLabel(box1, text="关 = 演练（只识别自检，安全）　开 = 真开活动/真参加/真挑战秘境",
                                 font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left")
        desc_mode.pack(fill="x", pady=(5, 0))
        bind_wraplength(desc_mode)

    def _build_body(self):
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=2, column=0, sticky="nsew", padx=4)
        body.grid_columnconfigure(0, weight=2, uniform="b")
        body.grid_columnconfigure(1, weight=3, uniform="b")
        body.grid_rowconfigure(0, weight=1)

        # 左：运行参数 + 标定状态
        left = Card(body)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        left.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(left, text="运行参数", font=self.fonts["h2"], text_color=T.TEXT).grid(
            row=0, column=0, sticky="w", padx=16, pady=(14, 6))

        cnt = ctk.CTkFrame(left, fg_color="transparent")
        cnt.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 6))
        ctk.CTkLabel(cnt, text="连跑轮数（跑满即停）", font=self.fonts["body"],
                     text_color=T.TEXT).pack(side="left")
        self.var_count = ctk.StringVar(value="1")
        ctk.CTkEntry(cnt, textvariable=self.var_count, width=70, font=self.fonts["body"],
                     fg_color=T.SURFACE_2, border_color=T.BORDER).pack(side="left", padx=(8, 0))

        lim = ctk.CTkFrame(left, fg_color="transparent")
        lim.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 6))
        ctk.CTkLabel(lim, text="时间上限(分钟，0=不限)", font=self.fonts["body"],
                     text_color=T.TEXT).pack(side="left")
        self.var_limit = ctk.StringVar(value="30")
        ctk.CTkEntry(lim, textvariable=self.var_limit, width=70, font=self.fonts["body"],
                     fg_color=T.SURFACE_2, border_color=T.BORDER).pack(side="left", padx=(8, 0))

        hint = ctk.CTkLabel(left, text="进入秘境后游戏自动战斗；到难度关卡会停下，脚本实时盯「进入战斗」按钮一出现就点。\n"
                               "每轮终止条件是出现 失败/超时/离开；时间上限只是安全网。\n"
                               "几个副本的「进入」长得一样，只认左下角那个（比例框可在配置 dungeon_enter_box 调）。\n"
                               "鼠标甩到屏幕左上角可紧急停止。",
                     font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left")
        hint.grid(row=3, column=0, sticky="ew", padx=16, pady=(2, 8))
        bind_wraplength(hint)

        self.lbl_calib = ctk.CTkLabel(left, text="", font=self.fonts["small"], text_color=T.TEXT_DIM,
                                      justify="left")
        self.lbl_calib.grid(row=4, column=0, sticky="ew", padx=16, pady=(2, 14))
        bind_wraplength(self.lbl_calib)

        # 右：日志
        right = Card(body)
        right.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        right.grid_rowconfigure(1, weight=1)
        right.grid_columnconfigure(0, weight=1)
        rhead = ctk.CTkFrame(right, fg_color="transparent")
        rhead.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 8))
        rhead.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(rhead, text="运行日志", font=self.fonts["h2"], text_color=T.TEXT).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(rhead, text="清空", font=self.fonts["small"], height=26, width=56,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self._clear_log).grid(row=0, column=1, sticky="e")
        self.log = ctk.CTkTextbox(right, font=self.fonts["mono"], fg_color=T.SURFACE_2,
                                  text_color=T.TEXT, corner_radius=T.RADIUS_SM, wrap="word")
        self.log.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        T.apply_log_tags(self.log._textbox)
        self.log.configure(state="disabled")
        self._log_line("界面就绪。第一次使用请先「标定」(区域 + 各标志模板)，并核对快捷键。", "info")

    # ---- 刷新 / 状态 ----
    def refresh(self):
        self.app.cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(self.app.cfg, self.TASK_NAME)
        dry = tc.get("dry_run", True)
        (self.switch_mode.select if not dry else self.switch_mode.deselect)()
        self._render_mode_pill(dry)
        loopc = tc.get("loop", {})
        self.var_count.set(str(loopc.get("max_runs", 1)))
        self.var_limit.set(str(loopc.get("time_limit_min", 30)))
        regions = tc.get("regions", {})
        templates = tc.get("templates", {})
        need_r = ["scene", "activity_list"]
        need_t = ["sr_entry", "sr_join", "sr_select",
                  "sr_continue", "sr_challenge", "sr_enter_battle", "sr_leave"]
        rdone = sum(1 for k in need_r if regions.get(k))
        tdone = sum(1 for k in need_t if templates.get(k))
        self.lbl_calib.configure(
            text=f"标定：必要区域 {rdone}/{len(need_r)}，必要模板 {tdone}/{len(need_t)}"
                 + ("　✓ 可运行" if rdone == len(need_r) and tdone == len(need_t) else "　（还需标定）"))

    def _render_mode_pill(self, dry):
        if dry:
            self.pill_mode.configure(text="演练", fg_color=T.PILL_OK_BG, text_color=T.SUCCESS)
        else:
            self.pill_mode.configure(text="实战", fg_color=T.PILL_DANGER_BG, text_color=T.DANGER)

    def update_game_pill(self, connected, summary=""):
        if connected:
            self.pill_game.configure(text="● " + (summary or "目标窗口已连接"),
                                     fg_color=T.PILL_OK_BG, text_color=T.SUCCESS)
        else:
            self.pill_game.configure(text="○ 未检测到目标窗口", fg_color=T.SURFACE_2, text_color=T.TEXT_DIM)

    # ---- 运行控制 ----
    def _toggle_run(self):
        if self.runner and self.runner.is_running():
            self.runner.stop()
            self._log_line("正在停止…", "warn")
            self.btn_run.configure(text="停止中…", state="disabled")
            return
        self._apply_params()
        self.app.cfg = cfg_mod.load_config()
        task_cls = get_task(self.TASK_NAME)
        self.runner = TaskRunner(task_cls(), self.app.cfg)
        ok, problems = self.runner.start()
        if not ok:
            for p in problems:
                self._log_line("无法启动：" + p, "error")
            self.runner = None
            return
        self.btn_run.configure(text="■  停止", fg_color=T.DANGER, hover_color=T.DANGER_HOVER, state="normal")

    def _apply_params(self):
        """启动前把「运行参数」里可调项（连跑轮数 / 时间上限）写回配置。"""
        cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(cfg, self.TASK_NAME)
        loopc = tc.setdefault("loop", {})
        try:
            loopc["max_runs"] = max(1, int(float(self.var_count.get())))
        except (TypeError, ValueError):
            pass
        try:
            loopc["time_limit_min"] = max(0.0, round(float(self.var_limit.get()), 1))
        except (TypeError, ValueError):
            pass
        cfg_mod.set_task_config(cfg, self.TASK_NAME, tc)
        cfg_mod.save_config(cfg)
        self.app.cfg = cfg

    def _on_runner_finished(self):
        self.btn_run.configure(text=self.RUN_LABEL, fg_color=T.ACCENT,
                               hover_color=T.ACCENT_HOVER, state="normal")

    def _toggle_mode(self):
        live = bool(self.switch_mode.get())
        cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(cfg, self.TASK_NAME)
        tc["dry_run"] = not live
        cfg_mod.set_task_config(cfg, self.TASK_NAME, tc)
        cfg_mod.save_config(cfg)
        self.app.cfg = cfg
        self._render_mode_pill(not live)
        if live:
            self._log_line("⚠ 已切到实战：会真开活动、真参加、真挑战秘境，请用小号！", "warn")
        else:
            self._log_line("已切回演练（只识别自检，安全）。", "info")

    def _open_calibrate(self):
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

    # ---- 日志（由 App._tick 驱动）----
    def pump(self):
        if self.runner:
            q = self.runner.log_queue
            while not q.empty():
                level, msg = q.get()
                self._log_line(msg, level)
            if not self.runner.is_running() and self.btn_run.cget("text") != self.RUN_LABEL:
                self._on_runner_finished()

    def _log_line(self, msg, level="info"):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self.log.configure(state="normal")
        try:
            self.log._textbox.insert("end", f"[{ts}] {msg}\n", level)
        except Exception:
            self.log.insert("end", f"[{ts}] {msg}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")


# ----------------------------------------------------------------------
# 设置页面
# ----------------------------------------------------------------------
class SettingsPage(ctk.CTkFrame):
    """设置页：基础项 + 「速度与节奏（手速）」一组滑块，全部可在界面里调。"""

    # 速度/节奏滑块定义：(存储位置, 键, 标签, 下限, 上限, 步数, 小数位, 说明)
    #   loc: "humanize" 存到 cfg["humanize"]；"loop" 存到 tasks.sniper.loop
    SPEED_FIELDS = [
        ("humanize", "speed", "整体速度倍率", 0.5, 3.0, 25, 2,
         "总开关：越大鼠标移动/点击越快(按比例缩短拟人化延迟)。想抢得快先调它。1.0=原速。"),
        ("humanize", "snipe_speed", "命中下单极速倍率", 1.0, 6.0, 25, 1,
         "命中后「下单那一下」的额外提速：只在抢的瞬间生效，巡航不受影响。越大越抢得到、也越不像人。建议 3~5。"),
        ("humanize", "px_per_step", "鼠标移动步长(px)", 6, 40, 34, 0,
         "每步移动的像素。越大步数越少→移动越快，但轨迹越不平滑(略更像机器)。"),
        ("loop", "shelf_load_wait_sec", "货架加载最长等待(秒)", 0.2, 3.0, 28, 2,
         "等货架刷出的上限/超时。自适应：画面一静止就提前识别，不会傻等满。只有慢机/慢网才需调大。"),
        ("loop", "shelf_load_min_sec", "货架加载最短等待(秒)", 0.0, 1.5, 30, 2,
         "再快也至少等这么久给画面起步。太小可能没开始加载就截图、偶发漏识别，那就调大一点。"),
        ("loop", "refresh_interval_sec", "两轮间隔(秒)", 0.0, 3.0, 30, 2,
         "两轮重进货架之间的停顿(带抖动)。想最快就调到接近 0，但完全无间隔更像机器。"),
        ("loop", "after_buy_cooldown_sec", "购买后冷却(秒)", 0.3, 5.0, 47, 2,
         "命中下单后的等待。给购买弹窗收尾用，太小可能下一轮误点。"),
        ("humanize", "idle_chance", "走神概率", 0.0, 0.10, 20, 3,
         "每轮随机“发呆”的概率，越像真人但会拖慢节奏。专心抢货时设 0。"),
        ("humanize", "click_radius", "落点随机半径(px)", 0, 12, 12, 0,
         "点击落点在目标周围随机偏移的范围。0=每次点正中心(更准但更机械)。"),
        ("humanize", "interval_jitter", "间隔抖动比例", 0.0, 0.8, 16, 2,
         "各种等待时间的随机浮动幅度。越大越不规律(更像人)，越小越稳定。"),
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
        hk = cfg.get("hotkey_toggle", "F5")
        self.var_hotkey = ctk.StringVar(value=hk if hk in HOTKEY_NAMES else "F5")
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
        self._row(card, 3, "开始/停止 快捷键", self._build_hotkey(card), sticky="ew")
        self._row(card, 4, "识别置信度（匹配阈值）", self._build_threshold(card), sticky="ew")

    def _build_hotkey(self, parent):
        box = ctk.CTkFrame(parent, fg_color="transparent")
        ctk.CTkOptionMenu(box, variable=self.var_hotkey, values=HOTKEY_NAMES,
                          font=self.fonts["body"], fg_color=T.SURFACE_2,
                          button_color=T.BORDER, button_hover_color=T.ACCENT, text_color=T.TEXT, dropdown_text_color=T.TEXT,
                          width=240).pack(anchor="w")
        hint = ctk.CTkLabel(box, text="全局热键：游戏在前台也能按。鼠标被脚本拉着失控时，按一下立刻停。改完记得保存。",
                     font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left")
        hint.pack(fill="x", pady=(4, 0))
        bind_wraplength(hint)
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
        warn = ctk.CTkLabel(head, text="抢不过别人就往「快」调；但越快越规律越像机器、封号风险越高。先在演练模式下试。",
                     font=self.fonts["small"], text_color=T.WARN, justify="left")
        warn.pack(fill="x", pady=(2, 0))
        bind_wraplength(warn)

        for i, (loc, key, label, lo, hi, steps, dec, hint) in enumerate(self.SPEED_FIELDS):
            self._slider_row(card, i + 1, loc, key, label, lo, hi, steps, dec, hint)

    def _slider_row(self, parent, row, loc, key, label, lo, hi, steps, dec, hint):
        box = ctk.CTkFrame(parent, fg_color="transparent")
        box.grid(row=row, column=0, sticky="ew", padx=16, pady=(10, 6))
        box.grid_columnconfigure(1, weight=1)

        # 第一行：标签 + 滑块 + 数值
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

        # 第二行：说明文字（整行单独占一行，不再和滑块重叠）
        hint_lbl = ctk.CTkLabel(box, text=hint, font=self.fonts["small"], text_color=T.TEXT_DIM,
                     justify="left")
        hint_lbl.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(4, 0))
        bind_wraplength(hint_lbl)

    def _on_slider(self, key, val):
        lbl, fmt = self._value_labels[key]
        lbl.configure(text=fmt.format(float(val)))

    # ---- 取值/存值助手 ----
    def _get_value(self, loc, key):
        if loc == "humanize":
            src = self.app.cfg.get("humanize", {})
            default = cfg_mod.DEFAULT_CONFIG["humanize"].get(key, 0)
        else:  # loop
            src = cfg_mod.task_config(self.app.cfg, SniperPage.TASK_NAME).get("loop", {})
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
        tc = cfg_mod.task_config(self.app.cfg, SniperPage.TASK_NAME)
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
        hint = ctk.CTkLabel(box, text="越高越严格：命中更准但可能漏；越低越宽松：易命中但可能误认。建议 0.85~0.92。",
                     font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left")
        hint.pack(fill="x", pady=(4, 0))
        bind_wraplength(hint)
        return box

    def _on_threshold(self, val):
        self.thr_value.configure(text=f"{float(val):.2f}")

    def _save(self):
        # 重新读盘再改，避免覆盖掉标定向导刚写入的 regions/watchlist。
        cfg = cfg_mod.load_config()
        cfg["window_title"] = self.var_title.get().strip() or "梦幻西游"
        cfg["input_backend"] = self.var_backend.get()
        cfg["hotkey_toggle"] = self.var_hotkey.get()

        hz = cfg.setdefault("humanize", {})
        tc = cfg_mod.task_config(cfg, SniperPage.TASK_NAME)
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

        cfg_mod.set_task_config(cfg, SniperPage.TASK_NAME, tc)
        cfg_mod.save_config(cfg)
        self.app.cfg = cfg
        self.app.toast("设置已保存")

    def refresh(self):
        self.var_title.set(self.app.cfg.get("window_title", "梦幻西游"))
        self.var_backend.set(self.app.cfg.get("input_backend", "sendinput"))
        hk = self.app.cfg.get("hotkey_toggle", "F5")
        self.var_hotkey.set(hk if hk in HOTKEY_NAMES else "F5")
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


# ----------------------------------------------------------------------
# 关于页面
# ----------------------------------------------------------------------
class AboutPage(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.fonts = app.fonts
        ctk.CTkLabel(self, text="关于", font=self.fonts["title"], text_color=T.TEXT).pack(
            anchor="w", padx=4, pady=(2, 14))
        card = Card(self)
        card.pack(fill="x", padx=4)
        text = (
            "梦幻西游 · 时空  辅助助手\n\n"
            "· 原理：截屏 + 图像识别 + 拟人化模拟点击，不读内存、不注入进程。\n"
            "· 输入走 Windows SendInput 底层接口，配合贝塞尔移动/随机抖动，尽量不像机器。\n"
            "· 模块化架构：core 基础设施 / tasks 任务 / gui 界面，便于后续扩展。\n\n"
            "⚠ 风险提示：使用任何第三方脚本都违反《梦幻西游》用户协议，可能被封号（含永封）。\n"
            "   请务必用小号测试，自负风险。本工具仅供学习交流。"
        )
        lbl = ctk.CTkLabel(card, text=text, font=self.fonts["body"], text_color=T.TEXT,
                     justify="left")
        lbl.pack(fill="x", padx=16, pady=16)
        bind_wraplength(lbl, padding=32)


# ----------------------------------------------------------------------
# 通用 / 工具页：跨任务、任务流程之外的功能（选窗口 / 标定尺寸 / 还原尺寸）
# ----------------------------------------------------------------------
class GeneralPage(ctk.CTkFrame):
    """通用页：集中放与具体任务无关的功能。
    目前：窗口尺寸归一化——列出所有游戏窗口、把某个尺寸设为基准、一键还原被拉大的号。"""

    def __init__(self, parent, app):
        super().__init__(parent, fg_color="transparent")
        self.app = app
        self.fonts = app.fonts
        self.cfg = app.cfg
        self._build()

    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ctk.CTkLabel(head, text="通用 / 工具", font=self.fonts["title"], text_color=T.TEXT).pack(anchor="w")
        sub = ctk.CTkLabel(head, text="跨任务的通用功能：组队标定 / 还原窗口尺寸。各任务专属的标定与「选择窗口」仍在对应任务页。",
                           font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left", anchor="w")
        sub.pack(fill="x", anchor="w", pady=(4, 0))
        bind_wraplength(sub)

        self.body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.body.grid(row=1, column=0, sticky="nsew")
        self.body.grid_columnconfigure(0, weight=1)
        T.tune_scroll_speed(self.body)
        # 不在构建时枚举窗口（省启动开销）；首次切到本页时 _show 会调 refresh() 填充。

    def _card(self):
        c = Card(self.body)
        c.pack(fill="x", pady=(0, T.SP_3), padx=2)
        return c

    # 切到本页或操作后都会调
    def refresh(self):
        self.cfg = cfg_mod.load_config()
        self.app.cfg = self.cfg
        self._refresh_body()

    def _normalize_now(self):
        """点「还原尺寸」时触发：把所有尺寸≠基准的游戏窗口拉回基准尺寸。
        基准未设置时提示；没有窗口时提示。只对尺寸不符的窗口动手（已是基准的不碰）。"""
        cfg = cfg_mod.load_config()
        self.cfg = cfg
        self.app.cfg = cfg
        base = (cfg.get("targets") or {}).get("base_size")
        if not base or len(base) < 2:
            self.app.toast("请先设置基准尺寸（填上面两个框或在窗口列表点「设为基准」）")
            return
        bw, bh = int(base[0]), int(base[1])
        title = cfg.get("window_title", "梦幻西游")
        offset = cfg.get("window_offset", [0, 0])
        try:
            wins = win_mod.locate_all(title, offset)
        except Exception:
            wins = []
        if not wins:
            self.app.toast(f"没检测到游戏窗口（标题含「{title}」），请先打开游戏")
            return
        todo = []
        for w in wins:
            r = w.rect()
            if r and (abs(r[2] - bw) > 4 or abs(r[3] - bh) > 4):
                todo.append(w)
        if not todo:
            self.app.toast(f"所有窗口已是基准尺寸 {bw}×{bh}，无需还原")
            self.refresh()
            return
        ok = 0
        for w in todo:
            w.activate()
            if w.resize_to(bw, bh):
                ok += 1
        self.app._game_connected = None    # 尺寸变了，强制下次 tick 刷新药丸
        if ok < len(todo):
            self.app.toast(f"已还原 {ok}/{len(todo)} 个号到 {bw}×{bh}；部分窗口可能锁了分辨率档位")
        else:
            self.app.toast(f"已把 {ok} 个号还原到基准尺寸 {bw}×{bh}")
        self.refresh()

    def _refresh_body(self):
        for w in self.body.winfo_children():
            w.destroy()
        cfg = self.cfg
        targets = cfg.get("targets", {})
        base = targets.get("base_size")

        # ── 组队（跨任务共享：任何用到组队的任务都自动读这份标定）──
        c_team = self._card()
        head_t = ctk.CTkFrame(c_team, fg_color="transparent")
        head_t.pack(fill="x", padx=16, pady=(14, 4))
        head_t.grid_columnconfigure(0, weight=1)
        txt_t = ctk.CTkFrame(head_t, fg_color="transparent")
        txt_t.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(txt_t, text="组队（共享）", font=self.fonts["h2"], text_color=T.TEXT).pack(anchor="w")
        team_tc = cfg_mod.task_config(cfg, "teaming")
        treg, ttpl = team_tc.get("regions", {}), team_tc.get("templates", {})
        rdone = sum(1 for k in TEAM_REQUIRED_REGIONS if treg.get(k))
        tdone = sum(1 for k in TEAM_REQUIRED_TEMPLATES if ttpl.get(k))
        ready = (rdone == len(TEAM_REQUIRED_REGIONS) and tdone == len(TEAM_REQUIRED_TEMPLATES))
        ctk.CTkLabel(txt_t, text=f"组队标定：必要区域 {rdone}/{len(TEAM_REQUIRED_REGIONS)}，"
                                 f"必要模板 {tdone}/{len(TEAM_REQUIRED_TEMPLATES)}"
                                 + ("　✓ 已就绪" if ready else "　（还需标定）"),
                     font=self.fonts["body"],
                     text_color=T.SUCCESS if ready else T.WARN).pack(anchor="w", pady=(4, 0))
        sub_t = ctk.CTkLabel(txt_t, text="队长建队→队员申请→接受→关窗，是跨任务的共享能力。"
                                        "刷副本等任何用到组队的任务都自动读这份标定"
                                        "（队长ID、创建/申请/接受/申请入队、好友列表区/队伍面板区等）。",
                             font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left")
        sub_t.pack(fill="x", pady=(2, 0))
        bind_wraplength(sub_t)
        btns_t = ctk.CTkFrame(head_t, fg_color="transparent")
        btns_t.grid(row=0, column=1, padx=(12, 0))
        ctk.CTkButton(btns_t, text="标定（组队）", font=self.fonts["body"], height=36, width=120,
                      corner_radius=T.RADIUS_SM, fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER,
                      text_color=T.ON_ACCENT, command=self._open_team_calibrate).pack()

        # ── 窗口尺寸归一化 ──
        c2 = self._card()
        head2 = ctk.CTkFrame(c2, fg_color="transparent")
        head2.pack(fill="x", padx=16, pady=(14, 4))
        head2.grid_columnconfigure(0, weight=1)
        txt2 = ctk.CTkFrame(head2, fg_color="transparent")
        txt2.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(txt2, text="窗口尺寸归一化", font=self.fonts["h2"], text_color=T.TEXT).pack(anchor="w")
        base_txt = f"{int(base[0])}×{int(base[1])}" if base and len(base) >= 2 else "未设置"
        ctk.CTkLabel(txt2, text=f"当前基准尺寸：{base_txt}", font=self.fonts["body"],
                     text_color=T.TEXT if base else T.WARN).pack(anchor="w", pady=(4, 0))
        sub2 = ctk.CTkLabel(txt2, text="点「还原尺寸」把所有窗口拉回基准尺寸（脚本点位按此尺寸标定）。"
                                       "在下面窗口列表点「设为基准」来设定基准尺寸。",
                            font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left")
        sub2.pack(fill="x", pady=(2, 0))
        bind_wraplength(sub2)
        btns2 = ctk.CTkFrame(head2, fg_color="transparent")
        btns2.grid(row=0, column=1, padx=(12, 0))
        ctk.CTkButton(btns2, text="还原尺寸", font=self.fonts["body"], height=36, width=100,
                      corner_radius=T.RADIUS_SM, fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER, text_color=T.ON_ACCENT,
                      command=self._normalize_now).pack(pady=(0, 6))
        ctk.CTkButton(btns2, text="刷新", font=self.fonts["body"], height=30, width=100,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self.refresh).pack()

        if not (base and len(base) >= 2):
            ctk.CTkLabel(c2, text="基准尺寸尚未设置：在下方窗口列表点「设为基准」即可。",
                         font=self.fonts["small"], text_color=T.WARN, justify="left").pack(
                             anchor="w", padx=16, pady=(2, 0))

        # 窗口列表（信息 + 快捷把某个窗口尺寸设为基准）
        ctk.CTkLabel(c2, text="检测到的窗口（点「设为基准」用该窗口的当前尺寸作为基准）：",
                     font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left").pack(
                         anchor="w", padx=16, pady=(8, 2))
        # 窗口枚举（getAllWindows）很慢、绝不能卡主线程：先放占位、后台线程枚举完再回主线程填。
        rows_holder = ctk.CTkFrame(c2, fg_color="transparent")
        rows_holder.pack(fill="x")
        ctk.CTkLabel(rows_holder, text="正在检测窗口…", font=self.fonts["body"],
                     text_color=T.TEXT_DIM).pack(anchor="w", padx=16, pady=(2, 14))
        self._kick_enum_windows(rows_holder, base)

    def _kick_enum_windows(self, holder, base):
        """后台枚举窗口，完成后回主线程把列表填进 holder。用 token 丢弃过期结果（连续切页/刷新时）。"""
        cfg = self.cfg
        title = cfg.get("window_title", "梦幻西游")
        offset = cfg.get("window_offset", [0, 0])
        token = object()
        self._enum_token = token

        def work():
            try:
                wins = win_mod.locate_all(title, offset)
                data = [(w, w.rect()) for w in wins]   # rect() 趁后台一并取好
            except Exception:
                data = []
            try:
                self.app.after(0, lambda: self._fill_win_rows(holder, data, base, token))
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

    def _fill_win_rows(self, holder, data, base, token):
        """在主线程把枚举结果渲染进 holder。过期结果/控件已销毁则丢弃。"""
        if token is not getattr(self, "_enum_token", None):
            return
        try:
            if not holder.winfo_exists():
                return
            for w in holder.winfo_children():
                w.destroy()
        except Exception:
            return
        if not data:
            ctk.CTkLabel(holder, text="没检测到游戏窗口，请先打开游戏再点「刷新」。",
                         font=self.fonts["body"], text_color=T.TEXT_DIM).pack(anchor="w", padx=16, pady=(2, 14))
            return
        for i, (w, r) in enumerate(data):
            row = ctk.CTkFrame(holder, fg_color=T.SURFACE_2, corner_radius=T.RADIUS_SM)
            row.pack(fill="x", padx=12, pady=4)
            row.grid_columnconfigure(0, weight=1)
            meta = f"号{i + 1}    {r[2]}×{r[3]}    @({r[0]},{r[1]})" if r else f"号{i + 1}    （窗口已失效）"
            is_base = bool(base and r and int(base[0]) == r[2] and int(base[1]) == r[3])
            ctk.CTkLabel(row, text=meta + ("   ✓ 当前基准" if is_base else ""),
                         font=self.fonts["body"],
                         text_color=T.SUCCESS if is_base else T.TEXT).grid(
                             row=0, column=0, sticky="w", padx=12, pady=8)
            ctk.CTkButton(row, text="设为基准", font=self.fonts["small"], width=84, height=30,
                          corner_radius=T.RADIUS_SM, fg_color="transparent", hover_color=T.BORDER, text_color=T.TEXT,
                          border_width=1, border_color=T.BORDER,
                          command=lambda w=w: self._set_base_from(w)).grid(row=0, column=1, padx=10)
        ctk.CTkFrame(holder, fg_color="transparent", height=6).pack()

    def _set_base_from(self, w):
        r = w.rect()
        if not r:
            self.app.toast("该窗口已失效，请点「刷新」")
            return
        cfg = cfg_mod.load_config()
        cfg.setdefault("targets", {})["base_size"] = [r[2], r[3]]
        cfg_mod.save_config(cfg)
        self.app.cfg = cfg
        self.app.toast(f"已设基准尺寸 {r[2]}×{r[3]}（点「还原尺寸」时其它号会归一化到这个大小）")
        self.refresh()

    def _open_team_calibrate(self):
        """打开组队标定（共享命名空间 teaming）。任何用到组队的任务都读这份配置。"""
        from .calibrate_dialog import CalibrateDialog
        try:
            CalibrateDialog(self.app, task_name="teaming", on_done=self.refresh)
        except Exception as e:
            self.app.toast(f"打开组队标定失败：{e}")


# ----------------------------------------------------------------------
# 日常一条龙 页面：勾选已有任务 + 调序，一次按顺序跑完
# ----------------------------------------------------------------------
class DailyPage(ctk.CTkFrame):
    """日常一条龙：只做串联——勾选哪些任务、按什么顺序跑，存 tasks.daily.steps。
    多开/单开与各任务的演练/实战、标定、参数全部沿用各自任务页，本页不另设这些开关。"""

    TASK_NAME = "daily"
    RUN_LABEL = "▶  开始一条龙"

    # 各子任务「就绪」所需的区域/模板（与各任务页保持一致；仅用于状态显示）
    _READY = {
        "escort": (["activity_list"],
                   ["escort_entry", "escort_join", "escort_silver", "escort_confirm", "escort_ongoing"]),
        "secret_realm": (["activity_list"],
                         ["sr_entry", "sr_join", "sr_select", "sr_continue",
                          "sr_challenge", "sr_enter_battle", "sr_leave"]),
    }

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.fonts = app.fonts
        self.runner = None
        self._steps = []          # [{"task","enabled"}]，有序

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        self._build_header()
        self._build_control()
        self._build_body()
        self.refresh()

    # ---- 头部 ----
    def _build_header(self):
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=4, pady=(2, 14))
        bar.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(bar, text="日常一条龙", font=self.fonts["title"], text_color=T.TEXT).grid(
            row=0, column=0, sticky="w")
        right = ctk.CTkFrame(bar, fg_color="transparent")
        right.grid(row=0, column=1, sticky="e")
        self.pill_game = Pill(right, self.fonts)
        self.pill_game.pack(side="left")
        sub = ctk.CTkLabel(bar, text="勾选要串起来跑的任务并调序，一次按顺序跑完；多开/单开与各任务的演练/实战、"
                                     "标定、参数全部沿用各自任务页设置",
                           font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left", anchor="w")
        sub.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        bind_wraplength(sub)

    # ---- 控制区：运行按钮 + 工具（选择窗口/刷新），无标定/无模式开关 ----
    def _build_control(self):
        card = Card(self)
        card.grid(row=1, column=0, sticky="ew", padx=4, pady=(0, 14))
        card.grid_columnconfigure(0, weight=1)

        top = ctk.CTkFrame(card, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 10))
        top.grid_columnconfigure(1, weight=1)
        self.btn_run = ctk.CTkButton(top, text=self.RUN_LABEL, font=self.fonts["btn"],
                                     height=46, width=200, corner_radius=T.RADIUS_SM,
                                     fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER, text_color=T.ON_ACCENT,
                                     command=self._toggle_run)
        self.btn_run.grid(row=0, column=0, sticky="w")
        tools = ctk.CTkFrame(top, fg_color="transparent")
        tools.grid(row=0, column=2, sticky="e")
        ctk.CTkButton(tools, text="选择窗口", font=self.fonts["body"], height=36, width=104,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=lambda: self.app.open_window_picker(self.refresh)).pack(side="left", padx=(0, 8))
        ctk.CTkButton(tools, text="刷新配置", font=self.fonts["body"], height=36, width=104,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self.refresh).pack(side="left")

        ctk.CTkFrame(card, fg_color=T.BORDER, height=1).grid(
            row=1, column=0, sticky="ew", padx=16, pady=(0, 4))

        # 时间上限（整条龙的安全网）
        opts = ctk.CTkFrame(card, fg_color="transparent")
        opts.grid(row=2, column=0, sticky="ew", padx=16, pady=(8, 16))
        lim = ctk.CTkFrame(opts, fg_color="transparent")
        lim.pack(anchor="w")
        ctk.CTkLabel(lim, text="整体时间上限(分钟，0=不限)", font=self.fonts["body"],
                     text_color=T.TEXT).pack(side="left")
        self.var_limit = ctk.StringVar(value="0")
        ctk.CTkEntry(lim, textvariable=self.var_limit, width=70, font=self.fonts["body"],
                     fg_color=T.SURFACE_2, border_color=T.BORDER).pack(side="left", padx=(8, 0))
        net = ctk.CTkLabel(opts, text="只是安全网：正常会按各子任务自身条件跑完。未就绪（缺标定/缺窗口）的任务会自动跳过。",
                     font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left")
        net.pack(fill="x", pady=(5, 0))
        bind_wraplength(net)

    # ---- 主体：左任务清单 + 右日志 ----
    def _build_body(self):
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=2, column=0, sticky="nsew", padx=4)
        body.grid_columnconfigure(0, weight=2, uniform="b")
        body.grid_columnconfigure(1, weight=3, uniform="b")
        body.grid_rowconfigure(0, weight=1)

        # 左：任务清单（勾选 + 调序）
        left = Card(body)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        left.grid_rowconfigure(1, weight=1)
        left.grid_columnconfigure(0, weight=1)
        head = ctk.CTkFrame(left, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 8))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text="任务清单（勾选 + 调序）", font=self.fonts["h2"],
                     text_color=T.TEXT).grid(row=0, column=0, sticky="w")
        self.lbl_count = ctk.CTkLabel(head, text="", font=self.fonts["small"], text_color=T.TEXT_DIM)
        self.lbl_count.grid(row=0, column=1, sticky="e")
        self.list_frame = ctk.CTkScrollableFrame(left, fg_color="transparent")
        self.list_frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 12))
        self.list_frame.grid_columnconfigure(0, weight=1)
        T.tune_scroll_speed(self.list_frame)

        # 右：日志
        right = Card(body)
        right.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        right.grid_rowconfigure(1, weight=1)
        right.grid_columnconfigure(0, weight=1)
        rhead = ctk.CTkFrame(right, fg_color="transparent")
        rhead.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 8))
        rhead.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(rhead, text="运行日志", font=self.fonts["h2"], text_color=T.TEXT).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(rhead, text="清空", font=self.fonts["small"], height=26, width=56,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self._clear_log).grid(row=0, column=1, sticky="e")
        self.log = ctk.CTkTextbox(right, font=self.fonts["mono"], fg_color=T.SURFACE_2,
                                  text_color=T.TEXT, corner_radius=T.RADIUS_SM, wrap="word")
        self.log.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        T.apply_log_tags(self.log._textbox)
        self.log.configure(state="disabled")
        self._log_line("界面就绪。勾选要串的任务并调序；各任务的标定/演练实战请到对应任务页设置。", "info")

    # ------------------------------------------------------------------
    # 刷新 / 渲染
    # ------------------------------------------------------------------
    def refresh(self):
        self.app.cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(self.app.cfg, self.TASK_NAME)
        self._steps = self._normalize(tc.get("steps", []))
        self.var_limit.set(str(tc.get("loop", {}).get("time_limit_min", 0)))
        self._render_steps()

    @staticmethod
    def _normalize(stored):
        """把存储的 steps 规整成「含全部可串联任务、保留已存顺序、缺的补到末尾」。"""
        out, seen = [], []
        for s in stored or []:
            if isinstance(s, dict) and s.get("task") in CHAINABLE and s["task"] not in seen:
                out.append({"task": s["task"], "enabled": bool(s.get("enabled", True))})
                seen.append(s["task"])
        for t in CHAINABLE:
            if t not in seen:
                out.append({"task": t, "enabled": True})
                seen.append(t)
        return out

    def _task_title(self, name):
        cls = get_task(name)
        return cls.title if cls else name

    def _task_status(self, name):
        """返回 (模式串, 是否已就绪)。模式=演练/实战；就绪=必要区域+模板都已标定。"""
        sub = cfg_mod.task_config(self.app.cfg, name)
        mode = "演练" if sub.get("dry_run", True) else "实战"
        if name == "treasure_map":
            skip = sub.get("skip_collect", False)
            need_r = ["bag_list"] if skip else ["activity_list", "bag_list"]
            need_t = (["flag_next_map", "treasure_item"] if skip else
                      ["flag_treasure_entry", "flag_join", "flag_tingting", "flag_next_map", "treasure_item"])
        else:
            need_r, need_t = self._READY.get(name, ([], []))
        regions = sub.get("regions", {})
        templates = sub.get("templates", {})
        ready = all(regions.get(k) for k in need_r) and all(templates.get(k) for k in need_t)
        return mode, ready

    def _render_steps(self):
        # 内容/各任务就绪状态没变就别重建：切页时 refresh 反复调到这里，整段重画是「切页卡顿」来源之一。
        # 状态串纳入签名——别处页面刚标定/切换演练实战，切回来时这里要能反映出最新状态。
        sig = [(s["task"], s["enabled"], self._task_status(s["task"])) for s in self._steps]
        if sig == getattr(self, "_steps_sig", None):
            return
        self._steps_sig = sig
        for w in self.list_frame.winfo_children():
            w.destroy()
        n_on = sum(1 for s in self._steps if s["enabled"])
        self.lbl_count.configure(text=f"已勾选 {n_on}/{len(self._steps)}")

        order = 0
        for i, step in enumerate(self._steps):
            name = step["task"]
            row = ctk.CTkFrame(self.list_frame, fg_color=T.SURFACE_2, corner_radius=T.RADIUS_SM)
            row.grid(row=i, column=0, sticky="ew", pady=4, padx=4)
            row.grid_columnconfigure(1, weight=1)

            var = ctk.BooleanVar(value=step["enabled"])
            seq = ""
            if step["enabled"]:
                order += 1
                seq = f"{order}. "
            # 第一行：勾选框（左，撑开） + ↑↓ 调序（右）
            cb = ctk.CTkCheckBox(row, text=seq + self._task_title(name), font=self.fonts["body_b"],
                                 variable=var, text_color=T.TEXT,
                                 fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER,
                                 command=lambda idx=i, v=var: self._toggle_step(idx, v))
            cb.grid(row=0, column=0, sticky="w", padx=(12, 8), pady=(10, 0))

            up = ctk.CTkButton(row, text="↑", font=self.fonts["body"], width=30, height=28,
                               corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER,
                               text_color=T.TEXT, border_width=1, border_color=T.BORDER,
                               command=lambda idx=i: self._move(idx, -1))
            up.grid(row=0, column=1, padx=(0, 4), pady=(8, 0))
            down = ctk.CTkButton(row, text="↓", font=self.fonts["body"], width=30, height=28,
                                 corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER,
                                 text_color=T.TEXT, border_width=1, border_color=T.BORDER,
                                 command=lambda idx=i: self._move(idx, 1))
            down.grid(row=0, column=2, padx=(0, 10), pady=(8, 0))
            if i == 0:
                up.configure(state="disabled")
            if i == len(self._steps) - 1:
                down.configure(state="disabled")

            # 第二行：状态单独占整行（sticky=ew + 自动换行），避免被窗口右缘/调序按钮挤窄切掉
            mode, ready = self._task_status(name)
            status = f"{mode} · " + ("✓ 已就绪" if ready else "⚠ 还需标定")
            slbl = ctk.CTkLabel(row, text=status, font=self.fonts["small"], anchor="w", justify="left",
                                text_color=T.SUCCESS if ready else T.WARN)
            slbl.grid(row=1, column=0, columnspan=3, sticky="ew", padx=(14, 10), pady=(2, 10))
            bind_wraplength(slbl)

    # ------------------------------------------------------------------
    # 勾选 / 调序 / 保存
    # ------------------------------------------------------------------
    def _toggle_step(self, idx, var):
        if 0 <= idx < len(self._steps):
            self._steps[idx]["enabled"] = bool(var.get())
            self._save()
            self._render_steps()

    def _move(self, idx, delta):
        j = idx + delta
        if 0 <= idx < len(self._steps) and 0 <= j < len(self._steps):
            self._steps[idx], self._steps[j] = self._steps[j], self._steps[idx]
            self._save()
            self._render_steps()

    def _save(self):
        """把当前勾选/顺序/时间上限写回配置（读盘再改，避免覆盖别处刚写入的配置）。"""
        cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(cfg, self.TASK_NAME)
        tc["steps"] = [{"task": s["task"], "enabled": bool(s["enabled"])} for s in self._steps]
        loopc = tc.setdefault("loop", {})
        try:
            loopc["time_limit_min"] = max(0.0, round(float(self.var_limit.get()), 1))
        except (TypeError, ValueError):
            pass
        cfg_mod.set_task_config(cfg, self.TASK_NAME, tc)
        cfg_mod.save_config(cfg)
        self.app.cfg = cfg

    # ------------------------------------------------------------------
    # 运行控制
    # ------------------------------------------------------------------
    def _toggle_run(self):
        if self.runner and self.runner.is_running():
            self.runner.stop()
            self._log_line("正在停止…", "warn")
            self.btn_run.configure(text="停止中…", state="disabled")
            return
        self._save()
        self.app.cfg = cfg_mod.load_config()
        task_cls = get_task(self.TASK_NAME)
        self.runner = TaskRunner(task_cls(), self.app.cfg)
        ok, problems = self.runner.start()
        if not ok:
            for p in problems:
                self._log_line("无法启动：" + p, "error")
            self.runner = None
            return
        self.btn_run.configure(text="■  停止", fg_color=T.DANGER, hover_color=T.DANGER_HOVER, state="normal")

    def _on_runner_finished(self):
        self.btn_run.configure(text=self.RUN_LABEL, fg_color=T.ACCENT,
                               hover_color=T.ACCENT_HOVER, state="normal")

    def update_game_pill(self, connected, summary=""):
        if connected:
            self.pill_game.configure(text="● " + (summary or "目标窗口已连接"),
                                     fg_color=T.PILL_OK_BG, text_color=T.SUCCESS)
        else:
            self.pill_game.configure(text="○ 未检测到目标窗口", fg_color=T.SURFACE_2, text_color=T.TEXT_DIM)

    # ---- 日志（由 App._tick 驱动）----
    def pump(self):
        if self.runner:
            q = self.runner.log_queue
            while not q.empty():
                level, msg = q.get()
                self._log_line(msg, level)
            if not self.runner.is_running() and self.btn_run.cget("text") != self.RUN_LABEL:
                self._on_runner_finished()

    def _log_line(self, msg, level="info"):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self.log.configure(state="normal")
        try:
            self.log._textbox.insert("end", f"[{ts}] {msg}\n", level)
        except Exception:
            self.log.insert("end", f"[{ts}] {msg}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")


# ----------------------------------------------------------------------
# 主窗口
# ----------------------------------------------------------------------
class App(ctk.CTk):
    NAV = [("general", "🧰  通用 / 工具"),
           ("daily", "🐉  日常一条龙"),
           ("sniper", "🗡  秒装备"), ("treasure_map", "🗺  刷副本·宝图"),
           ("escort", "🚚  运镖"), ("secret_realm", "👹  秘境降妖"),
           ("dungeon", "🏰  刷副本"),
           ("settings", "⚙  设置"), ("about", "ⓘ  关于")]
    # 可运行任务页（有 runner/pump/update_game_pill），App 的定时器/热键/关闭钩子按此遍历
    RUNNABLE_KEYS = ("daily", "sniper", "treasure_map", "escort", "secret_realm", "dungeon")

    def __init__(self):
        super().__init__()
        self.cfg = cfg_mod.load_config()
        mode = self.cfg.get("appearance", "dark")
        ctk.set_appearance_mode(mode if mode in ("dark", "light") else "dark")
        self.title("梦幻 · 时空 助手")
        self.geometry("1020x680")
        self.minsize(940, 620)
        self.configure(fg_color=T.BG)

        self.fonts = T.build_fonts()
        self.game_win = win_mod.GameWindow(self.cfg.get("window_title", "梦幻西游"))
        self._tick_count = 0
        self._game_connected = None   # 缓存连接状态，只在变化时刷新药丸
        self._locating = False        # 防止多个后台定位线程叠加

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._build_sidebar()
        self._build_pages()
        self._show("general")

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(150, self._tick)
        self._hotkey_down = False
        self.after(60, self._poll_hotkey)
        # 界面显示后趁空闲把其余页面逐个预建好，首次切过去即秒开（每个间隔开，单帧不卡）。
        self.after(800, self._prebuild_idle)

    def _build_sidebar(self):
        bar = ctk.CTkFrame(self, fg_color=T.SIDEBAR, corner_radius=0, width=210)
        bar.grid(row=0, column=0, sticky="nsew")
        bar.grid_propagate(False)
        bar.grid_rowconfigure(99, weight=1)

        ctk.CTkLabel(bar, text="梦幻 · 时空", font=self.fonts["title"], text_color=T.TEXT).grid(
            row=0, column=0, sticky="w", padx=22, pady=(24, 0))
        ctk.CTkLabel(bar, text="辅助助手", font=self.fonts["small"], text_color=T.TEXT_DIM).grid(
            row=1, column=0, sticky="w", padx=22, pady=(0, 22))

        self.nav_buttons = {}
        for i, (key, label) in enumerate(self.NAV):
            b = ctk.CTkButton(bar, text=label, font=self.fonts["nav"], anchor="w",
                              height=42, corner_radius=T.RADIUS_SM,
                              fg_color="transparent", hover_color=T.SURFACE,
                              text_color=T.TEXT_DIM, command=lambda k=key: self._show(k))
            b.grid(row=2 + i, column=0, sticky="ew", padx=12, pady=3)
            self.nav_buttons[key] = b

        # 明暗切换按钮（置于风险提示之上，随侧栏底部对齐）
        self.btn_appearance = ctk.CTkButton(
            bar, text="", font=self.fonts["nav"], anchor="w", height=42,
            corner_radius=T.RADIUS_SM, fg_color="transparent", hover_color=T.SURFACE,
            text_color=T.TEXT_DIM, command=self._toggle_appearance)
        self.btn_appearance.grid(row=100, column=0, sticky="ew", padx=12, pady=(8, 4))
        self._render_appearance_btn()

        ctk.CTkLabel(bar, text="⚠ 脚本有封号风险\n请用小号测试", font=self.fonts["small"],
                     text_color=T.WARN, justify="left").grid(row=101, column=0, sticky="sw",
                                                             padx=22, pady=18)

    # 各页面对应的类。懒加载：启动只建默认页，其余等第一次切到才建——
    # 一次性建全部 9 个页面会瞬间绘制几百个 CTk 画布控件，正是启动「一块块慢慢刷出来」的根因。
    PAGE_CLASSES = {
        "daily": DailyPage,
        "sniper": SniperPage,
        "treasure_map": TreasureMapPage,
        "escort": EscortPage,
        "secret_realm": SecretRealmPage,
        "dungeon": DungeonPage,
        "general": GeneralPage,
        "settings": SettingsPage,
        "about": AboutPage,
    }

    def _build_pages(self):
        self.container = ctk.CTkFrame(self, fg_color="transparent")
        self.container.grid(row=0, column=1, sticky="nsew", padx=24, pady=20)
        self.container.grid_rowconfigure(0, weight=1)
        self.container.grid_columnconfigure(0, weight=1)
        self.pages = {}   # 懒加载：key -> 页面实例，按需创建

    def _ensure_page(self, key):
        """返回页面实例，不存在则即时创建（懒加载）。返回 (page, just_created)。"""
        p = self.pages.get(key)
        if p is not None:
            return p, False
        p = self.PAGE_CLASSES[key](self.container, self)
        p.grid(row=0, column=0, sticky="nsew")
        self.pages[key] = p
        # 新建的可运行页要补一次游戏连接状态（药丸初值为空，否则要等下一轮 tick 才更新）
        if self._game_connected and hasattr(p, "update_game_pill"):
            found, summary = self._game_connected
            p.update_game_pill(found, summary)
        return p, True

    def build_all_pages(self, on_step=None):
        """一次性把所有页面都建好（建完再亮窗口，杜绝「出现后才逐页卡」）。
        每建好一页回调一次 on_step——用它驱动遮罩上的进度条转动，让构建期也有动画。"""
        for key in self.PAGE_CLASSES:
            if key not in self.pages:
                self._ensure_page(key)
                if callable(on_step):
                    try:
                        on_step()
                    except Exception:
                        pass
        cur = getattr(self, "_current_key", None)
        if cur in self.pages:
            self.pages[cur].tkraise()

    def _safe_update(self):
        try:
            self.update()
        except Exception:
            pass

    def reveal_with_overlay(self):
        """在本窗口上盖一层全屏「正在准备界面…」遮罩，遮罩后把其余页面全部建好，再撤遮罩。
        全程只用本窗口这一个 Tk 根（不再开第二个根），既避免双根崩溃，又盖住建页面的卡顿。

        遮罩是 App 的直接子组件、最后创建，叠在 container（含所有页面）与侧栏之上；各页面建在
        container 里，层级在遮罩之下，故新建页面不会盖穿遮罩。建页期间用 update() 让进度条转动。"""
        import tkinter as tk
        from tkinter import ttk

        bg, fg, dim, acc, trough = (T.resolve(T.BG), T.resolve(T.TEXT), T.resolve(T.TEXT_DIM),
                                    T.resolve(T.ACCENT), T.resolve(T.SURFACE_2))
        ov = tk.Frame(self, bg=bg)
        ov.place(x=0, y=0, relwidth=1, relheight=1)
        tk.Label(ov, text="梦幻 · 时空 助手", bg=bg, fg=fg,
                 font=("Microsoft YaHei UI", 16, "bold")).place(relx=0.5, rely=0.43, anchor="center")
        tk.Label(ov, text="正在准备界面…", bg=bg, fg=dim,
                 font=("Microsoft YaHei UI", 11)).place(relx=0.5, rely=0.51, anchor="center")
        try:
            style = ttk.Style(self)
            style.theme_use("default")
            style.configure("Ovl.Horizontal.TProgressbar", troughcolor=trough,
                            background=acc, bordercolor=bg, lightcolor=acc, darkcolor=acc)
            pb = ttk.Progressbar(ov, mode="indeterminate", length=240,
                                 style="Ovl.Horizontal.TProgressbar")
            pb.place(relx=0.5, rely=0.59, anchor="center")
            pb.start(12)
        except Exception:
            pass
        self._safe_update()                 # 先把遮罩画出来（盖住未完成的界面）
        self.build_all_pages(on_step=self._safe_update)
        try:
            ov.destroy()                    # 撤遮罩，露出已就绪的界面
        except Exception:
            pass
        self._safe_update()

    def _prebuild_idle(self):
        """启动后趁空闲逐个把尚未创建的页面建好；每次只建一个并重排一次调度，避免单帧卡顿。"""
        for key in self.PAGE_CLASSES:
            if key not in self.pages:
                self._ensure_page(key)
                # 新建的页默认叠在最上、会盖住当前可见页，立刻把当前页重新置顶。
                cur = getattr(self, "_current_key", None)
                if cur in self.pages:
                    self.pages[cur].tkraise()
                self.after(120, self._prebuild_idle)
                return
        # 全部建完，停止调度。

    def _show(self, key):
        self._current_key = key   # 记当前可见页，全局热键只控它
        page, _just_created = self._ensure_page(key)
        page.tkraise()
        # 总是 refresh：部分页（如通用页）刻意把内容填充放在 refresh 里、__init__ 不填，靠这里驱动。
        # 重复 refresh 的代价已被各页的「内容签名守卫」摊薄（数据没变就不重画列表）。
        if hasattr(page, "refresh"):
            page.refresh()
        for k, b in self.nav_buttons.items():
            if k == key:
                b.configure(fg_color=T.SURFACE, text_color=T.TEXT)
            else:
                b.configure(fg_color="transparent", text_color=T.TEXT_DIM)

    def _render_appearance_btn(self):
        """按当前外观刷新切换按钮文案：夜间显示「🌙 夜间」、白天显示「☀ 白天」。"""
        if ctk.get_appearance_mode() == "Light":
            self.btn_appearance.configure(text="☀  白天模式")
        else:
            self.btn_appearance.configure(text="🌙  夜间模式")

    def _toggle_appearance(self):
        """在夜间/白天之间切换，写回配置，并补刷不随外观自动变的部分（日志级别色）。"""
        new = "light" if ctk.get_appearance_mode() == "Dark" else "dark"
        ctk.set_appearance_mode(new)
        self._render_appearance_btn()
        # 写回配置（读盘再改，避免覆盖别处刚写入的配置）
        cfg = cfg_mod.load_config()
        cfg["appearance"] = new
        cfg_mod.save_config(cfg)
        self.cfg = cfg
        # 日志框走底层 tk tag_config，不随 set_appearance_mode 自动变，逐页重刷
        for k in self.RUNNABLE_KEYS:
            p = self.pages.get(k)
            log = getattr(p, "log", None) if p else None
            if log is not None:
                try:
                    T.apply_log_tags(log._textbox)
                except Exception:
                    pass

    def toast(self, msg):
        """简单的右下角浮层提示。"""
        lbl = ctk.CTkLabel(self, text=msg, font=self.fonts["body"], fg_color=T.ACCENT,
                           text_color=T.ON_ACCENT, corner_radius=T.RADIUS_SM, padx=16, pady=8)
        lbl.place(relx=0.99, rely=0.97, anchor="se")
        self.after(1600, lbl.destroy)

    def _tick(self):
        # 抽日志：所有可运行任务页
        for k in self.RUNNABLE_KEYS:
            p = self.pages.get(k)
            if p:
                p.pump()
        # 每约 1.2s 检测一次游戏窗口（放后台线程，避免阻塞 UI 造成滑动卡顿）
        self._tick_count += 1
        if self._tick_count % 8 == 0:
            self._kick_locate()
        self.after(150, self._tick)

    def _kick_locate(self):
        """在后台线程枚举窗口找游戏；getAllWindows 较慢，绝不能在主线程跑。"""
        if self._locating:
            return
        self._locating = True
        title = self.cfg.get("window_title", "梦幻西游")
        offset = self.cfg.get("window_offset", [0, 0])
        targets = self.cfg.get("targets", {})

        def work():
            try:
                all_wins = win_mod.locate_all(title, offset)
                found, summary = self._compute_target_state(all_wins, targets)
            except Exception:
                found, summary = False, ""
            # 回主线程更新（after 由 Tk 在主线程执行，线程安全）
            try:
                self.after(0, lambda: self._apply_game_state(found, summary))
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def _compute_target_state(all_wins, targets):
        """据「已枚举的窗口 + targets 选择」算出药丸要显示的 (是否连上, 摘要串)。
        纯函数，跑在后台线程，不碰 Tk。"""
        if not all_wins:
            return False, ""
        if targets.get("multi"):
            idxs = targets.get("multi_indices") or list(range(len(all_wins)))
            sel = [i for i in idxs if 0 <= i < len(all_wins)]
            n = len(sel) if sel else len(all_wins)
            return True, f"{n} 号 · 多开"
        i = targets.get("single_index", 0)
        if not (isinstance(i, int) and 0 <= i < len(all_wins)):
            i = 0
        return True, f"号{i + 1} · 单开"

    def _apply_game_state(self, found, summary=""):
        self._locating = False
        state = (found, summary)
        if state == self._game_connected:
            return  # 状态没变就不动控件，省掉无谓重绘
        self._game_connected = state
        for k in self.RUNNABLE_KEYS:
            p = self.pages.get(k)
            if p:
                p.update_game_pill(found, summary)

    def open_window_picker(self, after=None):
        """打开「选择窗口」对话框（各任务页共用）。关闭后刷新配置并强制刷新药丸。"""
        from .window_picker import WindowPickerDialog

        def _done():
            self.cfg = cfg_mod.load_config()
            self._game_connected = None   # 选择可能变了，强制下次 tick 刷新药丸
            if callable(after):
                try:
                    after()
                except Exception:
                    pass

        try:
            WindowPickerDialog(self, on_done=_done)
        except Exception:
            pass

    def restore_window_size(self, after=None):
        """把选中的号窗口还原到标定时记录的基准尺寸（被手操拉大后一键复位）。"""
        targets = self.cfg.get("targets", {})
        base = targets.get("base_size")
        if not base or len(base) < 2:
            self.toast("请先标定一次，标定时会自动记录基准尺寸")
            return
        title = self.cfg.get("window_title", "梦幻西游")
        offset = self.cfg.get("window_offset", [0, 0])
        ok, total, actual = win_mod.restore_targets_size(title, offset, targets, base)
        if total == 0:
            self.toast(f"没找到/没选中目标窗口（标题含「{title}」），请先「选择窗口」")
            return
        w, h = int(base[0]), int(base[1])
        if ok == total:
            self.toast(f"已还原 {ok}/{total} 个号到 {w}×{h}")
        else:
            # 有号没还原成功——多半是游戏锁了分辨率档位，resize 被忽略
            self.toast(f"还原 {ok}/{total} 个号；部分窗口可能不支持自由缩放")
        self._game_connected = None   # 尺寸变了，强制下次 tick 刷新药丸
        if callable(after):
            try:
                after()
            except Exception:
                pass

    # ---- 全局快捷键轮询：上升沿触发开始/停止 ----
    def _poll_hotkey(self):
        name = self.cfg.get("hotkey_toggle", "F5")
        vk = HOTKEY_VK.get(name)
        if vk is not None:
            down = _vk_down(vk)
            if down and not self._hotkey_down:   # 按下瞬间触发一次
                self._trigger_hotkey(name)
            self._hotkey_down = down
        else:
            self._hotkey_down = False
        self.after(60, self._poll_hotkey)

    def _trigger_hotkey(self, name):
        # 全局热键只作用于「当前可见」的可运行任务页，避免同时启停多个任务
        key = getattr(self, "_current_key", "sniper")
        page = self.pages.get(key)
        if page is None or not hasattr(page, "_toggle_run"):
            return
        was_running = bool(getattr(page, "runner", None) and page.runner.is_running())
        page._toggle_run()
        self.toast(f"[{name}] {'已停止' if was_running else '已开始'}")

    def _on_close(self):
        for k in self.RUNNABLE_KEYS:
            p = self.pages.get(k)
            if p and getattr(p, "runner", None) and p.runner.is_running():
                p.runner.stop()
        self.destroy()


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
