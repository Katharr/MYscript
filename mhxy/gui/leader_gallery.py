# -*- coding: utf-8 -*-
"""
队长ID 库弹窗：当前 + 最近 3 个历史队长ID 的画廊，可随时「设为当前」「删除」「重新标定追加」。

为什么是弹窗：行内入口只保留一个带缩略图的小按钮（点开本窗口），把全部复杂度收进这里，
主界面不拥挤。数据逻辑全在 core/leader_history.py（纯函数），本文件只是它的薄壳 + 画面。

同步：所有操作都改写同一份 app.cfg + 同一批物理文件（tm_leader_id.png + 4 个 slot），
并在变更后回调 on_done（= App.refresh_leader_thumb）广播刷新两处行内入口缩略图。
"""

import customtkinter as ctk

from . import theme as T
from ..core import config as cfg_mod
from ..core import leader_history as lh


class LeaderIdGallery(ctk.CTkToplevel):
    @classmethod
    def open(cls, app):
        """打开（去重：已开则前置，避免两个库窗同时写 config 打架）。on_done 固定广播刷新两页。"""
        existing = getattr(app, "_leader_gallery", None)
        if existing is not None:
            try:
                if existing.winfo_exists():
                    existing.lift()
                    existing.focus_force()
                    return existing
            except Exception:
                pass
        g = cls(app, on_done=getattr(app, "refresh_leader_thumb", None))
        app._leader_gallery = g
        return g

    def __init__(self, app, on_done=None):
        super().__init__(app)
        self.app = app
        self.fonts = app.fonts
        self.on_done = on_done
        self._thumbs = []                 # 防缩略图被 GC（每次 _render 先清空再重建）

        # 以磁盘为准 reload，确保迁移兜底看到真实状态、操作不被旧内存对象覆盖。
        self.app.cfg = cfg_mod.load_config()

        self.title("队长ID 库")
        self.geometry("480x460")
        self.minsize(440, 420)
        self.configure(fg_color=T.BG)
        self.transient(app)

        self._build()
        self._render()
        self.after(120, self._center)
        self.protocol("WM_DELETE_WINDOW", self._close)

    # ------------------------------------------------------------------
    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 4))
        top.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(top, text="队长ID 库", font=self.fonts["title"], text_color=T.TEXT).grid(
            row=0, column=0, sticky="w")
        sub = ctk.CTkLabel(top, text="当前队长ID + 最近 3 个历史，可随时设为当前，免去重复标定。"
                                     "标在最前面、带「● 当前」的就是组队时实际识别用的那张。",
                           font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left")
        sub.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        T.bind_wraplength(sub)

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=1, column=0, sticky="ew", padx=18, pady=(8, 4))
        ctk.CTkButton(bar, text="＋ 重新标定（追加一张并设为当前）", font=self.fonts["btn"], height=38,
                      corner_radius=T.RADIUS_SM, fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER,
                      text_color=T.ON_ACCENT, command=self._recalibrate).pack(fill="x")

        self.grid_frame = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.grid_frame.grid(row=2, column=0, sticky="nsew", padx=12, pady=(4, 4))
        self.grid_frame.grid_columnconfigure(0, weight=1, uniform="col")
        self.grid_frame.grid_columnconfigure(1, weight=1, uniform="col")
        T.tune_scroll_speed(self.grid_frame)

        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.grid(row=3, column=0, sticky="ew", padx=18, pady=(4, 14))
        bottom.grid_columnconfigure(0, weight=1)
        self.status_lbl = ctk.CTkLabel(bottom, text="", font=self.fonts["small"], text_color=T.TEXT_DIM)
        self.status_lbl.grid(row=0, column=0, sticky="w")
        ctk.CTkButton(bottom, text="完成", font=self.fonts["btn"], width=100, height=36,
                      corner_radius=T.RADIUS_SM, fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER,
                      text_color=T.ON_ACCENT, command=self._close).grid(row=0, column=1, sticky="e")

    # ------------------------------------------------------------------
    def _render(self):
        from .app import load_thumb     # 延迟导入避免与 app.py 循环依赖
        for w in self.grid_frame.winfo_children():
            w.destroy()
        self._thumbs.clear()

        history = lh.get_history(self.app.cfg)
        active = lh.get_active_index(self.app.cfg)

        if not history:
            empty = ctk.CTkLabel(self.grid_frame,
                                 text="还没有标定过队长ID。\n点上方「＋ 重新标定」框选队长名字即可。",
                                 font=self.fonts["body"], text_color=T.TEXT_DIM, justify="left")
            empty.grid(row=0, column=0, columnspan=2, sticky="ew", padx=14, pady=24)
            T.bind_wraplength(empty)
            return

        for i in range(lh.MAX_SLOTS):
            r, col = divmod(i, 2)
            if i < len(history):
                self._card(r, col, i, history[i], is_active=(i == active), load_thumb=load_thumb)
            else:
                self._empty_slot(r, col)

    def _card(self, r, col, idx, item, is_active, load_thumb):
        card = ctk.CTkFrame(self.grid_frame, fg_color=T.SURFACE_2, corner_radius=T.RADIUS_SM,
                            border_width=2, border_color=(T.SUCCESS if is_active else T.BORDER))
        card.grid(row=r, column=col, sticky="nsew", padx=6, pady=6)
        card.grid_columnconfigure(0, weight=1)

        head = ctk.CTkFrame(card, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 0))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text=item.get("label", "—"), font=self.fonts["small"],
                     text_color=T.TEXT_DIM).grid(row=0, column=0, sticky="w")
        if is_active:
            ctk.CTkLabel(head, text="● 当前", font=self.fonts["small"], text_color=T.SUCCESS).grid(
                row=0, column=1, sticky="e")

        thumb = load_thumb(item.get("slot"), self._thumbs, max_h=46)
        if thumb is not None:
            ctk.CTkLabel(card, text="", image=thumb).grid(row=1, column=0, padx=10, pady=8)
        else:
            ctk.CTkLabel(card, text="（图片丢失）", font=self.fonts["small"],
                         text_color=T.WARN).grid(row=1, column=0, padx=10, pady=14)

        btns = ctk.CTkFrame(card, fg_color="transparent")
        btns.grid(row=2, column=0, sticky="ew", padx=8, pady=(0, 8))
        btns.grid_columnconfigure(0, weight=1)
        btns.grid_columnconfigure(1, weight=1)
        if is_active:
            ctk.CTkButton(btns, text="✓ 当前", font=self.fonts["small"], height=28, state="disabled",
                          corner_radius=T.RADIUS_SM, fg_color="transparent", text_color=T.TEXT_DIM,
                          border_width=1, border_color=T.BORDER).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        else:
            ctk.CTkButton(btns, text="设为当前", font=self.fonts["small"], height=28,
                          corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                          border_width=1, border_color=T.BORDER,
                          command=lambda i=idx: self._activate(i)).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ctk.CTkButton(btns, text="删除", font=self.fonts["small"], height=28,
                      corner_radius=T.RADIUS_SM, fg_color="transparent", hover_color=T.DANGER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=lambda i=idx: self._delete(i)).grid(row=0, column=1, sticky="ew", padx=(4, 0))

    def _empty_slot(self, r, col):
        ph = ctk.CTkFrame(self.grid_frame, fg_color="transparent", corner_radius=T.RADIUS_SM,
                          border_width=1, border_color=T.BORDER)
        ph.grid(row=r, column=col, sticky="nsew", padx=6, pady=6)
        ctk.CTkLabel(ph, text="（空槽）", font=self.fonts["small"], text_color=T.TEXT_DIM).pack(
            expand=True, padx=10, pady=28)

    # ------------------------------------------------------------------
    def _recalibrate(self):
        from .calibrate_dialog import calibrate_template_direct
        self.withdraw()                   # 框选期间整窗隐身，确保截图里只有游戏画面
        ok = False
        try:
            ok = calibrate_template_direct(self.app, "teaming", "leader_id", "队长ID", toast=self._toast)
        finally:
            try:
                self.deiconify()
                self.lift()
                self.focus_force()
            except Exception:
                pass
        if ok:
            self.app.cfg = cfg_mod.load_config()      # calibrate 自己写过盘，必须 reload 再 push
            lh.push_after_calibrate(self.app.cfg)
            self._after_change("已标定新的队长ID并设为当前。", T.SUCCESS)

    def _activate(self, idx):
        lh.activate(self.app.cfg, idx)
        self._after_change("已设为当前队长ID。", T.SUCCESS)

    def _delete(self, idx):
        lh.delete(self.app.cfg, idx)
        self._after_change("已删除。", T.TEXT_DIM)

    def _after_change(self, msg, color):
        self._render()
        self._toast(msg, color)
        if callable(self.on_done):
            try:
                self.on_done()
            except Exception:
                pass

    # ------------------------------------------------------------------
    def _toast(self, msg, color=T.TEXT_DIM):
        try:
            self.status_lbl.configure(text=msg, text_color=color)
        except Exception:
            pass

    def _center(self):
        try:
            self.lift()
            self.focus_force()
        except Exception:
            pass

    def _close(self):
        try:
            if getattr(self.app, "_leader_gallery", None) is self:
                self.app._leader_gallery = None
        except Exception:
            pass
        self.destroy()
