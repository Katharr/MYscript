# -*- coding: utf-8 -*-
"""
历史 Tk 主窗口，仅供原回归测试与 Spec00 性能对照；无可执行入口。
当前默认界面见 mhxy.qtui；本包不进入发布产物。
"""

import queue
import threading
from dataclasses import dataclass, replace

import customtkinter as ctk

from . import theme as T
from .launch_login_page import LaunchLoginPage
from .quick_start import QuickStartPanel
from ..core import calib_profiles as calib
from ..core import config as cfg_mod
from ..core import window as win_mod
from ..core import accounts
from ..core.runtime import get_runtime
from ..core.emergency import get_cursor


UI_COMPACT = "compact"
UI_FULL = "full"
UI_FLOATING = "floating"
GAME_UNKNOWN = "unknown"
GAME_READY = "ready"
GAME_ABSENT = "absent"
TASK_IDLE = "idle"
TASK_RUNNING = "running"
TASK_STOPPING = "stopping"
TASK_COMPLETED = "completed"
TASK_STOPPED = "stopped"
TASK_FAILED = "failed"


@dataclass(frozen=True)
class AppState:
    """脚本的单一状态快照：界面形态、目标窗口存在性与最近任务的生命周期。

    任务页仍各自维护按钮和 runner，但所有跨界面状态展示都只读此快照，避免小窗、主界面、
    悬浮窗各自根据线程存活状态作出不一致推断。"""
    ui_mode: str = UI_FULL
    game_state: str = GAME_UNKNOWN
    game_summary: str = ""
    task_state: str = TASK_IDLE
    task_label: str | None = None
    active_labels: tuple = ()


from .window_labels import resolve_window_labels
from .widgets import Card, Pill, load_thumb, bind_wraplength
from .shortcuts import (_parse_stop_hotkey, _compose_stop_hotkey, _split_stop_hotkey, _in_failsafe_corner, _vk_down, HOTKEY_VK, MODIFIER_VK, HOTKEY_NAMES, DEFAULT_STOP_HOTKEY, FAILSAFE_CORNERS, FAILSAFE_LABELS, FAILSAFE_VALUE_OF, DEFAULT_FAILSAFE)
from .sniper_page import SniperPage
from .freeclick_page import FreeClickPage
from .treasure_map_page import TreasureMapPage
from .escort_page import EscortPage
from .dungeon_page import DungeonPage
from .secret_realm_page import SecretRealmPage
from .settings_page import SettingsPage
from .about_page import AboutPage
from .general_page import GeneralPage
from .daily_page import DailyPage


