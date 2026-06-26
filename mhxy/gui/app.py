# -*- coding: utf-8 -*-
"""
主界面。深色简约风，左侧导航 + 中间分页 + 右侧常驻全局日志。
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
from ..tasks.base import dungeon_tasks
from ..tasks.daily import CHAINABLE
from ..core.teaming import TEAM_REQUIRED_REGIONS, TEAM_REQUIRED_TEMPLATES
from ..core.inventory import required_templates
from ..core.input import get_cursor


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

# 全局【急停】热键 = 若干修饰键 + 一个主键（上面的功能键），存成 "ctrl+alt+F12" 这样的字符串。
# 默认 Ctrl+Alt+F12：组合键比单键更不易和游戏内操作误撞，按一下立刻停止一切正在跑的任务。
MODIFIER_VK = {"ctrl": 0x11, "alt": 0x12, "shift": 0x10}
DEFAULT_STOP_HOTKEY = "ctrl+alt+F12"


def _parse_stop_hotkey(s):
    """'ctrl+alt+F12' -> (修饰键VK列表, 主键VK)。主键缺失/不认得返回 ([], None)。修饰键名不区分大小写。"""
    mods, key_vk = [], None
    for part in str(s or "").split("+"):
        p = part.strip()
        if not p:
            continue
        low = p.lower()
        if low in MODIFIER_VK:
            mods.append(MODIFIER_VK[low])
            continue
        for name, vk in HOTKEY_VK.items():
            if name.lower() == low:
                key_vk = vk
                break
    return mods, key_vk


def _compose_stop_hotkey(ctrl, alt, shift, key):
    """(ctrl,alt,shift 勾选 + 主键名) -> 'ctrl+alt+F12' 字符串（修饰键在前、主键在后）。"""
    parts = []
    if ctrl:
        parts.append("ctrl")
    if alt:
        parts.append("alt")
    if shift:
        parts.append("shift")
    parts.append(key)
    return "+".join(parts)


def _split_stop_hotkey(s):
    """'ctrl+alt+F12' -> (ctrl:bool, alt:bool, shift:bool, 主键名)，供设置 UI 回填；主键认不得回退 F12。"""
    mods, key_vk = _parse_stop_hotkey(s)
    key = "F12"
    for name, vk in HOTKEY_VK.items():
        if vk == key_vk:
            key = name
            break
    return (MODIFIER_VK["ctrl"] in mods, MODIFIER_VK["alt"] in mods,
            MODIFIER_VK["shift"] in mods, key)


# 失控急停：任务运行时把鼠标甩到屏幕某个角(撞到角落)即停。内部值 <-> 中文显示，off=关闭。
FAILSAFE_CORNERS = {
    "top_right": "右上角", "top_left": "左上角",
    "bottom_right": "右下角", "bottom_left": "左下角", "off": "关闭",
}
FAILSAFE_LABELS = list(FAILSAFE_CORNERS.values())
FAILSAFE_VALUE_OF = {v: k for k, v in FAILSAFE_CORNERS.items()}
DEFAULT_FAILSAFE = "top_right"


def _in_failsafe_corner(cx, cy, corner, margin=3):
    """光标 (cx,cy) 是否撞到所选屏幕角（off/未知一律 False）。用主屏像素尺寸判断。"""
    if corner == "off" or cx is None or cy is None:
        return False
    try:
        u = ctypes.windll.user32
        W, H = u.GetSystemMetrics(0), u.GetSystemMetrics(1)   # SM_CXSCREEN / SM_CYSCREEN
    except Exception:
        return False
    left, right = cx <= margin, cx >= W - 1 - margin
    top, bottom = cy <= margin, cy >= H - 1 - margin
    return {
        "top_left": left and top, "top_right": right and top,
        "bottom_left": left and bottom, "bottom_right": right and bottom,
    }.get(corner, False)


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


def load_thumb(template_rel, thumbs_list, max_h=40):
    """把模板图按高缩放成缩略图 CTkImage，引用 append 进 thumbs_list 防 GC，兼容中文/打包路径。
    不存在/失败返回 None。多页复用（SniperPage 清单、队长ID 库与行内按钮都调它）。"""
    if not template_rel:
        return None
    try:
        from PIL import Image
        path = template_rel if os.path.isabs(template_rel) else str(cfg_mod.PROJECT_ROOT / template_rel)
        if not os.path.exists(path):
            return None
        try:
            img = Image.open(path)
            img.load()
        except Exception:
            # 中文/异常路径兜底：走 cv2 imdecode（np.fromfile，兼容中文路径）读出再转回 PIL。
            # watchlist 装备图是 templates/<中文名>.png，个别环境 PIL.open 不保险，缺这条会“有图却显示不出”。
            import cv2
            from ..core import vision
            arr = vision.load_template(template_rel)
            if arr is None:
                return None
            img = Image.fromarray(cv2.cvtColor(arr, cv2.COLOR_BGR2RGB))
        w, h = img.size
        scale = max_h / max(1, h)
        size = (max(1, int(w * scale)), max_h)
        cimg = ctk.CTkImage(light_image=img, dark_image=img, size=size)
        thumbs_list.append(cimg)
        return cimg
    except Exception:
        return None


# ----------------------------------------------------------------------
# 秒装备页面
# ----------------------------------------------------------------------
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

    # ---- 主体：监控清单（铺满；日志已移到全局右栏）----
    def _build_body(self):
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=2, column=0, sticky="nsew", padx=4)
        body.grid_columnconfigure(0, weight=1)   # 日志已移到全局右栏，主体内容独占整宽
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

        # 日志已统一到 App 右侧的全局日志面板，本页不再单独建日志框。

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
        # 逻辑已抽成模块级 load_thumb（多页共享）；保留本方法名兼容现有调用。
        return load_thumb(template_rel, self._thumbs)

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
        # 日志统一汇到 App 右侧全局面板，按本页 LOG_SOURCE 打来源标签。
        self.app.log_line(msg, level, getattr(self, "LOG_SOURCE", None))

    def _clear_log(self):
        self.app.clear_log()


# ----------------------------------------------------------------------
# 刷副本·宝图 页面
# ----------------------------------------------------------------------
class TreasureMapPage(ctk.CTkFrame):
    TASK_NAME = "treasure_map"
    LOG_SOURCE = "宝图"
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
        body.grid_columnconfigure(0, weight=1)   # 日志已移到全局右栏，主体内容独占整宽
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

        # 日志已统一到 App 右侧的全局日志面板，本页不再单独建日志框。

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
        # 日志统一汇到 App 右侧全局面板，按本页 LOG_SOURCE 打来源标签。
        self.app.log_line(msg, level, getattr(self, "LOG_SOURCE", None))

    def _clear_log(self):
        self.app.clear_log()


# ----------------------------------------------------------------------
# 运镖 页面
# ----------------------------------------------------------------------
class EscortPage(ctk.CTkFrame):
    TASK_NAME = "escort"
    LOG_SOURCE = "运镖"
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
        body.grid_columnconfigure(0, weight=1)   # 日志已移到全局右栏，主体内容独占整宽
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

        # 日志已统一到 App 右侧的全局日志面板，本页不再单独建日志框。

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
        # 日志统一汇到 App 右侧全局面板，按本页 LOG_SOURCE 打来源标签。
        self.app.log_line(msg, level, getattr(self, "LOG_SOURCE", None))

    def _clear_log(self):
        self.app.clear_log()


# ----------------------------------------------------------------------
# 刷副本 页面（副本中枢：选一个已收录的副本来跑；以后可扩展为连刷多个）
# ----------------------------------------------------------------------
class DungeonPage(ctk.CTkFrame):
    """副本中枢：下拉选一个副本（is_dungeon=True 的任务）→ 选队长 → 跑。
    副本本身的「先组队再跑流程」都在各副本任务里；本页只负责选哪个、谁当队长、演练/实战、标定入口。
    新增副本：写个 is_dungeon=True 的 Task 即自动出现在下拉里，本页无需改。"""

    TASK_NAME = "dungeon"
    LOG_SOURCE = "刷副本"
    RUN_LABEL = "▶  开始刷副本"

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.fonts = app.fonts
        self.runner = None
        self._cal_dialog = None          # 副本自身「标定」的去重槽（队长ID 走「队长ID 库」弹窗）
        self._leader_thumbs = []         # 行内队长ID缩略图防 GC
        self._win_count = 0
        # 已收录的副本（按注册顺序）。title 给下拉显示、name 作配置命名空间键。
        self._dungeons = dungeon_tasks()
        self._dnames = [c.name for c in self._dungeons]
        self._dtitles = [c.title for c in self._dungeons]
        self._selected = self._dnames[0] if self._dnames else None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        self._build_header()
        self._build_control()
        self._build_body()
        self.refresh()

    # ---- 选中副本的辅助 ----
    def _title_to_name(self, title):
        for c in self._dungeons:
            if c.title == title:
                return c.name
        return self._selected

    def _selected_title(self):
        for c in self._dungeons:
            if c.name == self._selected:
                return c.title
        return self._dtitles[0] if self._dtitles else "（无副本）"

    def _selected_cls(self):
        return get_task(self._selected) if self._selected else None

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
        sub = ctk.CTkLabel(bar, text="选一个副本来跑：自动先组队、再由队长跑完该副本流程。"
                                     "目前收录：蹈海去·50。需多开≥2 个号、同尺寸。"
                                     "组队功能本身在「通用 / 工具」页（选队长→一键组队）。",
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
        desc_mode = ctk.CTkLabel(box1, text="关 = 演练（只识别自检，安全）　开 = 真组队 + 真跑副本（演练/实战是「每个副本各自」的设置）",
                                 font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left")
        desc_mode.pack(fill="x", pady=(5, 0))
        bind_wraplength(desc_mode)

    def _build_body(self):
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=2, column=0, sticky="nsew", padx=4)
        body.grid_columnconfigure(0, weight=1)   # 日志已移到全局右栏，主体内容独占整宽
        body.grid_rowconfigure(0, weight=1)

        # 左：选副本 + 队长 + 标定状态
        left = Card(body)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        left.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(left, text="副本设置", font=self.fonts["h2"], text_color=T.TEXT).grid(
            row=0, column=0, sticky="w", padx=16, pady=(14, 6))

        dsel = ctk.CTkFrame(left, fg_color="transparent")
        dsel.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 6))
        ctk.CTkLabel(dsel, text="选择副本", font=self.fonts["body"], text_color=T.TEXT).pack(side="left")
        self.var_dungeon = ctk.StringVar(value=self._selected_title())
        self.opt_dungeon = ctk.CTkOptionMenu(dsel, variable=self.var_dungeon,
                                             values=self._dtitles or ["（无副本）"],
                                             font=self.fonts["body"], fg_color=T.SURFACE_2,
                                             button_color=T.BORDER, button_hover_color=T.ACCENT,
                                             text_color=T.TEXT, dropdown_text_color=T.TEXT, width=160,
                                             command=self._on_select_dungeon)
        self.opt_dungeon.pack(side="left", padx=(8, 0))

        cap = ctk.CTkFrame(left, fg_color="transparent")
        cap.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 6))
        ctk.CTkLabel(cap, text="谁当队长", font=self.fonts["body"], text_color=T.TEXT).pack(side="left")
        self.var_captain = ctk.StringVar(value="号1")
        self.opt_captain = ctk.CTkOptionMenu(cap, variable=self.var_captain, values=["号1"],
                                             font=self.fonts["body"], fg_color=T.SURFACE_2,
                                             button_color=T.BORDER, button_hover_color=T.ACCENT,
                                             text_color=T.TEXT, dropdown_text_color=T.TEXT, width=120,
                                             command=self._on_captain)
        self.opt_captain.pack(side="left", padx=(8, 0))
        # 队长ID 入口：带缩略图的小按钮（一眼看到当前认哪张脸），点开「队长ID 库」弹窗；
        # 库里管当前+最近3历史、可切换，写的是共享 teaming.leader_id，和通用页同步。
        self.btn_leader = ctk.CTkButton(cap, text="标定队长ID", font=self.fonts["small"], height=36, width=110,
                                        corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER,
                                        text_color=T.TEXT, border_width=1, border_color=T.BORDER,
                                        compound="left", command=self._open_leader_gallery)
        self.btn_leader.pack(side="left", padx=(8, 0))
        self._refresh_leader_btn()

        skip = ctk.CTkFrame(left, fg_color="transparent")
        skip.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 4))
        self.switch_skip = ctk.CTkSwitch(skip, text="已组队（跳过组队，直接开刷）", font=self.fonts["body"],
                                         progress_color=T.ACCENT, command=self._toggle_skip)
        self.switch_skip.pack(anchor="w")
        self.switch_disband = ctk.CTkSwitch(skip, text="跑完解散队伍（所有号退队）", font=self.fonts["body"],
                                            progress_color=T.ACCENT, command=self._toggle_disband)
        self.switch_disband.pack(anchor="w", pady=(6, 0))

        hint = ctk.CTkLabel(left, text="选要跑的副本，再从已选多开窗口里指定队长（按从左到右/上到下编号），其余号自动当队员。\n"
                               "「已组队」勾上=已自行组好队，直接由队长开刷、不再组队（此时不需要组队标定）。\n"
                               "组队的模板/区域在「通用」页统一标定（所有副本共享）；副本自身模板用本页「标定」按钮。\n"
                               "鼠标甩到屏幕左上角可紧急停止。",
                     font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left")
        hint.grid(row=4, column=0, sticky="ew", padx=16, pady=(2, 8))
        bind_wraplength(hint)

        self.lbl_calib = ctk.CTkLabel(left, text="", font=self.fonts["small"], text_color=T.TEXT_DIM,
                                      justify="left")
        self.lbl_calib.grid(row=5, column=0, sticky="ew", padx=16, pady=(2, 14))
        bind_wraplength(self.lbl_calib)

        # 日志已统一到 App 右侧的全局日志面板，本页不再单独建日志框。

    # ---- 刷新 / 状态 ----
    def refresh(self):
        self.app.cfg = cfg_mod.load_config()
        # 当前选中的副本（中枢级，存 tasks.dungeon.selected）
        hub = cfg_mod.task_config(self.app.cfg, self.TASK_NAME)
        sel = hub.get("selected")
        if sel in self._dnames:
            self._selected = sel
        elif self._dnames:
            self._selected = self._dnames[0]
        self.var_dungeon.set(self._selected_title())
        # 演练/实战、已组队 都读「选中副本自己」的命名空间
        sel_tc = cfg_mod.task_config(self.app.cfg, self._selected) if self._selected else {}
        dry = sel_tc.get("dry_run", True)
        (self.switch_mode.select if not dry else self.switch_mode.deselect)()
        self._render_mode_pill(dry)
        (self.switch_skip.select if sel_tc.get("skip_team", False) else self.switch_skip.deselect)()
        (self.switch_disband.select if sel_tc.get("auto_disband", False) else self.switch_disband.deselect)()
        self._refresh_leader_btn()
        # 先用上次已知的号数即时渲染（窗口枚举很慢，不能每次切页都同步卡住）；再后台枚举刷新号数。
        self._render_team_status()
        self._kick_count_windows()

    def _selected_dry(self):
        if not self._selected:
            return True
        return cfg_mod.task_config(self.app.cfg, self._selected).get("dry_run", True)

    def _selected_skip_team(self):
        if not self._selected:
            return False
        return cfg_mod.task_config(self.app.cfg, self._selected).get("skip_team", False)

    def _dungeon_calib_counts(self):
        """据选中副本的 CALIBRATION 通用算出 (区域完成, 区域需, 模板完成, 模板需)。
        区域 spec 第 4 项为 True 表示「可选」，不计入必要项（如 scene 留空=整窗）。"""
        cls = self._selected_cls()
        if cls is None:
            return 0, 0, 0, 0
        spec = getattr(cls, "CALIBRATION", {}) or {}
        tc = cfg_mod.task_config(self.app.cfg, self._selected)
        regions, templates = tc.get("regions", {}), tc.get("templates", {})
        need_r = [t[0] for t in spec.get("regions", []) if not (len(t) >= 4 and t[3])]
        need_t = [t[0] for t in spec.get("templates", [])]
        rdone = sum(1 for k in need_r if regions.get(k))
        tdone = sum(1 for k in need_t if templates.get(k))
        return rdone, len(need_r), tdone, len(need_t)

    def _render_team_status(self):
        """据当前 self._win_count + 组队标定 + 选中副本标定，渲染队长下拉与就绪状态（纯本地数据，秒回）。"""
        n = self._win_count
        opts = [f"号{i + 1}" for i in range(n)] or ["（未选窗口）"]
        self.opt_captain.configure(values=opts)
        tc = cfg_mod.task_config(self.app.cfg, self._selected) if self._selected else {}
        cap = tc.get("captain_index", 0)
        if not (0 <= cap < n):
            cap = 0
        self.var_captain.set(f"号{cap + 1}" if n else "（未选窗口）")

        skip_team = self._selected_skip_team()
        team_tc = cfg_mod.task_config(self.app.cfg, "teaming")
        treg, ttpl = team_tc.get("regions", {}), team_tc.get("templates", {})
        trdone = sum(1 for k in TEAM_REQUIRED_REGIONS if treg.get(k))
        ttdone = sum(1 for k in TEAM_REQUIRED_TEMPLATES if ttpl.get(k))
        team_ok = (trdone == len(TEAM_REQUIRED_REGIONS) and ttdone == len(TEAM_REQUIRED_TEMPLATES))

        rdone, rneed, tdone, tneed = self._dungeon_calib_counts()
        self_ok = (rdone == rneed and tdone == tneed)
        if skip_team:
            ready = self_ok and n >= 1
            team_line = "组队标定：已勾「已组队」，本次跳过组队（无需组队标定）\n"
            tail = "　✓ 可运行" if ready else "　（需≥1 个号 且副本标定齐全）"
        else:
            ready = team_ok and self_ok and n >= 2
            team_line = (f"组队标定：区域 {trdone}/{len(TEAM_REQUIRED_REGIONS)}，"
                         f"模板 {ttdone}/{len(TEAM_REQUIRED_TEMPLATES)}\n")
            tail = "　✓ 可运行" if ready else "　（需多开≥2 且组队+副本标定齐全）"
        self.lbl_calib.configure(
            text=team_line
                 + f"副本标定（{self._selected_title()}）：区域 {rdone}/{rneed}，模板 {tdone}/{tneed}\n"
                 + f"已选 {n} 个号，队长=号{cap + 1}" + tail)

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
                    self._render_team_status()

            try:
                self.app.after(0, apply)
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

    def _on_select_dungeon(self, choice):
        """切换要跑的副本：把选择存进中枢命名空间（tasks.dungeon.selected），再整体刷新本页
        （队长/演练实战/标定状态都改读新副本自己的命名空间）。"""
        name = self._title_to_name(choice)
        if name == self._selected:
            return
        self._selected = name
        cfg = cfg_mod.load_config()
        hub = cfg_mod.task_config(cfg, self.TASK_NAME)
        hub["selected"] = name
        cfg_mod.set_task_config(cfg, self.TASK_NAME, hub)
        cfg_mod.save_config(cfg)
        self.app.cfg = cfg
        self.refresh()
        self._log_line(f"已切换副本：{self._selected_title()}", "info")

    def _toggle_skip(self):
        """切「已组队」：写进选中副本自己的命名空间。勾上=本次跳过组队、直接由队长开刷。"""
        if not self._selected:
            return
        skip = bool(self.switch_skip.get())
        cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(cfg, self._selected)
        tc["skip_team"] = skip
        cfg_mod.set_task_config(cfg, self._selected, tc)
        cfg_mod.save_config(cfg)
        self.app.cfg = cfg
        self._render_team_status()
        if skip:
            self._log_line("已勾「已组队」：本次不再组队，直接由队长开刷（确保你已自行组好队、队长在副本入口）。", "info")
        else:
            self._log_line("已取消「已组队」：恢复先组队再开刷。", "info")

    def _toggle_disband(self):
        """切「跑完解散队伍」：写进选中副本自己的命名空间。勾上=副本跑完后让所有号自动退队。
        退队的「退出队伍」按钮在「通用」页「标定（组队）」里标（共享 teaming 命名空间）。"""
        if not self._selected:
            return
        auto = bool(self.switch_disband.get())
        cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(cfg, self._selected)
        tc["auto_disband"] = auto
        cfg_mod.set_task_config(cfg, self._selected, tc)
        cfg_mod.save_config(cfg)
        self.app.cfg = cfg
        if auto:
            self._log_line("已勾「跑完解散队伍」：副本结束后所有号自动退队（需在「通用」页标定「退出队伍」按钮）。", "info")
        else:
            self._log_line("已取消「跑完解散队伍」：副本跑完保留队伍。", "info")

    def _open_leader_gallery(self):
        """打开「队长ID 库」：当前+最近3历史可切换（共享 teaming.leader_id，与通用页同步）。"""
        from .leader_gallery import LeaderIdGallery
        LeaderIdGallery.open(self.app)

    def _refresh_leader_btn(self):
        """重读激活队长ID图，更新行内按钮缩略图（无图则回退纯文字「标定队长ID」）。"""
        btn = getattr(self, "btn_leader", None)
        if btn is None:
            return
        self._leader_thumbs.clear()
        img = load_thumb("templates/tm_leader_id.png", self._leader_thumbs, max_h=26)
        try:
            btn.configure(image=img, text=" 队长ID" if img is not None else "标定队长ID")
        except Exception:
            pass

    def _on_captain(self, choice):
        if not self._win_count or not self._selected:
            return
        try:
            idx = int(str(choice).replace("号", "")) - 1
        except (TypeError, ValueError):
            idx = 0
        idx = max(0, min(self._win_count - 1, idx))
        cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(cfg, self._selected)
        tc["captain_index"] = idx
        cfg_mod.set_task_config(cfg, self._selected, tc)
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
        if not self._selected:
            self._log_line("还没有可跑的副本（未收录任何副本）。", "error")
            return
        self._apply_params()
        self.app.cfg = cfg_mod.load_config()
        task_cls = get_task(self._selected)
        if task_cls is None:
            self._log_line(f"找不到副本任务「{self._selected}」。", "error")
            return
        self.runner = TaskRunner(task_cls(), self.app.cfg)
        ok, problems = self.runner.start()
        if not ok:
            for p in problems:
                self._log_line("无法启动：" + p, "error")
            self.runner = None
            return
        self._log_line(f"开始跑副本：{self._selected_title()}", "hit")
        self.btn_run.configure(text="■  停止", fg_color=T.DANGER, hover_color=T.DANGER_HOVER, state="normal")

    def _apply_params(self):
        """启动前把队长序号写回【选中副本】的配置（下拉框变更时已即时存，这里兜底再存一次）。"""
        if not self._selected:
            return
        cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(cfg, self._selected)
        if self._win_count:
            try:
                idx = int(self.var_captain.get().replace("号", "")) - 1
                tc["captain_index"] = max(0, min(self._win_count - 1, idx))
            except (TypeError, ValueError):
                pass
        cfg_mod.set_task_config(cfg, self._selected, tc)
        cfg_mod.save_config(cfg)
        self.app.cfg = cfg

    def _on_runner_finished(self):
        self.btn_run.configure(text=self.RUN_LABEL, fg_color=T.ACCENT,
                               hover_color=T.ACCENT_HOVER, state="normal")

    def _toggle_mode(self):
        if not self._selected:
            return
        live = bool(self.switch_mode.get())
        cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(cfg, self._selected)
        tc["dry_run"] = not live
        cfg_mod.set_task_config(cfg, self._selected, tc)
        cfg_mod.save_config(cfg)
        self.app.cfg = cfg
        self._render_mode_pill(not live)
        if live:
            self._log_line(f"⚠ 已切到实战（{self._selected_title()}）：会真组队、真跑副本，请用小号！", "warn")
        else:
            self._log_line(f"已切回演练（{self._selected_title()}，只识别自检，安全）。", "info")

    def _open_calibrate(self):
        """打开【当前选中副本】的标定向导（标定它自己的模板/区域；组队标定仍在「通用」页）。"""
        if not self._selected:
            self._log_line("没有可标定的副本。", "warn")
            return
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
            self._cal_dialog = CalibrateDialog(self.app, task_name=self._selected, on_done=_after)
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
        # 日志统一汇到 App 右侧全局面板，按本页 LOG_SOURCE 打来源标签。
        self.app.log_line(msg, level, getattr(self, "LOG_SOURCE", None))

    def _clear_log(self):
        self.app.clear_log()


# ----------------------------------------------------------------------
# 秘境降妖 页面
# ----------------------------------------------------------------------
class SecretRealmPage(ctk.CTkFrame):
    TASK_NAME = "secret_realm"
    LOG_SOURCE = "秘境"
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
        body.grid_columnconfigure(0, weight=1)   # 日志已移到全局右栏，主体内容独占整宽
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

        # 日志已统一到 App 右侧的全局日志面板，本页不再单独建日志框。

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
        # 日志统一汇到 App 右侧全局面板，按本页 LOG_SOURCE 打来源标签。
        self.app.log_line(msg, level, getattr(self, "LOG_SOURCE", None))

    def _clear_log(self):
        self.app.clear_log()


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
        hint = ctk.CTkLabel(box, text="全局急停：游戏在前台也能按，按一下立刻停止所有正在跑的任务（鼠标被脚本"
                                      "拉着失控时随时叫停）。默认 Ctrl+Alt+F12；建议带修饰键，避免和游戏内按键误撞。改完记得保存。",
                            font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left")
        hint.pack(fill="x", pady=(4, 0))
        bind_wraplength(hint)
        return box

    def _build_failsafe(self, parent):
        box = ctk.CTkFrame(parent, fg_color="transparent")
        ctk.CTkOptionMenu(box, variable=self.var_failsafe, values=FAILSAFE_LABELS,
                          font=self.fonts["body"], fg_color=T.SURFACE_2,
                          button_color=T.BORDER, button_hover_color=T.ACCENT, text_color=T.TEXT,
                          dropdown_text_color=T.TEXT, width=160).pack(anchor="w")
        hint = ctk.CTkLabel(box, text="任务运行时，把鼠标猛甩到所选屏幕角（撞到角落）立刻急停。独立于鼠标后端、"
                                      "始终生效。选「关闭」则只靠停止按钮 / 急停热键。改完记得保存。",
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
        cfg["hotkey_stop"] = _compose_stop_hotkey(
            self.var_hk_ctrl.get(), self.var_hk_alt.get(),
            self.var_hk_shift.get(), self.var_hk_key.get())
        cfg["failsafe_corner"] = FAILSAFE_VALUE_OF.get(self.var_failsafe.get(), DEFAULT_FAILSAFE)

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
    目前：组队标定 + 一键组队（选队长→把所选多开窗口组成一队）；窗口尺寸归一化。"""

    LOG_SOURCE = "通用"   # 组队/整理背包日志在 pump 里各自覆盖来源标签

    def __init__(self, parent, app):
        super().__init__(parent, fg_color="transparent")
        self.app = app
        self.fonts = app.fonts
        self.cfg = app.cfg
        self.runner = None          # 一键组队跑的后台任务（DungeonTask）
        self.runner_db = None       # 一键解散跑的后台任务（DisbandTask），与组队互斥（同用鼠标/队伍面板）
        self.runner_ob = None       # 一键整理跑的后台任务（OrganizeBagTask），与组队并存互不干扰
        self._team_cal_dialog = None    # 「标定（组队）」去重槽（队长ID 走无弹窗直接标定，无需去重槽）
        self._ob_cal_dialog = None      # 「标定（整理背包）」去重槽
        self.btn_ob = None          # 「一键整理」按钮（_refresh_body 每次重建）
        self.switch_ob = None       # 整理背包实战/演练开关
        self.switch_auto_ob = None  # 「自动整理背包」开关（任何任务检测到背包满自动整理）
        self._win_count = 0         # 已选多开窗口数（resolve_targets），供状态行显示
        self.btn_team = None
        self.btn_disband = None     # 「一键解散」按钮（_refresh_body 每次重建）
        self.btn_leader = None      # 行内队长ID按钮（_refresh_body 每次重建）
        self._leader_thumbs = []    # 行内队长ID缩略图防 GC
        self.lbl_team_status = None
        self._build()

    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ctk.CTkLabel(head, text="通用 / 工具", font=self.fonts["title"], text_color=T.TEXT).pack(anchor="w")
        sub = ctk.CTkLabel(head, text="跨任务的通用功能：组队标定 + 一键组队 / 一键解散 / 还原窗口尺寸。各任务专属的标定与「选择窗口」仍在对应任务页。",
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

        # —— 一键组队（选好队长，把所选多开窗口直接组成一队）——
        ctk.CTkFrame(c_team, fg_color=T.BORDER, height=1).pack(fill="x", padx=16, pady=(10, 0))
        act = ctk.CTkFrame(c_team, fg_color="transparent")
        act.pack(fill="x", padx=16, pady=(10, 0))
        self.btn_team = ctk.CTkButton(act, text="▶  一键组队", font=self.fonts["btn"], height=40, width=150,
                                      corner_radius=T.RADIUS_SM, fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER,
                                      text_color=T.ON_ACCENT, command=self._start_teaming)
        self.btn_team.pack(side="left")
        # 一键解散：让所选各号都退出当前队伍（开队伍面板→退出队伍→关面板，每号同一套流程）
        self.btn_disband = ctk.CTkButton(act, text="⏏  一键解散", font=self.fonts["btn"], height=40, width=130,
                                         corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER,
                                         text_color=T.TEXT, border_width=1, border_color=T.BORDER,
                                         command=self._start_disband)
        self.btn_disband.pack(side="left", padx=(8, 0))
        # 「选择窗口」带缩略图，且队长就在这里选（卡片上勾「队长」）——下拉框「号123」看不出是哪个窗口，故移进来。
        ctk.CTkButton(act, text="选择窗口/队长", font=self.fonts["body"], height=36, width=120,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=lambda: self.app.open_window_picker(self.refresh, captain_ns="teaming")).pack(
                          side="left", padx=(8, 0))
        # 队长ID 入口：带缩略图的小按钮，点开「队长ID 库」（当前+最近3历史可切换）；
        # 和刷副本页写同一处 teaming.leader_id，天然同步。
        self.btn_leader = ctk.CTkButton(act, text="标定队长ID", font=self.fonts["small"], height=36, width=110,
                                        corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER,
                                        text_color=T.TEXT, border_width=1, border_color=T.BORDER,
                                        compound="left", command=self._open_leader_gallery)
        self.btn_leader.pack(side="left", padx=(8, 0))
        self._refresh_leader_btn()

        self.lbl_team_status = ctk.CTkLabel(c_team, text="", font=self.fonts["small"],
                                            text_color=T.TEXT_DIM, justify="left")
        self.lbl_team_status.pack(fill="x", padx=16, pady=(6, 0))
        bind_wraplength(self.lbl_team_status)

        # 日志已统一到 App 右侧的全局日志面板（组队打「组队」标签、整理背包打「整理背包」标签），本页不再单独建日志框。
        # 重建后：按窗口数即时渲染队长下拉/状态 + 据 runner 复位按钮，再后台刷新窗口数
        self._render_team_action()
        if self.runner and self.runner.is_running():
            self.btn_team.configure(text="■  停止组队", fg_color=T.DANGER, hover_color=T.DANGER_HOVER)
        if self.runner_db and self.runner_db.is_running():
            self.btn_disband.configure(text="■  停止解散", fg_color=T.DANGER, hover_color=T.DANGER_HOVER)
        self._kick_count_windows()

        # ── 整理背包（跨任务共享：任何任务流程都可穿插调用，这里可单独一键运行）──
        self._build_organize_card()

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

    def _calib_singleton(self, attr, only, fail_msg, exclude=None):
        """打开一个标定窗并按 attr 去重：已开着就 lift 回来，不叠开多个写同一处 teaming 的窗
        （叠开会「后关的覆盖先关的」，让用户以为没生效）。"""
        existing = getattr(self, attr, None)
        if existing is not None:
            try:
                if existing.winfo_exists():
                    existing.lift()
                    existing.focus_force()
                    return
            except Exception:
                pass
        from .calibrate_dialog import CalibrateDialog

        def _after():
            setattr(self, attr, None)
            self.refresh()

        try:
            setattr(self, attr, CalibrateDialog(self.app, task_name="teaming",
                                                only=only, exclude=exclude, on_done=_after))
        except Exception as e:
            setattr(self, attr, None)
            self.app.toast(f"{fail_msg}：{e}")

    def _open_team_calibrate(self):
        """打开组队标定（共享命名空间 teaming）。队长ID 已移到「标定队长ID」按钮单独标，这里不再列出。"""
        self._calib_singleton("_team_cal_dialog", None, "打开组队标定失败", exclude=["leader_id"])

    def _open_leader_gallery(self):
        """打开「队长ID 库」：当前+最近3历史可切换（共享 teaming.leader_id，与刷副本页同步）。"""
        from .leader_gallery import LeaderIdGallery
        LeaderIdGallery.open(self.app)

    def _refresh_leader_btn(self):
        """重读激活队长ID图，更新行内按钮缩略图（无图则回退纯文字「标定队长ID」）。"""
        btn = getattr(self, "btn_leader", None)
        if btn is None:
            return
        self._leader_thumbs.clear()
        img = load_thumb("templates/tm_leader_id.png", self._leader_thumbs, max_h=26)
        try:
            btn.configure(image=img, text=" 队长ID" if img is not None else "标定队长ID")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 一键组队（角色参数存共享命名空间 tasks.teaming；跑的是 DungeonTask）
    # ------------------------------------------------------------------
    def _render_team_action(self):
        """据当前 self._win_count + teaming.captain_index 渲染队长状态行（纯本地数据，秒回）。
        队长已挪进「选择窗口/队长」里选（带缩略图），这里只读出来显示。"""
        if self.lbl_team_status is None:
            return
        n = self._win_count
        team_tc = cfg_mod.task_config(self.app.cfg, "teaming")
        cap = team_tc.get("captain_index", 0)
        if not (0 <= cap < n):
            cap = 0
        multi = self.app.cfg.get("targets", {}).get("multi", False)
        if n >= 2:
            tip = f"已选 {n} 个号，队长=第{cap + 1}个所选号，其余当队员。点「一键组队」开始。"
        elif not multi:
            tip = "组队需多开：请点「选择窗口/队长」切到多开、勾 2~5 个号并指定队长。"
        else:
            tip = f"已选 {n} 个号，组队至少 2 个号（队长+≥1 队员）。"
        self.lbl_team_status.configure(text=tip)

    def _kick_count_windows(self):
        """后台枚举已选窗口数，变了再回主线程重渲染队长状态。token 丢弃过期结果。"""
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
                self._win_count = n
                self._render_team_action()

            try:
                self.app.after(0, apply)
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

    def _start_teaming(self):
        if self.runner and self.runner.is_running():
            self.runner.stop()
            self._log_line("正在停止…", "warn", "组队")
            self.btn_team.configure(text="停止中…", state="disabled")
            return
        # 强制实战（一键组队是显式动作，不走演练）。窗口选择与队长都在「选择窗口/队长」里定好了。
        cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(cfg, "teaming")
        tc["dry_run"] = False
        cfg_mod.set_task_config(cfg, "teaming", tc)
        cfg_mod.save_config(cfg)
        self.app.cfg = self.cfg = cfg

        task_cls = get_task("dungeon")
        if task_cls is None:
            self._log_line("找不到组队任务。", "error", "组队")
            return
        self.runner = TaskRunner(task_cls(), self.app.cfg)
        ok, problems = self.runner.start()
        if not ok:
            for p in problems:
                self._log_line("无法开始组队：" + p, "error", "组队")
            self.runner = None
            return
        self._log_line("开始一键组队…", "hit", "组队")
        self.btn_team.configure(text="■  停止组队", fg_color=T.DANGER, hover_color=T.DANGER_HOVER, state="normal")

    def _on_team_finished(self):
        if self.btn_team is not None:
            self.btn_team.configure(text="▶  一键组队", fg_color=T.ACCENT,
                                    hover_color=T.ACCENT_HOVER, state="normal")

    def _start_disband(self):
        if self.runner_db and self.runner_db.is_running():
            self.runner_db.stop()
            self._log_line("正在停止…", "warn", "解散")
            self.btn_disband.configure(text="停止中…", state="disabled")
            return
        # 强制实战（一键解散是显式动作，不走演练）。窗口在「选择窗口/队长」里选定。
        cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(cfg, "teaming")
        tc["dry_run"] = False
        cfg_mod.set_task_config(cfg, "teaming", tc)
        cfg_mod.save_config(cfg)
        self.app.cfg = self.cfg = cfg

        task_cls = get_task("disband")
        if task_cls is None:
            self._log_line("找不到解散队伍任务。", "error", "解散")
            return
        self.runner_db = TaskRunner(task_cls(), self.app.cfg)
        ok, problems = self.runner_db.start()
        if not ok:
            for p in problems:
                self._log_line("无法开始解散：" + p, "error", "解散")
            self.runner_db = None
            return
        self._log_line("开始一键解散…", "hit", "解散")
        self.btn_disband.configure(text="■  停止解散", fg_color=T.DANGER, hover_color=T.DANGER_HOVER,
                                   state="normal")

    def _on_disband_finished(self):
        if self.btn_disband is not None:
            self.btn_disband.configure(text="⏏  一键解散", fg_color=T.BTN, hover_color=T.BTN_HOVER,
                                       text_color=T.TEXT, state="normal")

    # ---- 由 App._tick 驱动 ----
    def pump(self):
        if self.runner:
            q = self.runner.log_queue
            while not q.empty():
                level, msg = q.get()
                self._log_line(msg, level, "组队")
            if not self.runner.is_running() and self.btn_team is not None \
                    and self.btn_team.cget("text") != "▶  一键组队":
                self._on_team_finished()
        if self.runner_db:
            q = self.runner_db.log_queue
            while not q.empty():
                level, msg = q.get()
                self._log_line(msg, level, "解散")
            if not self.runner_db.is_running() and self.btn_disband is not None \
                    and self.btn_disband.cget("text") != "⏏  一键解散":
                self._on_disband_finished()
        if self.runner_ob:
            q = self.runner_ob.log_queue
            while not q.empty():
                level, msg = q.get()
                self._log_line(msg, level, "整理背包")
            if not self.runner_ob.is_running() and self.btn_ob is not None \
                    and self.btn_ob.cget("text") != "▶  一键整理":
                self._on_ob_finished()

    @staticmethod
    def _targets_summary(cfg):
        """只读 cfg.targets 拼一句「将操作哪些号」的说明，不去枚举/定位窗口（够快、给卡片当提示用）。
        多开未指定 multi_indices = 全体号；指定了就报个数；单开报号几。"""
        targets = (cfg or {}).get("targets", {}) or {}
        if targets.get("multi"):
            idxs = targets.get("multi_indices") or []
            return f"多开 · 已选 {len(idxs)} 个号" if idxs else "多开 · 全体号"
        i = targets.get("single_index", 0)
        i = i if isinstance(i, int) and i >= 0 else 0
        return f"单开 · 号{i + 1}"

    # ------------------------------------------------------------------
    # 整理背包（跨任务共享：core.InventoryOrganizer + tasks.OrganizeBagTask；
    # 标定/物品/参数存共享命名空间 tasks.organize_bag；这里可单独一键运行）
    # ------------------------------------------------------------------
    def _build_organize_card(self):
        """在组队卡之后渲染「整理背包（共享）」卡片：完成度行 + 说明 + 按钮行。
        日志统一写到 App 右侧的全局日志面板（来源标签「整理背包」）。"""
        cfg = self.cfg
        ob_tc = cfg_mod.task_config(cfg, "organize_bag")
        items = ob_tc.get("items", []) or []
        tpl = ob_tc.get("templates", {}) or {}

        # 被 items 用到的动作 → 所需按钮键（单一来源 core.inventory.required_templates，含各动作步骤+确认框）。
        need = set()
        for it in items:
            for key, _lbl in required_templates(it.get("action", "use")):
                need.add(key)
        done = sum(1 for k in need if tpl.get(k))
        total = len(need)
        ready = (total > 0 and done == total)

        c = self._card()
        head = ctk.CTkFrame(c, fg_color="transparent")
        head.pack(fill="x", padx=16, pady=(14, 4))
        head.grid_columnconfigure(0, weight=1)
        txt = ctk.CTkFrame(head, fg_color="transparent")
        txt.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(txt, text="整理背包（共享）", font=self.fonts["h2"], text_color=T.TEXT).pack(anchor="w")
        ctk.CTkLabel(txt, text=f"物品 {len(items)} 件；动作按钮 {done}/{total} 已标定"
                              + ("　✓ 已就绪" if ready else "　（还需标定）"),
                     font=self.fonts["body"],
                     text_color=T.SUCCESS if ready else T.WARN).pack(anchor="w", pady=(4, 0))
        sub = ctk.CTkLabel(txt, text="翻包裹找到标定的物品，逐个使用/丢弃/出售。是跨任务共享能力，"
                                     "任何任务流程都可穿插调用；这里可单独一键运行。",
                           font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left")
        sub.pack(fill="x", pady=(2, 0))
        bind_wraplength(sub)
        ctk.CTkLabel(txt, text="将整理：" + self._targets_summary(cfg),
                     font=self.fonts["small"], text_color=T.TEXT_DIM).pack(anchor="w", pady=(4, 0))
        btns = ctk.CTkFrame(head, fg_color="transparent")
        btns.grid(row=0, column=1, padx=(12, 0))
        ctk.CTkButton(btns, text="标定（整理背包）", font=self.fonts["body"], height=36, width=130,
                      corner_radius=T.RADIUS_SM, fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER,
                      text_color=T.ON_ACCENT, command=self._open_organize_calibrate).pack()
        ctk.CTkButton(btns, text="管理物品", font=self.fonts["body"], height=32, width=130,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self._open_organize_items).pack(pady=(6, 0))

        ctk.CTkFrame(c, fg_color=T.BORDER, height=1).pack(fill="x", padx=16, pady=(10, 0))
        act = ctk.CTkFrame(c, fg_color="transparent")
        act.pack(fill="x", padx=16, pady=(10, 14))
        self.btn_ob = ctk.CTkButton(act, text="▶  一键整理", font=self.fonts["btn"], height=40, width=150,
                                    corner_radius=T.RADIUS_SM, fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER,
                                    text_color=T.ON_ACCENT, command=self._start_organize)
        self.btn_ob.pack(side="left")
        # 选择整理哪些号：写的是和秒装备/运镖等任务共用的全局 targets（select_windows 读它），
        # 不选/多开留空 = 全体号。放在最右，和「一键整理」分列两端。
        ctk.CTkButton(act, text="选择窗口", font=self.fonts["body"], height=36, width=104,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=lambda: self.app.open_window_picker(self.refresh)).pack(side="right")
        self.switch_ob = ctk.CTkSwitch(act, text="实战模式（真的会使用/丢弃/出售）", font=self.fonts["body"],
                                       command=self._toggle_organize_mode, progress_color=T.DANGER)
        self.switch_ob.pack(side="left", padx=(16, 0))
        if not ob_tc.get("dry_run", True):
            self.switch_ob.select()
        else:
            self.switch_ob.deselect()

        # 「自动整理背包」：跨任务全局开关。开了后，任何走多开轮转的任务（运镖/宝图/秘境/副本）
        # 运行中每隔一会儿检测一次背包「满」图标，满了就自动整理一遍（真整理/只识别跟随上面的实战开关）。
        auto_row = ctk.CTkFrame(c, fg_color="transparent")
        auto_row.pack(fill="x", padx=16, pady=(0, 12))
        self.switch_auto_ob = ctk.CTkSwitch(auto_row, text="自动整理背包（任何任务检测到背包满就自动清）",
                                            font=self.fonts["body"], command=self._toggle_auto_organize)
        self.switch_auto_ob.pack(anchor="w")
        if ob_tc.get("auto_organize"):
            self.switch_auto_ob.select()
        else:
            self.switch_auto_ob.deselect()
        auto_hint = ctk.CTkLabel(auto_row, text="开启后，运镖 / 宝图 / 秘境 / 副本等任务运行中会每隔一会儿检测一次背包"
                                                "「满」图标，满了就自动整理一遍 —— 需先在「标定（整理背包）」里框选"
                                                "『背包满图标』，否则无从判断、不会触发。",
                                 font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left")
        auto_hint.pack(fill="x", anchor="w", pady=(2, 0))
        bind_wraplength(auto_hint)

        # 重建后：若整理在跑，恢复「停止整理」文案/颜色（照 btn_team 的恢复写法）
        if self.runner_ob and self.runner_ob.is_running():
            self.btn_ob.configure(text="■  停止整理", fg_color=T.DANGER, hover_color=T.DANGER_HOVER)

    def _open_organize_calibrate(self):
        """打开整理背包标定（共享命名空间 organize_bag），按 _ob_cal_dialog 去重。"""
        existing = self._ob_cal_dialog
        if existing is not None:
            try:
                if existing.winfo_exists():
                    existing.lift()
                    existing.focus_force()
                    return
            except Exception:
                pass
        from .calibrate_dialog import CalibrateDialog

        def _after():
            self._ob_cal_dialog = None
            self.refresh()

        try:
            self._ob_cal_dialog = CalibrateDialog(self.app, task_name="organize_bag", on_done=_after)
        except Exception as e:
            self._ob_cal_dialog = None
            self.app.toast(f"打开整理背包标定失败：{e}")

    def _open_organize_items(self):
        """打开「管理物品」弹窗：增删物品、改每件的动作（使用/丢弃/出售）。关闭后刷新完成度。"""
        from .inventory_items_dialog import InventoryItemsDialog
        try:
            InventoryItemsDialog(self.app, on_done=self.refresh)
        except Exception as e:
            self.app.toast(f"打开物品管理失败：{e}")

    def _toggle_organize_mode(self):
        """实战/演练开关：存 tasks.organize_bag.dry_run（照 SniperPage._toggle_mode 的存盘范式）。"""
        live = bool(self.switch_ob.get())  # 1=实战
        cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(cfg, "organize_bag")
        tc["dry_run"] = not live
        cfg_mod.set_task_config(cfg, "organize_bag", tc)
        cfg_mod.save_config(cfg)
        self.app.cfg = self.cfg = cfg
        if live:
            self._log_line("⚠ 整理背包已切到实战模式：会真正使用/丢弃/出售物品，请谨慎！", "warn", "整理背包")
        else:
            self._log_line("整理背包已切回演练模式（安全，只识别不操作）。", "info", "整理背包")

    def _toggle_auto_organize(self):
        """「自动整理背包」开关：存 tasks.organize_bag.auto_organize（任何任务流程检测到背包满自动整理）。"""
        on = bool(self.switch_auto_ob.get())
        cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(cfg, "organize_bag")
        tc["auto_organize"] = on
        cfg_mod.set_task_config(cfg, "organize_bag", tc)
        cfg_mod.save_config(cfg)
        self.app.cfg = self.cfg = cfg
        if on:
            tpl_ok = bool((tc.get("templates", {}) or {}).get("bag_full_icon"))
            self._log_line("已开启「自动整理背包」：任务流程中检测到背包满会自动整理。"
                           + ("" if tpl_ok else " ⚠ 但还没标定『背包满图标』，请先去「标定（整理背包）」框选，否则不会触发。"),
                           "warn" if not tpl_ok else "info", "整理背包")
        else:
            self._log_line("已关闭「自动整理背包」。", "info", "整理背包")

    def _start_organize(self):
        if self.runner_ob and self.runner_ob.is_running():
            self.runner_ob.stop()
            self._log_line("正在停止整理…", "warn", "整理背包")
            self.btn_ob.configure(text="停止中…", state="disabled")
            return
        # dry_run 由开关控制，这里不强改；只读最新配置开跑。
        cfg = cfg_mod.load_config()
        self.app.cfg = self.cfg = cfg
        task_cls = get_task("organize_bag")
        if task_cls is None:
            self._log_line("找不到整理背包任务。", "error", "整理背包")
            return
        self.runner_ob = TaskRunner(task_cls(), self.app.cfg)
        ok, problems = self.runner_ob.start()
        if not ok:
            for p in problems:
                self._log_line("无法开始整理：" + p, "error", "整理背包")
            self.runner_ob = None
            return
        self._log_line("开始一键整理背包…", "hit", "整理背包")
        self.btn_ob.configure(text="■  停止整理", fg_color=T.DANGER, hover_color=T.DANGER_HOVER, state="normal")

    def _on_ob_finished(self):
        if self.btn_ob is not None:
            self.btn_ob.configure(text="▶  一键整理", fg_color=T.ACCENT,
                                  hover_color=T.ACCENT_HOVER, state="normal")

    def update_game_pill(self, connected, summary=""):
        # 通用页没有连接药丸；仅为满足 App 对可运行页的统一调用而存在。
        pass

    def _log_line(self, msg, level="info", source=None):
        # 日志统一汇到 App 右侧全局面板；source 缺省用本页 LOG_SOURCE（「通用」），
        # 组队/整理背包在 pump 与各自的开始/停止消息里显式传「组队」「整理背包」。
        self.app.log_line(msg, level, source or self.LOG_SOURCE)


# ----------------------------------------------------------------------
# 日常一条龙 页面：勾选已有任务 + 调序，一次按顺序跑完
# ----------------------------------------------------------------------
class DailyPage(ctk.CTkFrame):
    """日常一条龙：只做串联——勾选哪些任务、按什么顺序跑，存 tasks.daily.steps。
    多开/单开与各任务的演练/实战、标定、参数全部沿用各自任务页，本页不另设这些开关。"""

    TASK_NAME = "daily"
    LOG_SOURCE = "一条龙"
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

    # ---- 主体：任务清单（铺满；日志已移到全局右栏）----
    def _build_body(self):
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=2, column=0, sticky="nsew", padx=4)
        body.grid_columnconfigure(0, weight=1)   # 日志已移到全局右栏，主体内容独占整宽
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

        # 日志已统一到 App 右侧的全局日志面板，本页不再单独建日志框。

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
        # 日志统一汇到 App 右侧全局面板，按本页 LOG_SOURCE 打来源标签。
        self.app.log_line(msg, level, getattr(self, "LOG_SOURCE", None))

    def _clear_log(self):
        self.app.clear_log()


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
    # 可运行任务页（有 runner/pump/update_game_pill），App 的定时器/热键/关闭钩子按此遍历。
    # general 也在内：它的「一键组队」会跑后台任务，需要 pump 抽日志、关闭时停 runner。
    RUNNABLE_KEYS = ("general", "daily", "sniper", "treasure_map", "escort", "secret_realm", "dungeon")

    def __init__(self):
        super().__init__()
        self.cfg = cfg_mod.load_config()
        # 全局窗口识别按进程名过滤（避免把终端/编辑器等同名标题窗口当游戏号）；GUI 各窗口操作据此生效。
        win_mod.set_game_process(self.cfg.get("window_process", "MyGame_x64r.exe"))
        mode = self.cfg.get("appearance", "dark")
        ctk.set_appearance_mode(mode if mode in ("dark", "light") else "dark")
        self.title("梦幻 · 时空 助手")
        self.geometry("1360x720")
        self.minsize(1180, 640)
        self.configure(fg_color=T.BG)

        self.fonts = T.build_fonts()
        self.game_win = win_mod.GameWindow(self.cfg.get("window_title", "梦幻西游"))
        self._tick_count = 0
        self._game_connected = None   # 缓存连接状态，只在变化时刷新药丸
        self._locating = False        # 防止多个后台定位线程叠加

        self.grid_columnconfigure(1, weight=1)   # 中间内容区随窗口拉伸
        self.grid_columnconfigure(2, weight=0)   # 右侧全局日志列固定宽
        self.grid_rowconfigure(0, weight=1)
        self._build_sidebar()
        self._build_log_panel()   # 先建日志面板：各页 _log_line 都往这写，必须先于建页
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

    # ---------------- 全局日志面板（常驻右侧，所有功能共用一处）----------------
    def _build_log_panel(self):
        """右侧常驻日志列：各页面/任务的日志统一汇到这里，按来源（秒装备/组队/整理背包…）打标签。
        以前每个页面各有一个日志框，功能一多就散乱；现在收敛成这一处，谁产生的日志靠行首来源标签区分。"""
        panel = ctk.CTkFrame(self, fg_color=T.SIDEBAR, corner_radius=0, width=340)
        panel.grid(row=0, column=2, sticky="nsew")
        panel.grid_propagate(False)
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(1, weight=1)

        head = ctk.CTkFrame(panel, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=14, pady=(16, 8))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text="运行日志", font=self.fonts["h2"], text_color=T.TEXT).grid(
            row=0, column=0, sticky="w")
        ctk.CTkButton(head, text="清空", font=self.fonts["small"], height=26, width=56,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self.clear_log).grid(row=0, column=1, sticky="e")

        self.log = ctk.CTkTextbox(panel, font=self.fonts["mono"], fg_color=T.SURFACE_2,
                                  text_color=T.TEXT, corner_radius=T.RADIUS_SM, wrap="word")
        self.log.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 14))
        T.apply_log_tags(self.log._textbox)
        self.log.configure(state="disabled")
        self.log_line("界面就绪。各功能的日志都会汇总到这里。", "info")

    def log_line(self, msg, level="info", source=None):
        """统一日志出口（所有页面/任务都调它）。source 非空时在行首加暗色来源标签，如「秒装备 ›」。
        超过约 2000 行就裁掉最旧的，避免长时间运行把内存吃满。"""
        log = getattr(self, "log", None)
        if log is None:
            return
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        log.configure(state="normal")
        try:
            tb = log._textbox
            tb.insert("end", f"[{ts}] ")
            if source:
                tb.insert("end", f"{source} › ", "src")
            tb.insert("end", f"{msg}\n", level)
            # 行数封顶：删掉最旧的若干行（int(index) 是行号，含末尾空行）
            try:
                nlines = int(tb.index("end-1c").split(".")[0])
                if nlines > 2000:
                    tb.delete("1.0", f"{nlines - 1800}.0")
            except Exception:
                pass
        except Exception:
            prefix = f"{source} › " if source else ""
            log.insert("end", f"[{ts}] {prefix}{msg}\n")
        log.see("end")
        log.configure(state="disabled")

    def clear_log(self):
        log = getattr(self, "log", None)
        if log is None:
            return
        log.configure(state="normal")
        log.delete("1.0", "end")
        log.configure(state="disabled")

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

    def refresh_leader_thumb(self):
        """广播刷新两处行内队长ID缩略图：无论从刷副本页还是通用页改了「队长ID 库」，两页同时更新。"""
        for key in ("dungeon", "general"):
            p = self.pages.get(key)
            if p is not None and hasattr(p, "_refresh_leader_btn"):
                try:
                    p._refresh_leader_btn()
                except Exception:
                    pass

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
        # 日志框走底层 tk tag_config，不随 set_appearance_mode 自动变，需手动重刷这一个全局面板
        log = getattr(self, "log", None)
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

    def open_window_picker(self, after=None, captain_ns=None):
        """打开「选择窗口」对话框（各任务页共用）。关闭后刷新配置并强制刷新药丸。
        captain_ns: 传入则多开模式下可在卡片上直接指定队长，写入 tasks.<captain_ns>.captain_index。"""
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
            WindowPickerDialog(self, on_done=_done, captain_ns=captain_ns)
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

    # ---- 全局急停轮询：鼠标甩到屏幕左上角 或 急停组合键 → 停止一切任务 ----
    def _poll_hotkey(self):
        # ① 物理急停：鼠标甩到所选屏幕角（默认右上角，设置里可改/可关）。注意——默认 sendinput 后端走
        #    SendInput 底层注入，【不经过 pyautogui，没有内置 FAILSAFE】，故必须在这里用真实光标位置自己兜
        #    （覆盖所有后端）。仅在有任务跑时生效，避免空触发；任务停了 _any_running 转 False 不再重复触发。
        corner = self.cfg.get("failsafe_corner", DEFAULT_FAILSAFE)
        if corner != "off":
            try:
                cx, cy = get_cursor()
            except Exception:
                cx, cy = None, None
            if _in_failsafe_corner(cx, cy, corner) and self._any_running():
                self._emergency_stop(f"鼠标甩到{FAILSAFE_CORNERS.get(corner, '角落')}")
        # ② 急停组合键（默认 Ctrl+Alt+F12）：所有修饰键+主键同时按下的瞬间触发一次。
        mods, key_vk = _parse_stop_hotkey(self.cfg.get("hotkey_stop", DEFAULT_STOP_HOTKEY))
        if key_vk is not None:
            down = all(_vk_down(m) for m in mods) and _vk_down(key_vk)
            if down and not self._hotkey_down:
                self._emergency_stop()
            self._hotkey_down = down
        else:
            self._hotkey_down = False
        self.after(60, self._poll_hotkey)

    def _emergency_stop(self, reason=None):
        """急停：停掉所有页面正在跑的后台任务，弹提示。reason 标明来源（甩角/热键）。"""
        tag = reason or f"[{self.cfg.get('hotkey_stop', DEFAULT_STOP_HOTKEY)}]"
        n = self.stop_all_tasks()
        if n:
            self.toast(f"{tag} 急停：已停止 {n} 个运行中的任务")
        else:
            self.toast(f"{tag} 急停（当前没有正在跑的任务）")

    def _any_running(self):
        """是否有任意页面的后台任务在跑（供甩角失控急停判断，避免空触发）。"""
        for page in self.pages.values():
            for v in vars(page).values():
                if isinstance(v, TaskRunner) and v.is_running():
                    return True
        return False

    def stop_all_tasks(self):
        """遍历所有页面，停掉其上任意正在运行的 TaskRunner（runner / runner_ob 等都覆盖到）。返回停了几个。"""
        n = 0
        for page in self.pages.values():
            for v in list(vars(page).values()):
                if isinstance(v, TaskRunner) and v.is_running():
                    try:
                        v.stop()
                        n += 1
                    except Exception:
                        pass
        return n

    def _on_close(self):
        self.stop_all_tasks()
        self.destroy()


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
