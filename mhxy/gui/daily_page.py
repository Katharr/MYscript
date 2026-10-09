# -*- coding: utf-8 -*-
"""DailyPage：独立页面，保留既有布局与任务控制。"""
import customtkinter as ctk
from . import theme as T
from ..core import config as cfg_mod
from ..core.runtime import get_runtime
from ..tasks import get_task
from ..tasks.base import dungeon_tasks
from ..tasks.daily import CHAINABLE
from ..core.teaming import TEAM_REQUIRED_REGIONS, TEAM_REQUIRED_TEMPLATES
from .widgets import Card, Pill, bind_wraplength


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
        self._rows = []           # 与 _steps 对齐的行控件 [{"frame","name","badge"}]
        self._drag = None         # 拖动中的状态 {"idx": 当前下标}

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
        ctk.CTkLabel(head, text="任务清单", font=self.fonts["h2"],
                     text_color=T.TEXT).grid(row=0, column=0, sticky="w")
        self.lbl_count = ctk.CTkLabel(head, text="", font=self.fonts["small"], text_color=T.TEXT_DIM)
        self.lbl_count.grid(row=0, column=1, sticky="e")
        hint = ctk.CTkLabel(head, text="拖动 ⠿ 排序　·　开关启用 / 停用",
                            font=self.fonts["small"], text_color=T.TEXT_DIM, anchor="w")
        hint.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        bind_wraplength(hint)
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

    def _selected_dungeon(self):
        """副本中枢当前选中的副本名（tasks.dungeon.selected）；非法/缺失退回第一个已收录副本。"""
        names = [c.name for c in dungeon_tasks()]
        if not names:
            return None
        sel = cfg_mod.task_config(self.app.cfg, "dungeon").get("selected")
        return sel if sel in names else names[0]

    def _task_title(self, name):
        if name == "dungeon":
            sel = self._selected_dungeon()
            cls = get_task(sel) if sel else None
            return f"刷副本 · {cls.title}" if cls else "刷副本（未收录副本）"
        cls = get_task(name)
        return cls.title if cls else name

    def _task_status(self, name):
        """返回 (模式串, 是否已就绪)。模式=演练/实战；就绪=必要区域+模板都已标定。
        「刷副本」步特殊：读选中副本自身命名空间，就绪=副本标定齐 +（未勾「已组队」时）组队标定齐。"""
        if name == "dungeon":
            return self._dungeon_step_status()
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

    def _dungeon_step_status(self):
        """「刷副本」步的 (模式, 就绪)：模式/标定都看选中副本自身；多开≥2 是运行期条件，留给链内 preflight。"""
        sel = self._selected_dungeon()
        if not sel:
            return "演练", False
        sub = cfg_mod.task_config(self.app.cfg, sel)
        mode = "演练" if sub.get("dry_run", True) else "实战"
        spec = getattr(get_task(sel), "CALIBRATION", {}) or {}
        need_r = [t[0] for t in spec.get("regions", []) if not (len(t) >= 4 and t[3])]
        need_t = [t[0] for t in spec.get("templates", [])]
        regions, templates = sub.get("regions", {}), sub.get("templates", {})
        self_ok = all(regions.get(k) for k in need_r) and all(templates.get(k) for k in need_t)
        if sub.get("skip_team", False):     # 已勾「已组队」：不查组队标定
            return mode, self_ok
        team = cfg_mod.task_config(self.app.cfg, "teaming")
        treg, ttpl = team.get("regions", {}), team.get("templates", {})
        team_ok = (all(treg.get(k) for k in TEAM_REQUIRED_REGIONS)
                   and all(ttpl.get(k) for k in TEAM_REQUIRED_TEMPLATES))
        return mode, (self_ok and team_ok)

    def _render_steps(self):
        # 内容/各任务就绪状态没变就别重建：切页时 refresh 反复调到这里，整段重画是「切页卡顿」来源之一。
        # 状态串纳入签名——别处页面刚标定/切换演练实战，切回来时这里要能反映出最新状态。
        sig = [(s["task"], s["enabled"], self._task_status(s["task"])) for s in self._steps]
        if sig == getattr(self, "_steps_sig", None):
            return
        self._steps_sig = sig
        for w in self.list_frame.winfo_children():
            w.destroy()
        self._rows = []
        self._drag = None
        for i, step in enumerate(self._steps):
            self._rows.append(self._build_row(i, step))
        self._renumber()

    def _build_row(self, i, step):
        """一张任务卡：左=拖动手柄，左二=执行序号徽标，中=标题+状态，右=启用开关。
        左键按住手柄上下拖动即可排序（见 _drag_*）。"""
        name = step["task"]
        row = ctk.CTkFrame(self.list_frame, fg_color=T.SURFACE_2, corner_radius=T.RADIUS_SM)
        row.grid(row=i, column=0, sticky="ew", pady=5, padx=4)
        row.grid_columnconfigure(2, weight=1)

        # 左：拖动手柄（按住左键上下拖动整行排序）。光标设成移动样式（失败不致命）。
        grip = ctk.CTkLabel(row, text="⠿", font=self.fonts["h2"], text_color=T.TEXT_DIM, width=22)
        grip.grid(row=0, column=0, rowspan=2, sticky="ns", padx=(10, 2), pady=8)
        try:
            grip.configure(cursor="fleur")
        except Exception:
            pass
        grip.bind("<Button-1>", lambda e, r=row: self._drag_start(e, r))
        grip.bind("<B1-Motion>", self._drag_motion)
        grip.bind("<ButtonRelease-1>", self._drag_end)

        # 左二：执行序号圆形徽标（启用=蓝底数字按先后编号；停用=灰底圆点，_renumber 里填）
        badge = ctk.CTkLabel(row, text="", font=self.fonts["body_b"], width=26, height=26,
                             corner_radius=13, text_color=T.ON_ACCENT, fg_color=T.ACCENT)
        badge.grid(row=0, column=1, rowspan=2, padx=(2, 12), pady=8)

        # 中：标题（可换行）+ 状态条（演练/实战药丸 + 就绪提示），独占可伸展列
        mid = ctk.CTkFrame(row, fg_color="transparent")
        mid.grid(row=0, column=2, rowspan=2, sticky="ew", pady=8)
        mid.grid_columnconfigure(0, weight=1)
        title = ctk.CTkLabel(mid, text=self._task_title(name), font=self.fonts["body_b"],
                             text_color=T.TEXT, anchor="w")
        title.grid(row=0, column=0, sticky="ew")
        bind_wraplength(title)

        mode, ready = self._task_status(name)
        bar = ctk.CTkFrame(mid, fg_color="transparent")
        bar.grid(row=1, column=0, sticky="w", pady=(5, 0))
        dry = (mode == "演练")
        ctk.CTkLabel(bar, text=f"  {mode}  ", font=self.fonts["small"], height=20,
                     corner_radius=T.RADIUS_PILL,
                     fg_color=(T.PILL_OK_BG if dry else T.PILL_DANGER_BG),
                     text_color=(T.SUCCESS if dry else T.DANGER)).pack(side="left")
        ctk.CTkLabel(bar, text=("✓ 已就绪" if ready else "⚠ 还需标定"), font=self.fonts["small"],
                     text_color=(T.SUCCESS if ready else T.WARN)).pack(side="left", padx=(8, 0))

        # 右：启用 / 停用开关（按任务名定位，拖动后下标会变，故 _toggle_step 用 name 不用 idx）
        var = ctk.BooleanVar(value=step["enabled"])
        sw = ctk.CTkSwitch(row, text="", variable=var, width=44,
                           progress_color=T.ACCENT, fg_color=T.BTN, button_color=T.ON_ACCENT,
                           command=lambda nm=name, v=var: self._toggle_step(nm, v))
        sw.grid(row=0, column=3, rowspan=2, padx=(8, 14), pady=8)

        return {"frame": row, "name": name, "badge": badge}

    def _renumber(self):
        """按当前顺序刷新各行的执行序号徽标与「已勾选 x/y」计数（_rows 与 _steps 始终对齐）。"""
        order = 0
        for r, step in zip(self._rows, self._steps):
            if step["enabled"]:
                order += 1
                r["badge"].configure(text=str(order), fg_color=T.ACCENT, text_color=T.ON_ACCENT)
            else:
                r["badge"].configure(text="·", fg_color=T.SURFACE, text_color=T.TEXT_DIM)
        n_on = sum(1 for s in self._steps if s["enabled"])
        self.lbl_count.configure(text=f"已勾选 {n_on}/{len(self._steps)}")

    # ------------------------------------------------------------------
    # 启用切换 / 左键拖动排序 / 保存
    # ------------------------------------------------------------------
    def _toggle_step(self, name, var):
        for s in self._steps:
            if s["task"] == name:
                s["enabled"] = bool(var.get())
                break
        self._save()
        self._render_steps()

    def _drag_start(self, event, row):
        idx = next((k for k, r in enumerate(self._rows) if r["frame"] is row), None)
        if idx is None:
            return
        self._drag = {"idx": idx}
        row.configure(fg_color=T.BTN)   # 提起高亮
        row.tkraise()

    def _drag_motion(self, event):
        if not self._drag:
            return
        cur = self._drag["idx"]
        tgt = self._index_at_pointer()
        if 0 <= tgt < len(self._rows) and tgt != cur:
            self._reorder(cur, tgt)
            self._drag["idx"] = tgt

    def _drag_end(self, event):
        if not self._drag:
            return
        idx = self._drag["idx"]
        self._drag = None
        if 0 <= idx < len(self._rows):
            self._rows[idx]["frame"].configure(fg_color=T.SURFACE_2)
        self._save()
        self._steps_sig = None      # 顺序已变，强制下次干净重建
        self._render_steps()

    def _index_at_pointer(self):
        """指针当前落在第几行（按各行竖直中线判定）；不销毁控件，故拖动中 winfo 几何有效。"""
        py = self.list_frame.winfo_pointery()
        for idx, r in enumerate(self._rows):
            f = r["frame"]
            if py < f.winfo_rooty() + f.winfo_height() / 2:
                return idx
        return len(self._rows) - 1

    def _reorder(self, frm, to):
        """把第 frm 行移到第 to 位：_steps/_rows 同步搬动，再按新次序重排 grid 与序号。
        全程不销毁控件——被按住的手柄控件存活，隐式 grab 不丢，B1-Motion 才能持续触发。"""
        self._steps.insert(to, self._steps.pop(frm))
        self._rows.insert(to, self._rows.pop(frm))
        for idx, r in enumerate(self._rows):
            r["frame"].grid_configure(row=idx)
        self._renumber()

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
        if self.runner and get_runtime().is_running(self.runner):
            get_runtime().stop(self.runner)
            self._log_line("正在停止…", "warn")
            self.btn_run.configure(text="停止中…", state="disabled")
            return
        self._save()
        self.app.cfg = cfg_mod.load_config()
        task_cls = get_task(self.TASK_NAME)
        self.runner = get_runtime().create_runner(task_cls(), self.app.cfg)
        ok, problems = get_runtime().start(self.runner)
        if not ok:
            for p in problems:
                self._log_line("无法启动：" + p, "error")
            self.runner = None
            return
        self.btn_run.configure(text="■  停止", fg_color=T.DANGER, hover_color=T.DANGER_HOVER, state="normal")
        self.app.on_task_started(self.LOG_SOURCE, self._toggle_run, self.runner)

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
            if not get_runtime().is_running(self.runner) and self.btn_run.cget("text") != self.RUN_LABEL:
                self._on_runner_finished()

    def _log_line(self, msg, level="info"):
        # 日志统一汇到 App 右侧全局面板，按本页 LOG_SOURCE 打来源标签。
        self.app.log_line(msg, level, getattr(self, "LOG_SOURCE", None))

    def _clear_log(self):
        self.app.clear_log()