class App(ctk.CTk):
    NAV = [("general", "🧰  通用 / 工具"),
            ("launch_login", "启动登录"),
           ("daily", "🐉  日常一条龙"),
           ("sniper", "🗡  秒装备"), ("treasure_map", "🗺  宝图"),
           ("escort", "🚚  运镖"), ("secret_realm", "👹  秘境降妖"),
           ("dungeon", "🏰  刷副本"), ("freeclick", "🎯  自由点击"),
           ("settings", "⚙  设置"), ("about", "ⓘ  关于")]
    # 可运行任务页（有 runner/pump/update_game_pill），App 的定时器/热键/关闭钩子按此遍历。
    # general 也在内：它的「一键组队」会跑后台任务，需要 pump 抽日志、关闭时停 runner。
    RUNNABLE_KEYS = ("general", "launch_login", "daily", "sniper", "freeclick", "treasure_map",
                     "escort", "secret_realm", "dungeon")

    def __init__(self, compact=False):
        super().__init__()
        self._compact = bool(compact)
        # 每次开机先留一份世代快照：出事时能退回「这次启动前的样子」（见 core/config 写盘护栏）。
        cfg_mod.snapshot_config()
        self.cfg = cfg_mod.load_config()
        # 全局窗口识别按进程名过滤（避免把终端/编辑器等同名标题窗口当游戏号）；GUI 各窗口操作据此生效。
        win_mod.set_game_process(self.cfg.get("window_process") or win_mod.DEFAULT_GAME_PROCESS_SPEC)
        # 游戏安装目录（读角色名用；空=自动从窗口进程反推）。见 core/accounts 模块头。
        accounts.set_game_dir(self.cfg.get("game_dir"))
        accounts.set_launcher_path((self.cfg.get("account_launch") or {}).get("launcher_path"))
        mode = self.cfg.get("appearance", "dark")
        ctk.set_appearance_mode(mode if mode in ("dark", "light") else "dark")
        self.title("梦幻小助手")
        if self._compact:
            self.geometry("440x420")
            self.minsize(440, 420)
            self.resizable(False, False)
        else:
            self.geometry("1360x720")
            self.minsize(1180, 640)
        self.configure(fg_color=T.BG)
        # ⚠ 绝对不要在这里 self.withdraw()。customtkinter 的 CTk 有
        # `_withdraw_called_before_window_exists` 标志：只要在窗口首次显示（第一次
        # update()/mainloop()）之前调过 withdraw()，它内部的「首次显示时自动 deiconify」
        # 就永久失效；随后 _windows_set_titlebar_color() 复位状态时又会
        # `state(self._state_before_windows_set_titlebar_color)`（该值此时是 "normal"），
        # 把窗口按 withdrawn 的映射状态还原回去 —— 结果就是窗口永远不显示，
        # 现象为「启动页跑完一闪就没了 / 什么都不出来」。踩过，别再试。
        # 想遮住建界面过程，用 reveal_with_overlay() 的同根不透明遮罩，而不是隐藏窗口。

        self.fonts = T.build_fonts()
        self.game_win = win_mod.GameWindow(self.cfg.get("window_title", "梦幻西游"))
        self._tick_count = 0
        self._game_connected = None   # 缓存连接状态，只在变化时刷新药丸
        self._locating = False        # 防止多个后台定位线程叠加
        self._ui_queue = queue.Queue()  # 后台线程 -> 主线程的任务队列（见 App.ui_post）
        self.float_log = None         # 悬浮日志窗实例（None=没收起，主界面正常显示）
        self.quick_panel = None       # compact 形态的日志 sink（由 Spec 04 换成正式面板）
        self._compact_placeholder = None
        # 跨形态唯一状态真源。页面只管各自按钮，compact / full / floating 的展示统一从这里取。
        # ⚠ 属性名【绝不能叫 state】：tk.Tk 自带 state(newstate) 方法（wm state），CTk 的
        # mainloop/_windows_set_titlebar_color 会调 `self.state(...)` 复位窗口显示状态；一旦被
        # AppState 实例覆盖，启动进 mainloop 就抛 `TypeError: 'AppState' object is not callable`。
        # 踩过（提交「统一窗口与任务状态管理」引入），故一律用 ui_state。
        self.ui_state = AppState(ui_mode=UI_COMPACT if self._compact else UI_FULL)
        self.runtime = get_runtime()  # 唯一运行登记、停止、日志与重跑入口

        self.grid_columnconfigure(1, weight=1)   # 中间内容区随窗口拉伸
        self.grid_columnconfigure(2, weight=0)   # 右侧全局日志列固定宽
        self.grid_rowconfigure(0, weight=1)
        if self._compact:
            # 小窗不建任务页，但 _tick / 急停遍历 self.pages，故保持一个空字典。
            self.pages = {}
            self.quick_panel = QuickStartPanel(self, self)
            self.quick_panel.grid(row=0, column=0, columnspan=3, sticky="nsew")
            self.reveal_compact()
        else:
            self._build_sidebar()
            self._build_log_panel()   # 先建日志面板：各页 _log_line 都往这写，必须先于建页
            self._build_pages()
            self._show("general")

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(150, self._tick)
        self._hotkey_down = False
        self.after(60, self._poll_hotkey)
        # 界面显示后趁空闲把其余页面逐个预建好，首次切过去即秒开（每个间隔开，单帧不卡）。
        if not self._compact:
            self.after(800, self._prebuild_idle)
        # 兜底显示：外层正常会调 reveal_with_overlay()（它建完页就亮窗口并置 _revealed）。
        # 真走到这里说明没人调（直接跑本文件 / 启动回退路径），窗口不能一直藏着。
        self.after_idle(self._ensure_revealed)

    @property
    def is_compact(self):
        return self._compact

    def reveal_compact(self):
        """显示已构建的一键启动小窗。"""
        if not self.is_compact:
            return
        self._ensure_revealed()

    def enter_full(self):
        """原地从 compact 展开完整界面；重复调用不产生副作用。"""
        if not self.is_compact:
            return
        quick_logs = []
        quick_panel = getattr(self, "quick_panel", None)
        if quick_panel is not None:
            try:
                quick_logs = quick_panel.log_history()
            except Exception:
                pass
        for attr in ("quick_panel", "_compact_placeholder"):
            widget = getattr(self, attr, None)
            if widget is not None:
                try:
                    widget.destroy()
                except Exception:
                    pass
            setattr(self, attr, None)

        self._compact = False
        self._set_state(ui_mode=UI_FULL)
        self.geometry("1360x720")
        self.minsize(1180, 640)
        self.resizable(True, True)
        self.grid_columnconfigure(1, weight=1)
        self.grid_columnconfigure(2, weight=0)
        self.grid_rowconfigure(0, weight=1)
        self._build_sidebar()
        self._build_log_panel()
        for msg, level, source in quick_logs:
            T.append_log(self.log, msg, level, source)
        self._build_pages()
        self._build_pages_with_overlay()
        self._show("general")
        self.after(800, self._prebuild_idle)

    def _build_sidebar(self):
        bar = ctk.CTkFrame(self, fg_color=T.SIDEBAR, corner_radius=0, width=210)
        bar.grid(row=0, column=0, sticky="nsew")
        bar.grid_propagate(False)
        bar.grid_rowconfigure(99, weight=1)

        ctk.CTkLabel(bar, text="梦幻小助手", font=self.fonts["title"], text_color=T.TEXT).grid(
            row=0, column=0, sticky="w", padx=22, pady=(24, 22))

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

    def log_line(self, msg, level="info", source=None):
        """统一日志出口（所有页面/任务都调它）。source 非空时在行首加暗色来源标签，如「秒装备 ›」。

        三个去处、同一份内容：主界面右侧的全局面板、（若已收起的）悬浮日志窗，
        以及 compact 形态的一键启动面板。插行/裁行的实现见 theme.append_log。"""
        log = getattr(self, "log", None)
        if log is not None:
            T.append_log(log, msg, level, source)
        fl = getattr(self, "float_log", None)
        if fl is not None:
            fl.append(msg, level, source)
        qp = getattr(self, "quick_panel", None)
        if qp is not None:
            qp.append(msg, level, source)

    # ---------------- 统一状态：跨小窗 / 主界面 / 悬浮窗的单一真源 ----------------
    def _set_state(self, **changes):
        """原子更新状态快照，并立即通知已存在的界面形态刷新。

        仅在主线程调用。状态未变化不重复刷新，避免 _tick 的轮询引发无意义重绘。"""
        current = getattr(self, "ui_state", None)
        if current is None:
            return
        updated = replace(current, **changes)
        if updated == current:
            return
        self.ui_state = updated
        panel = getattr(self, "quick_panel", None)
        if panel is not None:
            try:
                panel.refresh_state(updated)
            except Exception:
                pass
        fl = getattr(self, "float_log", None)
        if fl is not None:
            try:
                fl.refresh_state()
            except Exception:
                pass

    def _sync_task_state(self):
        """展示运行服务的生命周期；不扫描页面或线程属性。"""
        phase, label, labels = self.runtime.state()
        self._set_state(task_state=phase, task_label=label, active_labels=labels)
        return self.ui_state

    # ---------------- 收起为悬浮日志窗（细长条，可摆到游戏窗口边上） ----------------
    def collapse_to_float(self, auto=False):
        """把主界面收起成一条悬浮日志窗。已经收起了就把悬浮窗抬到最前（幂等，重复点不叠窗）。

        两个触发点：① 通用页「收起为悬浮日志窗」按钮（手动，auto=False）；
        ② 任意模块脚本启动成功后由 on_task_started 自动调（auto=True，主力用法——开跑就收掉界面）。
        ⚠ auto=True 时【不抢焦点】：任务刚启动，紧接着就要把游戏窗口切前台（约束 9），
        这里 focus_force 会把它顶掉；手动收起（用户正看着界面）才置前。"""
        if self.float_log is not None:
            try:
                self.float_log.lift()
            except Exception:
                pass
            return
        try:
            from .float_log import FloatLogWindow
            self.float_log = FloatLogWindow(self, focus=not auto)
        except Exception as e:
            self.float_log = None
            self.toast(f"打开悬浮日志窗失败：{e}")
            return
        # 先建好悬浮窗再把主界面藏起来：主界面没了也不至于桌面上什么都不剩（撤不回来）。
        try:
            self.withdraw()
        except Exception:
            pass
        self._set_state(ui_mode=UI_FLOATING)
        self.log_line("已收起为悬浮窗，点「展开主界面」还原。", "info")

    # ---------------- 任务运行态（悬浮窗的「开始/停止」按钮据此刷新） ----------------
    def on_quick_start_started(self, runner):
        """启动成功后绑定展示来源，紧凑窗口保持不变。"""
        self.runtime.bind(runner, "启动登录")
        self._sync_task_state()

    def on_task_started(self, label, restart=None, runner=None):
        """preflight 与登记成功后绑定重跑入口并收起界面。"""
        if runner is None:
            return
        self.runtime.bind(runner, label or "任务", restart)
        self.collapse_to_float(auto=True)
        self._sync_task_state()

    def restart_last_task(self):
        ok, problems = self.runtime.restart()
        if not ok:
            for problem in problems:
                self.toast(problem)
        return ok

    def task_state(self):
        state = self._sync_task_state()
        return self.runtime.any_running(), list(state.active_labels)

    def close_float_log(self, save=True):
        """收起悬浮窗、还原主界面（悬浮窗的标题栏 X 与「展开主界面」都走这里）。
        save=True 时把悬浮窗的位置/尺寸/置顶偏好记进 config，下次收起按原样摆回。"""
        fl = self.float_log
        self.float_log = None
        if fl is not None:
            if save:
                try:
                    fl._save_cfg(topmost=bool(fl.var_top.get()), geometry=fl._current_geometry())
                except Exception:
                    pass
            try:
                fl.destroy()
            except Exception:
                pass
        try:
            self.deiconify()
            self.lift()
        except Exception:
            pass
        self._set_state(ui_mode=UI_FULL)
        # 主界面重建后可能被别的窗口压着，抢一次前台（失败也无所谓，大不了用户自己点一下）
        try:
            self.after(60, self.focus_force)
        except Exception:
            pass

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
        "launch_login": LaunchLoginPage,
        "daily": DailyPage,
        "sniper": SniperPage,
        "freeclick": FreeClickPage,
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
        """跑掉 Tk 待处理事件（含重绘）并回一次事件循环。只在窗口需要立刻可见时用，
        不是给建页循环用的——每次都是一次同步整窗重绘，代价高。"""
        try:
            self.update()
        except Exception:
            pass

    def _safe_update_idletasks(self):
        """只跑空闲任务（几何计算 / 进度条重绘），**不**处理重绘事件、不进事件循环。
        建页期间用它推进度条：开销远小于 update()，且不会把半成品界面刷到屏幕上。"""
        try:
            self.update_idletasks()
        except Exception:
            pass

    def _ensure_revealed(self):
        """确保窗口已经显示（幂等）。正常由 reveal_with_overlay() 调用；
        直接跑本文件（没人调 reveal）时由 __init__ 排的 after_idle 兜底。"""
        if getattr(self, "_revealed", False):
            return
        self._revealed = True
        try:
            self.deiconify()
        except Exception:
            pass

    def _build_pages_with_overlay(self):
        """在本窗口上盖一层同根、不透明的「正在准备界面…」遮罩，然后在其后把全部页面建好。

        为什么是「亮窗口 + 盖遮罩」而不是「先把窗口藏起来建完再显示」：
        customtkinter 的 CTk 明确不支持「窗口首次显示前 withdraw()」（见 __init__ 里的说明），
        那样做窗口会永远不显示。所以窗口从一开始就是可见的，靠这层遮罩挡住未完成的界面——
        它和窗口同一个 Tk 根、直接 place 铺满，因此不存在「露出黑底」的窗口期。

        黑屏/闪屏的真正来源是【重绘次数】，不是窗口可见性：老实现每建一页都 self.update()，
        每次都同步重绘整窗（1360x720 上几百个 CTk 控件）。现在建页期只跑
        update_idletasks()（算几何、不重绘），建完撤遮罩再重绘一次，全程只有一次整窗重绘。"""
        import tkinter as tk
        from tkinter import ttk

        bg, fg, dim, acc, trough = (T.resolve(T.BG), T.resolve(T.TEXT), T.resolve(T.TEXT_DIM),
                                    T.resolve(T.ACCENT), T.resolve(T.SURFACE_2))
        total = len(self.PAGE_CLASSES)
        ov = tk.Frame(self, bg=bg)
        ov.place(x=0, y=0, relwidth=1, relheight=1)
        tk.Label(ov, text="梦幻小助手", bg=bg, fg=fg,
                 font=("Microsoft YaHei UI", 16, "bold")).place(relx=0.5, rely=0.43, anchor="center")
        tk.Label(ov, text="正在准备界面…", bg=bg, fg=dim,
                 font=("Microsoft YaHei UI", 11)).place(relx=0.5, rely=0.51, anchor="center")
        pb = None
        try:
            style = ttk.Style(self)
            style.theme_use("default")
            style.configure("Ovl.Horizontal.TProgressbar", troughcolor=trough,
                            background=acc, bordercolor=bg, lightcolor=acc, darkcolor=acc)
            # determinate 模式：进度由「已建页数」驱动，比 indeterminate 更实在。
            # maximum 取 total+1：determinate 进度条到 maximum 会【回绕到 0】（实测），
            # 若用 total，建完最后一页进度条会瞬间清零。多留一格，末页之后再补最后一步。
            pb = ttk.Progressbar(ov, mode="determinate", maximum=max(2, total + 1), value=0,
                                 length=240, style="Ovl.Horizontal.TProgressbar")
            pb.place(relx=0.5, rely=0.59, anchor="center")
        except Exception:
            pb = None

        # 先把窗口显示出来（CTk 的首次显示），再把遮罩这一帧画上去，用户看不到中间态。
        self._ensure_revealed()
        self._safe_update()

        def _on_step():
            # 只推进度条 + 跑空闲任务：建页期不再整窗重绘，黑屏/闪屏的主要来源就此消失。
            if pb is not None:
                try:
                    pb.step(1)
                except Exception:
                    pass
            self._safe_update_idletasks()

        try:
            self.build_all_pages(on_step=_on_step)
        finally:
            # 无论建页成功与否都要撤遮罩，避免异常把界面永久挡在遮罩后面。
            if pb is not None:
                try:
                    pb.step(1)              # 补最后一步：进度条走到满格（见上面 maximum 的说明）
                except Exception:
                    pass
            self._safe_update_idletasks()
            try:
                ov.destroy()
            except Exception:
                pass
            self._ensure_revealed()
            self._safe_update()

    def reveal_with_overlay(self):
        """完整界面的公开显示入口，保留既有调用点。"""
        self._build_pages_with_overlay()

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
        fl = getattr(self, "float_log", None)
        if fl is not None:
            fl.apply_theme()

    def toast(self, msg):
        """简单的浮层提示。收起为悬浮窗时改由悬浮窗代显（主界面被 withdraw 了，贴在它身上的浮层
        会跟着一起看不见，急停/任务结束这类提示就白弹了）。"""
        fl = getattr(self, "float_log", None)
        if fl is not None:
            fl.toast(msg)
            return
        lbl = ctk.CTkLabel(self, text=msg, font=self.fonts["body"], fg_color=T.ACCENT,
                           text_color=T.ON_ACCENT, corner_radius=T.RADIUS_SM, padx=16, pady=8)
        lbl.place(relx=0.99, rely=0.97, anchor="se")
        self.after(1600, lbl.destroy)

    def _tick(self):
        # 先兑现后台线程投递的主线程任务（见 ui_post）——放在最前，枚举一完成就渲染，不等下一拍。
        self._drain_ui_queue()
        # 统一流先分发到三个日志视图，再刷新页面控件。
        for runner, level, msg, source in self.runtime.drain_logs():
            self.log_line(msg, level, source)
            qp = getattr(self, "quick_panel", None)
            if qp is not None and getattr(qp, "runner", None) is runner:
                qp._track_progress(msg)
        # 页面 pump 仅刷新自己的控件。
        for k in self.RUNNABLE_KEYS:
            p = self.pages.get(k)
            if p:
                p.pump()
        qp = getattr(self, "quick_panel", None)
        if qp is not None:
            qp.pump()
        self._sync_task_state()
        # 每约 1.2s 检测一次游戏窗口（放后台线程，避免阻塞 UI 造成滑动卡顿）
        self._tick_count += 1
        if self._tick_count % 8 == 0:
            self._kick_locate()
        self.after(150, self._tick)

    def ui_post(self, fn):
        """【线程安全】把 fn 排到主线程执行，供后台线程回填 UI 用。

        ⚠ 别改回「后台线程直接调 Tk 的 after(0, fn)」：启动期（App.__init__ 里建页/
        reveal_with_overlay 建其余页）mainloop 还没进，Tk 会抛
        `RuntimeError: main thread is not in main loop`；调用点的 `except Exception: pass`
        把异常一吞，回调就【永久丢失】——现象正是「窗口归一化首次打开一直停在
        『正在检测窗口…』，点一次『刷新』才出来」（那时 mainloop 已在跑，after 才成功）。
        队列 + _tick 抽取则与 mainloop 无关，任何时候投递都必达。"""
        self._ui_queue.put(fn)

    def _drain_ui_queue(self):
        """在主线程抽干 ui_post 投递的任务。单项异常不拖垮其余项。"""
        q = getattr(self, "_ui_queue", None)
        if q is None:
            return
        while True:
            try:
                fn = q.get_nowait()
            except queue.Empty:
                return
            except Exception:
                return
            try:
                fn()
            except Exception:
                pass

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
                # 最小化或被遮挡不等于窗口消失。运行任务会在实际操作前 activate()
                # 还原并置前，这里只负责判断目标窗口句柄是否仍存在。
                all_wins = win_mod.locate_all(title, offset, include_minimized=True)
                found, summary = self._compute_target_state(all_wins, targets)
            except Exception:
                found, summary = False, ""
            # 回主线程更新（走 ui_post 队列，与 mainloop 是否已进入无关）
            try:
                self.ui_post(lambda: self._apply_game_state(found, summary))
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def _compute_target_state(all_wins, targets):
        """据「已枚举的窗口 + targets 选择」算出药丸要显示的 (是否连上, 摘要串)。
        纯函数，跑在后台线程，不碰 Tk。号名用真实角色名（认不出自动退回「号N」，见 core/accounts）。"""
        if not all_wins:
            return False, ""
        try:
            # 完整窗口序列同步显示顺序；只读逐窗识别的身份缓存，不对后台窗口 OCR。
            labels = accounts.sync_window_labels(all_wins)
        except Exception:
            labels = [accounts.fallback_label(i) for i in range(len(all_wins))]
        if targets.get("multi"):
            idxs = targets.get("multi_indices") or list(range(len(all_wins)))
            sel = [i for i in idxs if 0 <= i < len(all_wins)] or list(range(len(all_wins)))
            return True, "、".join(labels[i] for i in sel) + " · 多开"
        i = targets.get("single_index", 0)
        if not (isinstance(i, int) and 0 <= i < len(all_wins)):
            i = 0
        return True, f"{labels[i]} · 单开"

    def _apply_game_state(self, found, summary=""):
        self._locating = False
        state = (found, summary)
        if state == self._game_connected:
            return  # 状态没变就不动控件，省掉无谓重绘
        self._game_connected = state
        self._set_state(game_state=GAME_READY if found else GAME_ABSENT, game_summary=summary)
        for k in self.RUNNABLE_KEYS:
            p = self.pages.get(k)
            if p:
                p.update_game_pill(found, summary)
                # 这次枚举顺带把角色名（core/accounts 缓存）算出来了；页里那些「将整理：…」之类
                # 构建时静态渲染的文案要重渲染一遍，否则会一直停在构建那一刻的「号1」。
                refresh = getattr(p, "_refresh_targets_labels", None)
                if callable(refresh):
                    try:
                        refresh()
                    except Exception:
                        pass

    def open_window_picker(self, after=None, captain_ns=None):
        """打开「选择窗口」对话框（各任务页共用）。关闭后刷新配置并强制刷新药丸。
        captain_ns: 传入则可在卡片上直接指定队长，写入 tasks.<captain_ns>.captain_index。"""
        from .window_picker import WindowPickerDialog

        def _done():
            self.cfg = cfg_mod.load_config()
            self._game_connected = None   # 选择可能变了，强制下次 tick 刷新药丸
            accounts.invalidate()         # 序号→角色名 的缓存也作废，否则卡片提示会显示旧名字
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
        """把选中的号窗口还原到「激活尺寸组」标定时记录的分辨率（被手操拉大后一键复位）。"""
        targets = self.cfg.get("targets", {})
        base = calib.active_size(self.cfg)      # 真源是尺寸组；targets.base_size 只是它的镜像
        if not base or len(base) < 2:
            self.toast("请先标定一次，标定时会自动记住窗口尺寸（形成尺寸组）")
            return
        title = self.cfg.get("window_title", "梦幻西游")
        offset = self.cfg.get("window_offset", [0, 0])
        ok, total, actual = win_mod.restore_targets_size(title, offset, targets, base)
        if total == 0:
            self.toast(f"没找到/没选中目标窗口（标题含「{title}」），请先「选择窗口」")
            return
        w, h = int(base[0]), int(base[1])
        valid = [tuple(s) for s in actual if s]
        if ok == total:
            self.toast(f"已还原 {ok}/{total} 个号到 {w}×{h}")
        elif len(valid) == total and max(s[0] for s in valid) - min(s[0] for s in valid) <= 4 \
                and max(s[1] for s in valid) - min(s[1] for s in valid) <= 4:
            # 新外壳锁定纵横比：各号已统一到同一可达尺寸（±4px 吸附误差），但目标尺寸落在比例线外
            # → 提示在目标尺寸下重新标定一次。
            self.toast(f"已把 {total} 个号统一到 {valid[0][0]}×{valid[0][1]}；"
                       f"新版客户端锁定窗口比例，{w}×{h} 不可达，建议在该尺寸下重新标定一次")
        else:
            # 有号没还原成功——未提权（UIPI 拒绝）或窗口不支持调整
            hint = "" if win_mod.is_admin() else "；请用管理员身份运行（启动时 UAC 点「是」）"
            self.toast(f"还原 {ok}/{total} 个号；部分窗口调整失败{hint}")
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
        return self.runtime.any_running()

    def stop_all_tasks(self):
        """按钮、热键、甩角及关闭共同使用运行服务。"""
        n = self.runtime.stop_all()
        self._sync_task_state()
        return n

    def _on_close(self):
        if self.is_compact:
            qp = getattr(self, "quick_panel", None)
            if qp is not None:
                qp.on_close()
                return
        self.stop_all_tasks()
        self.destroy()



