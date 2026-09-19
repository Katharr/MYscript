# -*- coding: utf-8 -*-
"""
悬浮运行日志窗（通用页「收起为悬浮日志窗」用）。

把主界面收起来（withdraw），桌面上只留一条细长的悬浮窗显示运行日志，方便摆在游戏窗口的
边边角角，随时瞄一眼脚本在干什么。要点：

  · 日志与主界面右侧的全局面板是【同一份】：App.log_line 同时喂两边，创建时把已有历史整段搬过来，
    故「收起」不会丢上下文；收起期间产生的日志也会照常漏进来。
  · 可自由拖动/缩放（原生标题栏），窗口位置+尺寸记进 config 的 float_log.geometry，下次收起按原样
    摆回；没有记录时默认落在屏幕右上角（贴合「摆在游戏窗口边上」的习惯用法）。
  · 「固定在前台」开关 = -topmost（默认开）：游戏窗口切到前台时它也不会被盖住，才能一直看到日志。
  · 关掉本窗（标题栏 X / 「展开主界面」）＝回到主界面，绝不等于退出程序。
"""

import re

import customtkinter as ctk

from . import theme as T
from ..core import config as cfg_mod

# 默认尺寸：细长条（窄而不高），不挡住游戏画面
DEF_W, DEF_H = 320, 200
DEF_MARGIN = 24
_GEOM_RE = re.compile(r"^(\d+)x(\d+)([+-]\d+)([+-]\d+)$")


