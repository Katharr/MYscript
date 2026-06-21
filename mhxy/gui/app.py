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
    lbl = ctk.CTkLabel(master, text="", font=fonts["small"], corner_radius=20,
                       fg_color=T.SURFACE_2, text_color=T.TEXT_DIM,
                       padx=12, pady=4)
    return lbl


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

        left = ctk.CTkFrame(bar, fg_color="transparent")
        left.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(left, text="秒装备", font=self.fonts["title"], text_color=T.TEXT).pack(anchor="w")
        ctk.CTkLabel(left, text="盯市场列表，目标装备一出现立刻秒下单",
                     font=self.fonts["small"], text_color=T.TEXT_DIM).pack(anchor="w", pady=(2, 0))

        right = ctk.CTkFrame(bar, fg_color="transparent")
        right.grid(row=0, column=1, sticky="e")
        self.pill_game = Pill(right, self.fonts)
        self.pill_game.pack(side="left", padx=(0, 8))
        self.pill_mode = Pill(right, self.fonts)
        self.pill_mode.pack(side="left")

    # ---- 控制区：开始/停止 + 模式开关 ----
    def _build_control(self):
        card = Card(self)
        card.grid(row=1, column=0, sticky="ew", padx=4, pady=(0, 14))
        card.grid_columnconfigure(1, weight=1)

        self.btn_run = ctk.CTkButton(card, text="▶  开始秒装备", font=self.fonts["btn"],
                                     height=46, width=180, corner_radius=T.RADIUS_SM,
                                     fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER,
                                     command=self._toggle_run)
        self.btn_run.grid(row=0, column=0, padx=18, pady=18)

        mid = ctk.CTkFrame(card, fg_color="transparent")
        mid.grid(row=0, column=1, sticky="w", padx=8)
        self.switch_mode = ctk.CTkSwitch(mid, text="实战模式（命中会真买）", font=self.fonts["body"],
                                         progress_color=T.DANGER, command=self._toggle_mode)
        self.switch_mode.pack(anchor="w")
        ctk.CTkLabel(mid, text="关 = 演练（只识别不下单，安全）",
                     font=self.fonts["small"], text_color=T.TEXT_DIM).pack(anchor="w", pady=(4, 0))

        tools = ctk.CTkFrame(card, fg_color="transparent")
        tools.grid(row=0, column=2, padx=18)
        ctk.CTkButton(tools, text="标定 / 加装备", font=self.fonts["body"], height=34, width=120,
                      corner_radius=T.RADIUS_SM, fg_color=T.SURFACE_2, hover_color=T.BORDER,
                      command=self._open_calibrate).pack(pady=(0, 6))
        ctk.CTkButton(tools, text="刷新配置", font=self.fonts["body"], height=34, width=120,
                      corner_radius=T.RADIUS_SM, fg_color=T.SURFACE_2, hover_color=T.BORDER,
                      command=self.refresh).pack()

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
                      corner_radius=T.RADIUS_SM, fg_color=T.SURFACE_2, hover_color=T.BORDER,
                      command=self._clear_log).grid(row=0, column=1, sticky="e")
        self.log = ctk.CTkTextbox(right, font=self.fonts["mono"], fg_color=T.SURFACE_2,
                                  text_color=T.TEXT, corner_radius=T.RADIUS_SM, wrap="word")
        self.log.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        for lvl, color in T.LEVEL_COLOR.items():
            try:
                self.log._textbox.tag_config(lvl, foreground=color)
            except Exception:
                pass
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
        for w in self.list_frame.winfo_children():
            w.destroy()
        self._thumbs.clear()
        self.lbl_count.configure(text=f"{len(items)} 件")

        if not items:
            ctk.CTkLabel(self.list_frame, text="还没有要抢的装备。\n点上方「标定 / 加装备」添加。",
                         font=self.fonts["body"], text_color=T.TEXT_DIM, justify="left").grid(
                row=0, column=0, sticky="w", padx=12, pady=20)
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
                          corner_radius=T.RADIUS_SM, fg_color="transparent", hover_color=T.DANGER,
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
            self.pill_mode.configure(text="演练", fg_color="#1d3a2b", text_color=T.SUCCESS)
        else:
            self.pill_mode.configure(text="实战", fg_color="#3a1d1d", text_color=T.DANGER)

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
            self._cal_dialog = CalibrateDialog(self.app, on_done=_after)
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

    def update_game_pill(self, connected):
        if connected:
            self.pill_game.configure(text="● 游戏已连接", fg_color="#15301f", text_color=T.SUCCESS)
        else:
            self.pill_game.configure(text="○ 未检测到游戏", fg_color=T.SURFACE_2, text_color=T.TEXT_DIM)

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
        ("humanize", "px_per_step", "鼠标移动步长(px)", 6, 40, 34, 0,
         "每步移动的像素。越大步数越少→移动越快，但轨迹越不平滑(略更像机器)。"),
        ("loop", "shelf_load_wait_sec", "货架加载等待(秒)", 0.2, 3.0, 28, 2,
         "点完类别+商品后等货架刷出的时间。这是每轮最大的固定耗时——网快可调小，但太小会没加载完就截图漏识别。"),
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
                      corner_radius=T.RADIUS_SM, fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER,
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
                                    button_color=T.BORDER, button_hover_color=T.ACCENT, width=240))
        self._row(card, 3, "开始/停止 快捷键", self._build_hotkey(card))
        self._row(card, 4, "识别置信度（匹配阈值）", self._build_threshold(card))

    def _build_hotkey(self, parent):
        box = ctk.CTkFrame(parent, fg_color="transparent")
        ctk.CTkOptionMenu(box, variable=self.var_hotkey, values=HOTKEY_NAMES,
                          font=self.fonts["body"], fg_color=T.SURFACE_2,
                          button_color=T.BORDER, button_hover_color=T.ACCENT,
                          width=240).pack(anchor="w")
        ctk.CTkLabel(box, text="全局热键：游戏在前台也能按。鼠标被脚本拉着失控时，按一下立刻停。改完记得保存。",
                     font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left",
                     wraplength=300).pack(anchor="w", pady=(4, 0))
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
        ctk.CTkLabel(head, text="抢不过别人就往「快」调；但越快越规律越像机器、封号风险越高。先在演练模式下试。",
                     font=self.fonts["small"], text_color=T.WARN, justify="left",
                     wraplength=640).pack(anchor="w", pady=(2, 0))

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
        ctk.CTkLabel(box, text=hint, font=self.fonts["small"], text_color=T.TEXT_DIM,
                     justify="left", wraplength=620).grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(4, 0))

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

    def _row(self, parent, r, label, widget):
        ctk.CTkLabel(parent, text=label, font=self.fonts["body"], text_color=T.TEXT).grid(
            row=r, column=0, sticky="w", padx=16, pady=12)
        widget.grid(row=r, column=1, sticky="w", padx=16, pady=12)

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
        ctk.CTkLabel(box, text="越高越严格：命中更准但可能漏；越低越宽松：易命中但可能误认。建议 0.85~0.92。",
                     font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left",
                     wraplength=300).pack(anchor="w", pady=(4, 0))
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
        ctk.CTkLabel(card, text=text, font=self.fonts["body"], text_color=T.TEXT,
                     justify="left", wraplength=640).pack(anchor="w", padx=18, pady=18)


