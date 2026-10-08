# -*- coding: utf-8 -*-
"""DungeonPage：独立页面，保留既有布局与任务控制。"""
import threading
import customtkinter as ctk
from . import theme as T
from ..core import config as cfg_mod
from ..core import accounts
from ..core.runtime import get_runtime
from ..tasks import get_task
from ..tasks.base import dungeon_tasks
from ..core.teaming import TEAM_REQUIRED_REGIONS, TEAM_REQUIRED_TEMPLATES
from .widgets import Card, Pill, load_thumb, bind_wraplength
from .window_labels import resolve_window_labels


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
        self._win_labels = []            # 已选窗口的显示名（「角色名（等级）」，认不出是「号N」），与 _win_count 同序
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
        self.var_captain = ctk.StringVar(value="（未选窗口）")
        self.opt_captain = ctk.CTkOptionMenu(cap, variable=self.var_captain, values=["（未选窗口）"],
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

        self.lbl_calib = ctk.CTkLabel(left, text="", font=self.fonts["small"], text_color=T.TEXT_DIM,
                                      justify="left")
        self.lbl_calib.grid(row=4, column=0, sticky="ew", padx=16, pady=(2, 14))
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
        """据当前 self._win_count/_win_labels + 组队标定 + 选中副本标定，渲染队长下拉与就绪状态（纯本地数据，秒回）。"""
        n = self._win_count
        labels = list(self._win_labels)[:n] + [accounts.fallback_label(i)
                                               for i in range(len(self._win_labels), n)]
        opts = labels or ["（未选窗口）"]
        self.opt_captain.configure(values=opts)
        tc = cfg_mod.task_config(self.app.cfg, self._selected) if self._selected else {}
        cap = tc.get("captain_index", 0)
        if not (0 <= cap < n):
            cap = 0
        self.var_captain.set(labels[cap] if n else "（未选窗口）")

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
            tail = "　✓ 可运行" if ready else "　（需选 ≥2 个号 且组队+副本标定齐全）"
        self.lbl_calib.configure(
            text=team_line
                 + f"副本标定（{self._selected_title()}）：区域 {rdone}/{rneed}，模板 {tdone}/{tneed}\n"
                 + f"已选 {n} 个号，队长={labels[cap] if n else '—'}" + tail)

    def _kick_count_windows(self):
        """后台枚举已选窗口数，变了再回主线程重渲染。token 丢弃过期结果。"""
        title = self.app.cfg.get("window_title", "梦幻西游")
        offset = self.app.cfg.get("window_offset", [0, 0])
        targets = self.app.cfg.get("targets", {})
        token = object()
        self._count_token = token

        def work():
            labels = resolve_window_labels(title, offset, targets)
            n = len(labels)

            def apply():
                if token is not getattr(self, "_count_token", None):
                    return
                if n != self._win_count or labels != self._win_labels:
                    self._win_count = n
                    self._win_labels = labels
                    self._render_team_status()

            try:
                self.app.ui_post(apply)
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
            self._log_line("已组队：跳过组队，直接开刷", "info")
        else:
            self._log_line("先组队再开刷", "info")

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
            self._log_line("跑完自动退队", "info")
        else:
            self._log_line("跑完保留队伍", "info")

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

    def _captain_index_from_label(self, label):
        """把队长下拉框的显示名映射回窗口序号。

        下拉项现在是【角色名（等级）】（认不出才是「号N」），不能再拿字符串去 replace("号","") 解析
        ——故先按 self._win_labels 反查下标，「号N」形态作为兜底。"""
        s = str(label)
        labels = list(self._win_labels)
        if s in labels:
            return max(0, min(len(labels) - 1, labels.index(s)))
        if s.startswith("号") and s[1:].isdigit():
            return int(s[1:]) - 1
        return 0

    def _on_captain(self, choice):
        if not self._win_count or not self._selected:
            return
        idx = max(0, min(self._win_count - 1, self._captain_index_from_label(choice)))
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
        if self.runner and get_runtime().is_running(self.runner):
            get_runtime().stop(self.runner)
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
        self.runner = get_runtime().create_runner(task_cls(), self.app.cfg)
        ok, problems = get_runtime().start(self.runner)
        if not ok:
            for p in problems:
                self._log_line("无法启动：" + p, "error")
            self.runner = None
            return
        self._log_line(f"开始跑副本：{self._selected_title()}", "hit")
        self.btn_run.configure(text="■  停止", fg_color=T.DANGER, hover_color=T.DANGER_HOVER, state="normal")
        self.app.on_task_started(f"副本·{self._selected_title()}", self._toggle_run, self.runner)

    def _apply_params(self):
        """启动前把队长序号写回【选中副本】的配置（下拉框变更时已即时存，这里兜底再存一次）。"""
        if not self._selected:
            return
        cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(cfg, self._selected)
        if self._win_count:
            idx = max(0, min(self._win_count - 1, self._captain_index_from_label(self.var_captain.get())))
            tc["captain_index"] = idx
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
            self._log_line(f"⚠ 实战模式（{self._selected_title()}）：会真组队、真跑副本", "warn")
        else:
            self._log_line(f"演练模式（{self._selected_title()}，只识别自检）", "info")

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
            if not get_runtime().is_running(self.runner) and self.btn_run.cget("text") != self.RUN_LABEL:
                self._on_runner_finished()

    def _log_line(self, msg, level="info"):
        # 日志统一汇到 App 右侧全局面板，按本页 LOG_SOURCE 打来源标签。
        self.app.log_line(msg, level, getattr(self, "LOG_SOURCE", None))

    def _clear_log(self):
        self.app.clear_log()
