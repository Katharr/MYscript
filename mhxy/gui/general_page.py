# -*- coding: utf-8 -*-
"""GeneralPage：独立页面，保留既有布局与任务控制。"""
import threading
import customtkinter as ctk
from . import theme as T
from ..core import calib_profiles as calib
from ..core import config as cfg_mod
from ..core import window as win_mod
from ..core import accounts
from ..core.runtime import get_runtime
from ..tasks import get_task
from ..core.teaming import TEAM_REQUIRED_REGIONS, TEAM_REQUIRED_TEMPLATES
from ..core.inventory import required_templates
from .widgets import Card, load_thumb, bind_wraplength
from .window_labels import resolve_window_labels


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
        self.runner_ob = None       # 一键整理运行句柄；与其他任务共享运行服务互斥席位
        self._team_cal_dialog = None    # 「标定（组队）」去重槽（队长ID 走无弹窗直接标定，无需去重槽）
        self._ob_cal_dialog = None      # 「标定（整理背包）」去重槽
        self.btn_ob = None          # 「一键整理」按钮（_refresh_body 每次重建）
        self.switch_ob = None       # 整理背包实战/演练开关
        self.switch_auto_ob = None  # 「自动整理背包」开关（任何任务检测到背包满自动整理）
        self._win_count = 0         # 已选多开窗口数（resolve_targets），供状态行显示
        self._win_labels = []       # 已选窗口的显示名（「角色名（等级）」，认不出是「号N」），与 _win_count 同序
        self.btn_team = None
        self.btn_disband = None     # 「一键解散」按钮（_refresh_body 每次重建）
        self.btn_wake = None        # 「唤出所有游戏窗口」按钮（_refresh_body 每次重建）
        self.btn_close = None       # 「关闭所有游戏窗口」按钮（_refresh_body 每次重建）
        self.btn_float = None       # 「收起为悬浮日志窗」按钮（_refresh_body 每次重建）
        self.btn_leader = None      # 行内队长ID按钮（_refresh_body 每次重建）
        self._leader_thumbs = []    # 行内队长ID缩略图防 GC
        self.lbl_team_status = None
        self.lbl_ob_targets = None  # 「将整理：…」那句（构建时静态渲染，枚举回来要刷，见 _refresh_targets_labels）
        # 标定时「尺寸组已满 3 个」的引导回调：标定入口会调它让用户挑一个旧组替换
        # （见 calibrate_dialog.resolve_profile）。挂个小函数，免得标定模块反向依赖本页。
        self.app.on_profile_full = self._ask_replace_profile
        self._build()

    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ctk.CTkLabel(head, text="通用 / 工具", font=self.fonts["title"], text_color=T.TEXT).pack(anchor="w")

        self.body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.body.grid(row=1, column=0, sticky="nsew")
        self.body.grid_columnconfigure(0, weight=1)
        T.tune_scroll_speed(self.body)
        # 不在构建时枚举窗口（省启动开销）；首次切到本页时 _show 会调 refresh() 填充。

    def _card(self):
        c = Card(self.body)
        c.pack(fill="x", pady=(0, T.SP_3), padx=2)
        return c

    # ------------------------------------------------------------------
    # 窗口 / 界面工具：① 唤出所有游戏窗口 ② 关闭所有游戏窗口（红，需确认，强杀进程）③ 收起为悬浮日志窗
    #   —— 都是「盯脚本跑」这个场景的工具：多开窗口被压住时唤一次；把界面收成细长悬浮窗，
    #      摆到游戏窗口边角上边跑边看日志。
    #      「收起」现在【任意模块脚本一开始跑就自动发生】（App.on_task_started），本按钮是手动入口。
    # ------------------------------------------------------------------
    def _build_window_tools_card(self):
        c = self._card()
        head = ctk.CTkFrame(c, fg_color="transparent")
        head.pack(fill="x", padx=16, pady=(14, 4))
        ctk.CTkLabel(head, text="窗口 / 悬浮日志", font=self.fonts["h2"], text_color=T.TEXT).pack(anchor="w")

        act = ctk.CTkFrame(c, fg_color="transparent")
        act.pack(fill="x", padx=16, pady=(10, 14))
        self.btn_wake = ctk.CTkButton(act, text="🪟  唤出所有游戏窗口", font=self.fonts["btn"],
                                      height=40, width=180, corner_radius=T.RADIUS_SM,
                                      fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER, text_color=T.ON_ACCENT,
                                      command=self._bring_windows_front)
        self.btn_wake.pack(side="left")
        self.btn_close = ctk.CTkButton(act, text="✕  关闭所有游戏窗口", font=self.fonts["btn"],
                                       height=40, width=180, corner_radius=T.RADIUS_SM,
                                       fg_color=T.DANGER, hover_color=T.DANGER_HOVER, text_color=T.ON_ACCENT,
                                       command=self._close_all_windows)
        self.btn_close.pack(side="left", padx=(8, 0))
        self.btn_float = ctk.CTkButton(act, text="📋  收起为悬浮日志窗", font=self.fonts["btn"],
                                       height=40, width=180, corner_radius=T.RADIUS_SM,
                                       fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                                       border_width=1, border_color=T.BORDER,
                                       command=self._collapse_to_float)
        self.btn_float.pack(side="left", padx=(8, 0))

    def _bring_windows_front(self):
        """把所有游戏窗口（含最小化的）还原并切到前台。

        枚举窗口（getAllWindows）+ 逐个抢前台（带重试校验）都不便宜，放后台线程跑，避免卡住界面；
        完成后回主线程弹提示/写日志。"""
        cfg = self.cfg = cfg_mod.load_config()
        self.app.cfg = cfg
        title = cfg.get("window_title", "梦幻西游")
        offset = cfg.get("window_offset", [0, 0])
        btn = getattr(self, "btn_wake", None)
        if btn is not None:
            try:
                btn.configure(state="disabled", text="⏳  正在唤出…")
            except Exception:
                pass

        def work():
            try:
                total, ok = win_mod.wake_all_to_front(
                    title, offset, on_woken=lambda win: accounts.labels_for([win]))
            except Exception:
                total, ok = 0, 0

            def apply():
                if btn is not None:
                    try:
                        btn.configure(state="normal", text="🪟  唤出所有游戏窗口")
                    except Exception:
                        pass
                if not total:
                    self.app.toast(f"没检测到游戏窗口（标题含「{title}」），请先打开游戏")
                    self._log_line("唤出窗口：一个都没检测到（游戏没开？）", "warn")
                    return
                lvl = "info" if ok else "warn"
                self._log_line(f"唤出游戏窗口：检测到 {total} 个，{ok} 个成功切到前台。", lvl)
                self.app.toast(f"已唤出 {total} 个游戏窗口（{ok} 个切到前台）")
                self.app._game_connected = None   # 窗口状态变了，强制下一轮 tick 刷新药丸

            try:
                self.app.ui_post(apply)
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------------
    # 关闭所有游戏窗口：确认 → 先停全部自动任务 → 强杀全部游戏进程（最快的关法）
    #   ⚠ 必须确认（用户拍板）：按钮太顺手会被误点，而强杀会丢游戏内未保存的进度。
    # ------------------------------------------------------------------
    def _close_all_windows(self):
        """「关闭所有游戏窗口」入口：先弹确认框，确认后才真关。"""
        self._ask_close_windows(self._do_close_all_windows)

    def _ask_close_windows(self, on_ok):
        """确认弹窗：红色「确认关闭」在右，Esc / 取消都只是关掉弹窗、不动游戏。"""
        dlg = ctk.CTkToplevel(self.app)
        dlg.title("关闭所有游戏窗口")
        dlg.geometry("460x210")
        dlg.configure(fg_color=T.BG)
        dlg.transient(self.app)
        ctk.CTkLabel(dlg, text="关闭所有游戏窗口？", font=self.fonts["title"],
                     text_color=T.TEXT).pack(anchor="w", padx=20, pady=(18, 0))
        warn = ctk.CTkLabel(dlg, text="将强制结束全部游戏进程，游戏内未保存的进度会丢失；正在跑的任务会先停止。",
                            font=self.fonts["small"], text_color=T.WARN, justify="left")
        warn.pack(fill="x", padx=20, pady=(6, 0))
        bind_wraplength(warn, padding=40)

        bar = ctk.CTkFrame(dlg, fg_color="transparent")
        bar.pack(fill="x", padx=20, pady=(18, 16), side="bottom")

        def _ok():
            try:
                dlg.grab_release()
            except Exception:
                pass
            dlg.destroy()
            on_ok()

        ctk.CTkButton(bar, text="确认关闭", font=self.fonts["btn"], width=120, height=38,
                      corner_radius=T.RADIUS_SM, fg_color=T.DANGER, hover_color=T.DANGER_HOVER,
                      text_color=T.ON_ACCENT, command=_ok).pack(side="right")
        ctk.CTkButton(bar, text="取消", font=self.fonts["btn"], width=96, height=38,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER,
                      text_color=T.TEXT, border_width=1, border_color=T.BORDER,
                      command=dlg.destroy).pack(side="right", padx=(0, 8))
        dlg.bind("<Escape>", lambda _e: dlg.destroy())

        def _show():
            try:
                x = self.app.winfo_rootx() + max(0, (self.app.winfo_width() - 460) // 2)
                y = self.app.winfo_rooty() + max(0, (self.app.winfo_height() - 210) // 3)
                dlg.geometry(f"460x210+{x}+{y}")
                dlg.lift()
                dlg.grab_set()
            except Exception:
                pass

        dlg.after(80, _show)

    def _do_close_all_windows(self):
        """停掉所有自动任务后强杀全部游戏进程（放后台线程，避免卡界面）。"""
        cfg = self.cfg = cfg_mod.load_config()
        self.app.cfg = cfg
        title = cfg.get("window_title", "梦幻西游")
        offset = cfg.get("window_offset", [0, 0])
        btn = getattr(self, "btn_close", None)
        stopped = self.app.stop_all_tasks()
        if btn is not None:
            try:
                btn.configure(state="disabled", text="⏳  正在关闭…")
            except Exception:
                pass

        def work():
            try:
                total, killed = win_mod.close_all(title, offset)
            except Exception:
                total, killed = 0, 0

            def apply():
                if btn is not None:
                    try:
                        btn.configure(state="normal", text="✕  关闭所有游戏窗口")
                    except Exception:
                        pass
                self.app._game_connected = None   # 窗口没了，强制下一轮 tick 刷新药丸
                if not total:
                    self.app.toast(f"没检测到游戏窗口（标题含「{title}」），游戏没开")
                    self._log_line("关闭窗口：没检测到游戏进程（游戏没开？）", "warn")
                    return
                if killed >= total:
                    self._log_line(f"关闭窗口：已停止任务 {stopped} 个，{killed} 个游戏进程全部关闭。", "info")
                    self.app.toast(f"已关闭 {killed} 个游戏窗口（停止任务 {stopped} 个）")
                else:
                    hint = "" if win_mod.is_admin() else "请用管理员身份运行脚本"
                    if not hint:
                        hint = "可能有进程未响应，可再点一次"
                    self._log_line(f"关闭窗口：已停止任务 {stopped} 个，只关掉 {killed}/{total} 个游戏进程"
                                   + f"（{hint}）", "warn")
                    self.app.toast(f"只关掉 {killed}/{total} 个游戏窗口：{hint}")

            try:
                self.app.ui_post(apply)
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

    def _collapse_to_float(self):
        """把主界面收成细长悬浮日志窗（幂等：已经收起就把悬浮窗抬到最前）。"""
        self.app.collapse_to_float()

    # 切到本页或操作后都会调
    def refresh(self):
        self.cfg = cfg_mod.load_config()
        self.app.cfg = self.cfg
        self._refresh_body()

    def _normalize_now(self):
        """点「还原尺寸」时触发：把所有尺寸≠激活尺寸组的窗口拉回该组尺寸。
        没有尺寸组时提示（标定一次即自动建组）；没有窗口时提示。只对尺寸不符的窗口动手。"""
        cfg = cfg_mod.load_config()
        self.cfg = cfg
        self.app.cfg = cfg
        size = calib.active_size(cfg)
        if not size:
            self.app.toast("还没有尺寸组：先标定任意一项")
            return
        self._restore_size(size)

    def _restore_size(self, size):
        """把所有尺寸≠size 的游戏窗口拉回 size（[w,h]）。
        不动游戏画面里的任何东西，只改窗口大小；已是目标尺寸的窗口不碰。"""
        cfg = self.cfg = cfg_mod.load_config()
        self.app.cfg = cfg
        try:
            bw, bh = int(size[0]), int(size[1])
        except (TypeError, ValueError, IndexError):
            self.app.toast("该尺寸组的分辨率无效")
            return
        title = cfg.get("window_title", "梦幻西游")
        offset = cfg.get("window_offset", [0, 0])
        try:
            wins = win_mod.locate_all(title, offset)
        except Exception:
            wins = []
        if not wins:
            self.app.toast(f"没检测到游戏窗口（标题含「{title}」），请先打开游戏")
            return
        todo = [w for w in wins
                if (w.rect() and (abs(w.rect()[2] - bw) > 4 or abs(w.rect()[3] - bh) > 4))]
        if not todo:
            self.app.toast(f"所有窗口已是 {bw}×{bh}，无需还原")
            self.refresh()
            return
        ok = 0
        got = []
        for w in todo:
            w.activate()
            if w.resize_to(bw, bh):
                ok += 1
            r = w.rect()
            if r:
                got.append((r[2], r[3]))
        self.app._game_connected = None    # 尺寸变了，强制下次 tick 刷新药丸
        if ok == len(todo):
            self.app.toast(f"已把 {ok} 个号还原到 {bw}×{bh}")
        elif got and max(s[0] for s in got) - min(s[0] for s in got) <= 4 \
                and max(s[1] for s in got) - min(s[1] for s in got) <= 4:
            # 新外壳锁定纵横比：各号已被统一到同一可达尺寸（多开同尺寸的真正目标已达成，
            # 允许 ±4px 吸附误差），但与目标尺寸不符——该尺寸落在比例线外，提示重新标定。
            self.app.toast(f"已把 {len(todo)} 个号统一到 {got[0][0]}×{got[0][1]}；"
                           f"新版客户端锁定窗口比例，{bw}×{bh} 不可达，建议重新标定一次")
        else:
            hint = "" if win_mod.is_admin() else "（请用管理员身份运行，启动时 UAC 点「是」）"
            self.app.toast(f"已还原 {ok}/{len(todo)} 个号到 {bw}×{bh}，部分窗口调整失败{hint}")
        self.refresh()

    def _refresh_body(self):
        for w in self.body.winfo_children():
            w.destroy()
        cfg = self.cfg

        # ── 窗口 / 界面工具（与具体任务无关：唤出多开窗口、把界面收成悬浮日志窗）──
        self._build_window_tools_card()

        # ── 活动页导航（四个活动任务共用一份按钮模板）──
        from ..core import vision
        c_activity = self._card()
        head_a = ctk.CTkFrame(c_activity, fg_color="transparent")
        head_a.pack(fill="x", padx=16, pady=14)
        head_a.grid_columnconfigure(0, weight=1)
        txt_a = ctk.CTkFrame(head_a, fg_color="transparent")
        txt_a.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(txt_a, text="活动页（共享）", font=self.fonts["h2"],
                     text_color=T.TEXT).pack(anchor="w")
        daily_path = cfg_mod.task_config(cfg, "activity").get("templates", {}).get("daily_activity")
        daily_ready = bool(daily_path and vision.load_template(daily_path) is not None)
        daily_status = ctk.CTkLabel(
            txt_a, text="日常活动：已标定" if daily_ready else "日常活动：未标定（可选）",
            font=self.fonts["body"], text_color=T.SUCCESS if daily_ready else T.TEXT_DIM)
        daily_status.pack(fill="x", pady=(4, 0))
        bind_wraplength(daily_status)
        ctk.CTkButton(head_a, text="标定（日常活动）", font=self.fonts["body"], height=36, width=140,
                      corner_radius=T.RADIUS_SM, fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER,
                      text_color=T.ON_ACCENT, command=self._open_activity_calibrate).grid(
                          row=0, column=1, padx=(12, 0))

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
        if self.runner and get_runtime().is_running(self.runner):
            self.btn_team.configure(text="■  停止组队", fg_color=T.DANGER, hover_color=T.DANGER_HOVER)
        if self.runner_db and get_runtime().is_running(self.runner_db):
            self.btn_disband.configure(text="■  停止解散", fg_color=T.DANGER, hover_color=T.DANGER_HOVER)
        self._kick_count_windows()

        # ── 整理背包（跨任务共享：任何任务流程都可穿插调用，这里可单独一键运行）──
        self._build_organize_card()

        # ── 窗口尺寸归一化（胶囊组：每个尺寸组 = 一套标定图是在哪个窗口尺寸下标定的）──
        c2 = self._card()
        head2 = ctk.CTkFrame(c2, fg_color="transparent")
        head2.pack(fill="x", padx=16, pady=(14, 4))
        head2.grid_columnconfigure(0, weight=1)
        txt2 = ctk.CTkFrame(head2, fg_color="transparent")
        txt2.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(txt2, text="窗口尺寸归一化（标定尺寸组）", font=self.fonts["h2"],
                     text_color=T.TEXT).pack(anchor="w")
        aid = calib.active_id(cfg)
        prof = calib.get(cfg, aid)
        cur_txt = (f"当前尺寸组：{calib.label_for(aid)} {calib.size_text(prof)}"
                   if prof else "尚未标定任何尺寸组")
        ctk.CTkLabel(txt2, text=cur_txt, font=self.fonts["body"],
                     text_color=T.TEXT if prof else T.WARN).pack(anchor="w", pady=(4, 0))
        btns2 = ctk.CTkFrame(head2, fg_color="transparent")
        btns2.grid(row=0, column=1, padx=(12, 0))
        ctk.CTkButton(btns2, text="还原尺寸", font=self.fonts["body"], height=36, width=100,
                      corner_radius=T.RADIUS_SM, fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER, text_color=T.ON_ACCENT,
                      command=self._normalize_now).pack(pady=(0, 6))
        ctk.CTkButton(btns2, text="刷新", font=self.fonts["body"], height=30, width=100,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self.refresh).pack()

        # 尺寸组胶囊：编号 + 分辨率（+ ✓ 表示当前组）。点整体＝切到该组；
        # 胶囊旁不再放「还原」——选中哪一组，点上面的「还原尺寸」即可把所有窗口拉回该组尺寸。
        pills = ctk.CTkFrame(c2, fg_color="transparent")
        pills.pack(fill="x", padx=16, pady=(10, 2))
        for it in calib.items(cfg):
            self._profile_pill(pills, it, active=(it["id"] == aid))
        if calib.items(cfg):
            ctk.CTkButton(pills, text="管理/删除", font=self.fonts["small"], height=30, width=84,
                          corner_radius=T.RADIUS_PILL, fg_color="transparent", hover_color=T.BORDER,
                          text_color=T.TEXT_DIM, border_width=1, border_color=T.BORDER,
                          command=self._open_profile_admin).pack(side="left", padx=(6, 0))
        if not prof:
            hint0 = ctk.CTkLabel(c2, text="还没有尺寸组：标定任意一项会自动新建一组。",
                                 font=self.fonts["small"], text_color=T.WARN, justify="left")
            hint0.pack(fill="x", padx=16, pady=(2, 0))
            bind_wraplength(hint0)

        # 窗口列表（信息 + 显示各号当前对应哪个尺寸组）
        ctk.CTkLabel(c2, text="检测到的窗口：",
                     font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left").pack(
                         anchor="w", padx=16, pady=(8, 2))
        # 窗口枚举（getAllWindows）很慢、绝不能卡主线程：先放占位、后台线程枚举完再回主线程填。
        rows_holder = ctk.CTkFrame(c2, fg_color="transparent")
        rows_holder.pack(fill="x")
        ctk.CTkLabel(rows_holder, text="正在检测窗口…", font=self.fonts["body"],
                     text_color=T.TEXT_DIM).pack(anchor="w", padx=16, pady=(2, 14))
        self._kick_enum_windows(rows_holder, list(prof["size"]) if prof else None)

    def _profile_pill(self, parent, it, active):
        """一个尺寸组胶囊：`① 1521×1198 ✓`。整体可点＝切到该组（要拉回尺寸就点上面的「还原尺寸」）。
        只有两种配色：当前组=绿（PROFILE_ACTIVE）、非当前组=灰（PROFILE_NEUTRAL）。"""
        fg, txt = T.PROFILE_ACTIVE if active else T.PROFILE_NEUTRAL
        box = ctk.CTkFrame(parent, fg_color="transparent")
        box.pack(side="left", padx=(0, 8), pady=2)
        label = f"{it['label']} {it['size'][0]}×{it['size'][1]}" + ("  ✓" if active else "")
        ctk.CTkButton(box, text=label, font=self.fonts["small"], height=30,
                      corner_radius=T.RADIUS_PILL, fg_color=fg, hover_color=fg, text_color=txt,
                      border_width=(2 if active else 1), border_color=txt,
                      command=lambda pid=it["id"]: self._activate_profile(pid)).pack(side="left")

    def _activate_profile(self, pid):
        cfg = cfg_mod.load_config()
        if not calib.set_active(cfg, pid):
            self.app.toast("该尺寸组不存在，请点「刷新」")
            return
        cfg_mod.save_config(cfg)
        self.app.cfg = cfg
        self.app._game_connected = None
        prof = calib.get(cfg, pid)
        self.app.toast(f"已切到尺寸组 {calib.label_for(pid)}（{calib.size_text(prof)}）")
        self.refresh()

    # ---- 尺寸组的删除/替换（胶囊满 3 组后要腾位子时的唯一入口）----
    def _delete_profile(self, pid):
        cfg = cfg_mod.load_config()
        prof = calib.get(cfg, pid)
        if prof is None:
            self.app.toast("该尺寸组已不存在，请点「刷新」")
            return
        left = calib.delete(cfg, pid)
        cfg_mod.save_config(cfg)
        self.app.cfg = cfg
        self.app._game_connected = None
        tip = (f"已删除尺寸组 {calib.label_for(pid)}（{calib.size_text(prof)}）"
               f"；模板图一个都没删，只是归属改回「未记录」，可随时重标。")
        if left is not None:
            tip += f" 当前组改为 {calib.label_for(left)}。"
        self.app.toast(tip)
        self.refresh()

    def _open_profile_admin(self):
        """「管理/删除」：列出所有尺寸组，逐个可删（胶囊满 3 组时必须先删一个才能标新尺寸）。
        删组只删记录、绝不删模板图，故做成无二次确认的轻操作。"""
        cfg = self.cfg = cfg_mod.load_config()
        its = calib.items(cfg)
        if not its:
            self.app.toast("还没有尺寸组可管理")
            return
        aid = calib.active_id(cfg)
        dlg = ctk.CTkToplevel(self.app)
        dlg.title("尺寸组管理")
        dlg.geometry("520x400")
        dlg.configure(fg_color=T.BG)
        dlg.transient(self.app)
        ctk.CTkLabel(dlg, text="标定尺寸组", font=self.fonts["title"], text_color=T.TEXT).pack(
            anchor="w", padx=20, pady=(18, 0))
        hint = ctk.CTkLabel(dlg, text="最多 3 组。组满时先删一个旧组（只删记录，模板图不删）。",
                            font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left")
        hint.pack(fill="x", padx=20, pady=(4, 10))
        bind_wraplength(hint, padding=40)

        body = ctk.CTkScrollableFrame(dlg, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        body.grid_columnconfigure(0, weight=1)
        T.tune_scroll_speed(body)

        def _row(i, it, is_act):
            fg, txt = (T.PROFILE_ACTIVE if is_act else T.PROFILE_NEUTRAL)
            row = ctk.CTkFrame(body, fg_color=T.SURFACE, corner_radius=T.RADIUS,
                               border_width=1, border_color=T.BORDER)
            row.grid(row=i, column=0, sticky="ew", padx=4, pady=6)
            row.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(row, text=f"{it['label']}  {it['size'][0]}×{it['size'][1]}"
                                   + ("   ✓ 当前组" if is_act else ""),
                         font=self.fonts["body_b"], fg_color=fg, text_color=txt,
                         corner_radius=T.RADIUS_PILL, padx=10, height=26).grid(
                             row=0, column=0, sticky="w", padx=14, pady=(10, 0))
            ctk.CTkLabel(row, text=f"标定于 {it.get('at') or '（未知时间）'}", font=self.fonts["small"],
                         text_color=T.TEXT_DIM).grid(row=1, column=0, sticky="w", padx=14, pady=(2, 10))

            def _del(pid=it["id"], d=dlg):
                self._delete_profile(pid)
                try:
                    d.destroy()
                except Exception:
                    pass
                self._open_profile_admin()

            ctk.CTkButton(row, text="删除", font=self.fonts["body"], height=32, width=80,
                          corner_radius=T.RADIUS_SM, fg_color="transparent", hover_color=T.DANGER,
                          text_color=T.DANGER, border_width=1, border_color=T.BORDER,
                          command=_del).grid(row=0, column=1, rowspan=2, padx=14)

        for i, it in enumerate(its):
            _row(i, it, it["id"] == aid)

    def _ask_replace_profile(self, size):
        """标定遇到「组已满」时的引导：让用户挑一个旧组替换掉，随后由调用方重试同一项标定。
        返回被替换掉的组号（用户取消返回 None）。这样用户不会卡在「必须先自己找地方删组」上。"""
        cfg = cfg_mod.load_config()
        its = calib.items(cfg)
        if not its:
            return None
        picked = {"pid": None}
        dlg = ctk.CTkToplevel(self.app)
        dlg.title("尺寸组已满")
        dlg.geometry("560x360")
        dlg.configure(fg_color=T.BG)
        dlg.transient(self.app)
        ctk.CTkLabel(dlg, text=f"已有 3 个尺寸组，放不下 {int(size[0])}×{int(size[1])}",
                     font=self.fonts["title"], text_color=T.TEXT).pack(anchor="w", padx=20, pady=(18, 0))
        hint = ctk.CTkLabel(dlg, text="选一个旧组替换（只删记录，模板图不删）。",
                            font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left")
        hint.pack(fill="x", padx=20, pady=(4, 10))
        bind_wraplength(hint, padding=40)

        body = ctk.CTkScrollableFrame(dlg, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        body.grid_columnconfigure(0, weight=1)
        T.tune_scroll_speed(body)
        for i, it in enumerate(its):
            row = ctk.CTkFrame(body, fg_color=T.SURFACE, corner_radius=T.RADIUS,
                               border_width=1, border_color=T.BORDER)
            row.grid(row=i, column=0, sticky="ew", padx=4, pady=6)
            row.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(row, text=f"{it['label']}  {it['size'][0]}×{it['size'][1]}", font=self.fonts["body_b"],
                         text_color=T.TEXT).grid(row=0, column=0, sticky="w", padx=14, pady=(10, 0))
            ctk.CTkLabel(row, text=f"标定于 {it.get('at') or '（未知时间）'}", font=self.fonts["small"],
                         text_color=T.TEXT_DIM).grid(row=1, column=0, sticky="w", padx=14, pady=(0, 10))

            def _pick(pid=it["id"]):
                picked["pid"] = pid
                dlg.destroy()

            ctk.CTkButton(row, text="替换掉它", font=self.fonts["body"], height=32, width=100,
                          corner_radius=T.RADIUS_SM, fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER,
                          text_color=T.ON_ACCENT, command=_pick).grid(row=0, column=1, rowspan=2, padx=14)
        try:
            self.app.wait_window(dlg)
        except Exception:
            pass
        return picked["pid"]

    def _kick_enum_windows(self, holder, active_size):
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
                labels = accounts.cached_labels_for([w for w, _r in data])
            except Exception:
                labels = [accounts.fallback_label(i) for i in range(len(data))]
            try:
                self.app.ui_post(lambda: self._fill_win_rows(holder, data, labels, active_size, token))
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

    def _fill_win_rows(self, holder, data, labels, active_size, token):
        """在主线程把枚举结果渲染进 holder。过期结果/控件已销毁则丢弃。
        每行显示该号的角色名 + 当前尺寸，并标注它「属于哪个尺寸组」（与激活组尺寸吻合 = ±4px）。"""
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
            lbl = labels[i] if i < len(labels) else accounts.fallback_label(i)
            meta = f"{lbl}    {r[2]}×{r[3]}    @({r[0]},{r[1]})" if r else f"{lbl}    （窗口已失效）"
            is_act = bool(active_size and r
                          and abs(int(active_size[0]) - r[2]) <= 4
                          and abs(int(active_size[1]) - r[3]) <= 4)
            tag = ""
            if r:
                pid = calib.find_by_size(self.cfg, [r[2], r[3]])
                if pid is not None:
                    tag = f"   {calib.label_for(pid)} 尺寸组" + ("（当前）" if is_act else "")
                else:
                    tag = "   未标定尺寸"
            ctk.CTkLabel(row, text=meta + tag, font=self.fonts["body"],
                         text_color=T.SUCCESS if is_act else T.TEXT).grid(
                             row=0, column=0, sticky="w", padx=12, pady=8)
        ctk.CTkFrame(holder, fg_color="transparent", height=6).pack()

    def _calib_singleton(self, attr, only, fail_msg, exclude=None, task_name="teaming"):
        """打开共享标定窗并按 attr 去重，避免多个弹窗覆盖同一份配置。"""
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
            setattr(self, attr, CalibrateDialog(self.app, task_name=task_name,
                                                only=only, exclude=exclude, on_done=_after))
        except Exception as e:
            setattr(self, attr, None)
            self.app.toast(f"{fail_msg}：{e}")

    def _open_activity_calibrate(self):
        self._calib_singleton("_activity_cal_dialog", None, "打开活动页标定失败", task_name="activity")

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
        """据当前 self._win_count/_win_labels + teaming.captain_index 渲染队长状态行（纯本地数据，秒回）。
        队长已挪进「选择窗口/队长」里选（带缩略图），这里只读出来显示。"""
        if self.lbl_team_status is None:
            return
        n = self._win_count
        labels = self._win_labels
        team_tc = cfg_mod.task_config(self.app.cfg, "teaming")
        cap = team_tc.get("captain_index", 0)
        if not (0 <= cap < n):
            cap = 0
        multi = self.app.cfg.get("targets", {}).get("multi", False)
        if n >= 2:
            tip = f"已选 {n} 个号，队长={labels[cap] if cap < len(labels) else accounts.fallback_label(cap)}"
        elif not multi:
            tip = "组队需勾 2~5 个号并指定队长。"
        else:
            tip = f"已选 {n} 个号，组队至少 2 个号（队长+≥1 队员）。"
        self.lbl_team_status.configure(text=tip)

    def _kick_count_windows(self):
        """后台枚举已选窗口数 + 角色名，回主线程重渲染队长状态与「将整理：…」。token 丢弃过期结果。"""
        title = self.app.cfg.get("window_title", "梦幻西游")
        offset = self.app.cfg.get("window_offset", [0, 0])
        targets = self.app.cfg.get("targets", {})
        token = object()
        self._count_token = token

        def work():
            labels = resolve_window_labels(title, offset, targets)

            def apply():
                if token is not getattr(self, "_count_token", None):
                    return
                self._win_count = len(labels)
                self._win_labels = labels
                self._render_team_action()
                self._refresh_targets_labels()

            # ⚠ 必须走 ui_post（队列）。后台线程直接调 Tk 的 after(0, …) 在启动期会抛
            # RuntimeError 并被 except 吞掉 → 回调永久丢失，现象就是「将整理：」这类文案
            # 一直停在构建那一刻的「号1」。见 App.ui_post docstring，别再改回去。
            self.app.ui_post(apply)

        threading.Thread(target=work, daemon=True).start()

    def _start_teaming(self):
        if self.runner and get_runtime().is_running(self.runner):
            get_runtime().stop(self.runner)
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
        self.runner = get_runtime().create_runner(task_cls(), self.app.cfg)
        ok, problems = get_runtime().start(self.runner)
        if not ok:
            for p in problems:
                self._log_line("无法开始组队：" + p, "error", "组队")
            self.runner = None
            return
        self._log_line("开始一键组队…", "hit", "组队")
        self.btn_team.configure(text="■  停止组队", fg_color=T.DANGER, hover_color=T.DANGER_HOVER, state="normal")
        self.app.on_task_started("组队", self._start_teaming, self.runner)

    def _on_team_finished(self):
        if self.btn_team is not None:
            self.btn_team.configure(text="▶  一键组队", fg_color=T.ACCENT,
                                    hover_color=T.ACCENT_HOVER, state="normal")

    def _start_disband(self):
        if self.runner_db and get_runtime().is_running(self.runner_db):
            get_runtime().stop(self.runner_db)
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
        self.runner_db = get_runtime().create_runner(task_cls(), self.app.cfg)
        ok, problems = get_runtime().start(self.runner_db)
        if not ok:
            for p in problems:
                self._log_line("无法开始解散：" + p, "error", "解散")
            self.runner_db = None
            return
        self._log_line("开始一键解散…", "hit", "解散")
        self.btn_disband.configure(text="■  停止解散", fg_color=T.DANGER, hover_color=T.DANGER_HOVER,
                                   state="normal")
        self.app.on_task_started("解散", self._start_disband, self.runner_db)

    def _on_disband_finished(self):
        if self.btn_disband is not None:
            self.btn_disband.configure(text="⏏  一键解散", fg_color=T.BTN, hover_color=T.BTN_HOVER,
                                       text_color=T.TEXT, state="normal")

    # ---- 由 App._tick 驱动 ----
    def pump(self):
        if self.runner:
            if not get_runtime().is_running(self.runner) and self.btn_team is not None \
                    and self.btn_team.cget("text") != "▶  一键组队":
                self._on_team_finished()
        if self.runner_db:
            if not get_runtime().is_running(self.runner_db) and self.btn_disband is not None \
                    and self.btn_disband.cget("text") != "⏏  一键解散":
                self._on_disband_finished()
        if self.runner_ob:
            if not get_runtime().is_running(self.runner_ob) and self.btn_ob is not None \
                    and self.btn_ob.cget("text") != "▶  一键整理":
                self._on_ob_finished()

    @staticmethod
    def _targets_summary(cfg):
        """只读 cfg.targets 拼一句「将操作哪些号」的说明，不去枚举/定位窗口（够快、给卡片当提示用）。
        号名取 core/accounts 的缓存（纯读内存，不枚举进程）；缓存没热起来才退回「号N」。

        ⚠ 正因为是静态渲染，调用方在后台枚举出号名之后**必须**重新渲染这句
        （见 _refresh_targets_labels），否则卡片会一直挂着构建那一刻的「号1」。"""
        targets = (cfg or {}).get("targets", {}) or {}
        cached = accounts.cached_labels()
        if targets.get("multi"):
            idxs = targets.get("multi_indices") or list(range(len(cached)))
            names = [cached[i] for i in idxs if 0 <= i < len(cached)]
            if names:
                return "多开 · " + "、".join(names)
            return f"多开 · 已选 {len(idxs)} 个号" if idxs else "多开 · 全体号"
        i = targets.get("single_index", 0)
        i = i if isinstance(i, int) and i >= 0 else 0
        return f"单开 · {accounts.cached_label(i)}"

    def _refresh_targets_labels(self):
        """窗口枚举出角色名之后，重渲染「将整理：…」这句。

        它是 _build_organize_card 里静态渲染的，构建那一刻 accounts 缓存往往还是空的（会显示「号1」），
        所以每次后台枚举回来都要刷一次——否则名字永远停在「号1」。"""
        if self.lbl_ob_targets is None:
            return
        try:
            self.lbl_ob_targets.configure(text="将整理：" + self._targets_summary(self.app.cfg))
        except Exception:
            pass

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
        self.lbl_ob_targets = ctk.CTkLabel(txt, text="将整理：" + self._targets_summary(cfg),
                                           font=self.fonts["small"], text_color=T.TEXT_DIM)
        self.lbl_ob_targets.pack(anchor="w", pady=(4, 0))
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
        self.switch_ob = ctk.CTkSwitch(act, text="实战模式", font=self.fonts["body"],
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
        self.switch_auto_ob = ctk.CTkSwitch(auto_row, text="自动整理背包", font=self.fonts["body"],
                                            command=self._toggle_auto_organize)
        self.switch_auto_ob.pack(anchor="w")
        if ob_tc.get("auto_organize"):
            self.switch_auto_ob.select()
        else:
            self.switch_auto_ob.deselect()

        # 重建后：若整理在跑，恢复「停止整理」文案/颜色（照 btn_team 的恢复写法）
        if self.runner_ob and get_runtime().is_running(self.runner_ob):
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
            self._log_line("⚠ 实战模式：会真使用/丢弃/出售物品", "warn", "整理背包")
        else:
            self._log_line("演练模式（只识别不操作）", "info", "整理背包")

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
            self._log_line("自动整理背包：开"
                           + ("" if tpl_ok else "　⚠ 还没标定『背包满图标』，不会触发"),
                           "warn" if not tpl_ok else "info", "整理背包")
        else:
            self._log_line("自动整理背包：关", "info", "整理背包")

    def _start_organize(self):
        if self.runner_ob and get_runtime().is_running(self.runner_ob):
            get_runtime().stop(self.runner_ob)
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
        self.runner_ob = get_runtime().create_runner(task_cls(), self.app.cfg)
        ok, problems = get_runtime().start(self.runner_ob)
        if not ok:
            for p in problems:
                self._log_line("无法开始整理：" + p, "error", "整理背包")
            self.runner_ob = None
            return
        self._log_line("开始一键整理背包…", "hit", "整理背包")
        self.btn_ob.configure(text="■  停止整理", fg_color=T.DANGER, hover_color=T.DANGER_HOVER, state="normal")
        self.app.on_task_started("整理背包", self._start_organize, self.runner_ob)

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