# ----------------------------------------------------------------------
# 主窗口
# ----------------------------------------------------------------------
class App(ctk.CTk):
    NAV = [("sniper", "🗡  秒装备"), ("settings", "⚙  设置"), ("about", "ⓘ  关于")]

    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        self.title("梦幻 · 时空 助手")
        self.geometry("1020x680")
        self.minsize(940, 620)
        self.configure(fg_color=T.BG)

        self.fonts = T.build_fonts()
        self.cfg = cfg_mod.load_config()
        self.game_win = win_mod.GameWindow(self.cfg.get("window_title", "梦幻西游"))
        self._tick_count = 0
        self._game_connected = None   # 缓存连接状态，只在变化时刷新药丸
        self._locating = False        # 防止多个后台定位线程叠加

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._build_sidebar()
        self._build_pages()
        self._show("sniper")

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(150, self._tick)
        self._hotkey_down = False
        self.after(60, self._poll_hotkey)

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

        ctk.CTkLabel(bar, text="⚠ 脚本有封号风险\n请用小号测试", font=self.fonts["small"],
                     text_color=T.WARN, justify="left").grid(row=100, column=0, sticky="sw",
                                                             padx=22, pady=18)

    def _build_pages(self):
        self.container = ctk.CTkFrame(self, fg_color="transparent")
        self.container.grid(row=0, column=1, sticky="nsew", padx=24, pady=20)
        self.container.grid_rowconfigure(0, weight=1)
        self.container.grid_columnconfigure(0, weight=1)

        self.pages = {
            "sniper": SniperPage(self.container, self),
            "settings": SettingsPage(self.container, self),
            "about": AboutPage(self.container, self),
        }
        for p in self.pages.values():
            p.grid(row=0, column=0, sticky="nsew")

    def _show(self, key):
        self.pages[key].tkraise()
        if hasattr(self.pages[key], "refresh"):
            self.pages[key].refresh()
        for k, b in self.nav_buttons.items():
            if k == key:
                b.configure(fg_color=T.SURFACE, text_color=T.TEXT)
            else:
                b.configure(fg_color="transparent", text_color=T.TEXT_DIM)

    def toast(self, msg):
        """简单的右下角浮层提示。"""
        lbl = ctk.CTkLabel(self, text=msg, font=self.fonts["body"], fg_color=T.ACCENT,
                           text_color="white", corner_radius=T.RADIUS_SM, padx=16, pady=8)
        lbl.place(relx=0.99, rely=0.97, anchor="se")
        self.after(1600, lbl.destroy)

    def _tick(self):
        # 抽日志（仅秒装备页有 runner）
        sniper = self.pages.get("sniper")
        if sniper:
            sniper.pump()
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

        def work():
            try:
                self.game_win.title_substr = title
                found = self.game_win.locate()
            except Exception:
                found = False
            # 回主线程更新（after 由 Tk 在主线程执行，线程安全）
            try:
                self.after(0, lambda: self._apply_game_state(found))
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

    def _apply_game_state(self, found):
        self._locating = False
        if found == self._game_connected:
            return  # 状态没变就不动控件，省掉无谓重绘
        self._game_connected = found
        sniper = self.pages.get("sniper")
        if sniper:
            sniper.update_game_pill(found)

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
        sniper = self.pages.get("sniper")
        if not sniper:
            return
        was_running = bool(sniper.runner and sniper.runner.is_running())
        sniper._toggle_run()
        self.toast(f"[{name}] {'已停止' if was_running else '已开始'}")

    def _on_close(self):
        sniper = self.pages.get("sniper")
        if sniper and sniper.runner and sniper.runner.is_running():
            sniper.runner.stop()
        self.destroy()


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