class FloatLogWindow(ctk.CTkToplevel):
    """细长的悬浮日志窗。生命周期由 App.float_log 持有，App.close_float_log() 负责收尾。"""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.fonts = app.fonts
        self.cfg = cfg_mod.load_config()
        fl = self.cfg.get("float_log") or {}

        self.title("运行日志 · 悬浮")
        self.configure(fg_color=T.BG)
        self.minsize(190, 96)
        self._restore_geometry(fl.get("geometry"))

        # 固定前台的开关变量要先建（_build 里要绑它）
        self.var_top = ctk.BooleanVar(value=bool(fl.get("topmost", True)))
        self._build()
        # 建完立刻把主界面日志框里的历史整段搬过来：这样「收起」前后内容连续；之后再产生的日志由
        # App.log_line 实时喂进来（见 append），不会重复——若等窗口显示后再搬，收起那一刻写的那行
        # 会既被实时喂一次、又被搬进来一次。
        self._seed_from_app()
        self._set_topmost(bool(fl.get("topmost", True)))

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        # 首次显示后再滚到底/置前：窗口还没映射时 see("end") 定位不准。
        self.after(80, self._on_shown)

    # ------------------------------------------------------------------
    def _px(self, logical):
        """逻辑单位 -> 实际像素。CTk 的 geometry("WxH") 会把宽高按窗口缩放比放大（见 ctk_toplevel
        ._apply_geometry_scaling），而位置 (+x+y) 不缩放——故算摆位/夹取屏幕时必须自己换算，
        否则 150% 缩放下「贴右上角」会有一截跑到屏幕外。"""
        try:
            return self._apply_window_scaling(logical)
        except Exception:
            return logical

    def _restore_geometry(self, geom):
        """按上次记录摆回原位；没有记录/记录非法/已跑出屏幕外都退回「屏幕右上角」。
        （记录可能来自已拔掉的副屏，故一律做一次夹取，别让窗口出现在看不见的地方。）"""
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        m = _GEOM_RE.match(str(geom or ""))
        if m:
            w, h, x, y = (int(g) for g in m.groups())
            w, h = max(190, w), max(96, h)
            x = min(max(x, 0), max(0, sw - self._px(w)))
            y = min(max(y, 0), max(0, sh - self._px(h)))
        else:
            w, h = DEF_W, DEF_H
            x, y = max(0, sw - self._px(w) - DEF_MARGIN), DEF_MARGIN
        try:
            self.geometry(f"{w}x{h}+{x}+{y}")
        except Exception:
            pass

    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)      # 日志框占满剩余高度（它在第 2 行）

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 4))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text="运行日志", font=self.fonts["body_b"], text_color=T.TEXT).grid(
            row=0, column=0, sticky="w")
        ctk.CTkButton(head, text="展开主界面", font=self.fonts["small"], height=26, width=88,
                      corner_radius=T.RADIUS_SM, fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER,
                      text_color=T.ON_ACCENT,
                      command=self.app.close_float_log).grid(row=0, column=1, sticky="e")

        row2 = ctk.CTkFrame(self, fg_color="transparent")
        row2.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 6))
        row2.grid_columnconfigure(0, weight=1)
        ctk.CTkSwitch(row2, text="固定在前台", font=self.fonts["small"], variable=self.var_top,
                      command=self._on_top_toggle, switch_width=40, switch_height=18,
                      progress_color=T.ACCENT, text_color=T.TEXT_DIM,
                      button_color=T.TEXT_DIM, button_hover_color=T.TEXT).grid(
                          row=0, column=0, sticky="w")
        ctk.CTkButton(row2, text="清空", font=self.fonts["small"], height=26, width=56,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER,
                      text_color=T.TEXT, border_width=1, border_color=T.BORDER,
                      command=self.clear).grid(row=0, column=1, sticky="e")

        self.text = ctk.CTkTextbox(self, font=self.fonts["mono"], fg_color=T.SURFACE_2,
                                   text_color=T.TEXT, corner_radius=T.RADIUS_SM, wrap="word")
        self.text.grid(row=2, column=0, sticky="nsew", padx=10, pady=(0, 10))
        T.apply_log_tags(self.text._textbox)
        self.text.configure(state="disabled")

    # ------------------------------------------------------------------
    def _on_shown(self):
        """首次显示：滚到最新一行并置前（内容早在 __init__ 里就搬好了，见 _seed_from_app）。"""
        self._scroll_end()
        try:
            self.lift()
            self.focus_force()
        except Exception:
            pass

    def _scroll_end(self):
        try:
            self.text.see("end")
        except Exception:
            pass

    def _seed_from_app(self):
        """把主界面右侧日志框里的现有内容整段搬进悬浮窗（保持「收起」前后连续，不丢上下文）。"""
        src = getattr(self.app, "log", None)
        if src is None:
            return
        try:
            content = src.get("1.0", "end-1c")
        except Exception:
            return
        if not content:
            return
        try:
            self.text.configure(state="normal")
            tb = self.text._textbox
            tb.insert("1.0", content + "\n")
            # 与主面板同样的行数封顶：搬过来的是旧日志，顶掉也不可惜
            nlines = int(tb.index("end-1c").split(".")[0])
            if nlines > 2000:
                tb.delete("1.0", f"{nlines - 1800}.0")
            self._scroll_end()
        except Exception:
            pass
        finally:
            try:
                self.text.configure(state="disabled")
            except Exception:
                pass

    # ---- 对外：日志追加 / 清空 / 外观 ----
    def append(self, msg, level="info", source=None):
        """App.log_line 每产出一行就调它一次（与主面板逐字一致，见 theme.append_log）。"""
        try:
            T.append_log(self.text, msg, level, source)
        except Exception:
            pass

    def clear(self):
        try:
            self.text.configure(state="normal")
            self.text.delete("1.0", "end")
            self.text.configure(state="disabled")
        except Exception:
            pass

    def apply_theme(self):
        """明暗切换后重刷日志着色（tag_config 不吃 ctk 的二元组配色，必须手动重跑）。"""
        try:
            T.apply_log_tags(self.text._textbox)
        except Exception:
            pass

    def toast(self, msg):
        """浮层提示。收起状态下主界面是不可见的（App.toast 的浮层贴在它身上、会被一起藏掉），
        故这段时间的提示改由本窗口代显，否则急停/任务结束这类提示用户根本看不到。"""
        try:
            lbl = ctk.CTkLabel(self, text=msg, font=self.fonts["small"], fg_color=T.ACCENT,
                               text_color=T.ON_ACCENT, corner_radius=T.RADIUS_SM,
                               padx=10, pady=6, justify="left",
                               wraplength=max(140, int(self.winfo_width()) - 24))
            lbl.place(relx=0.5, rely=0.98, anchor="s")
            self.after(2400, lbl.destroy)
        except Exception:
            pass

    # ---- 固定在前台 ----
    def _on_top_toggle(self):
        val = bool(self.var_top.get())
        self._set_topmost(val)
        self._save_cfg(topmost=val)

    def _set_topmost(self, val):
        try:
            self.attributes("-topmost", bool(val))
        except Exception:
            pass

    # ---- 收尾 ----
    def _save_cfg(self, topmost=None, geometry=None):
        """把悬浮窗的位置/尺寸/置顶偏好写回 config（读盘再写，避免覆盖别处刚改的配置）。"""
        try:
            cfg = cfg_mod.load_config()
            fl = dict(cfg.get("float_log") or {})
            if geometry:
                fl["geometry"] = geometry
            if topmost is not None:
                fl["topmost"] = bool(topmost)
            cfg["float_log"] = fl
            cfg_mod.save_config(cfg)
            self.app.cfg = cfg
        except Exception:
            pass

    def _current_geometry(self):
        try:
            return self.geometry()          # 形如 "320x200+1200+24"
        except Exception:
            return None

    def _on_close(self):
        """标题栏 X：记下位置，然后回主界面（关悬浮窗 ≠ 关程序）。"""
        self._save_cfg(topmost=bool(self.var_top.get()), geometry=self._current_geometry())
        self.app.close_float_log(save=False)