def _wake_existing_windows(app):
    """后台启动扫描：逐窗唤出后识别当前标签条，禁止集中扫描后台像素。"""
    from mhxy.core import accounts
    from mhxy.core import window as win_mod

    cfg = app.cfg
    title = cfg.get("window_title", "梦幻西游")
    offset = cfg.get("window_offset", [0, 0])
    failures = []

    def identify(win):
        try:
            # wake_all_to_front 仅在 activate 校验通过后调用；识别必须在切下个窗口前完成。
            accounts.labels_for([win])
        except Exception:
            failures.append(win)

    try:
        total, woken = win_mod.wake_all_to_front(title, offset, on_woken=identify)
    except Exception as exc:
        message = "启动唤窗与角色识别失败：%s" % exc
        app.ui_post(lambda: app.log_line(message, "warn", "启动"))
        return

    def apply():
        if not total:
            return
        app.log_line(
            "启动唤出游戏窗口：检测到 %d 个，%d 个成功切到前台；已逐窗执行角色名检测。" % (total, woken),
            "info" if woken and not failures else "warn", "启动")
        if failures:
            app.log_line("部分窗口角色名检测失败，暂使用已有身份或号N。", "warn", "启动")
        # 常驻定位只读角色缓存；使启动时已绘制的号N和目标摘要立即重新计算。
        app._game_connected = None
        app._kick_locate()

    app.ui_post(apply)
