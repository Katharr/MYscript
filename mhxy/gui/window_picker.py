# -*- coding: utf-8 -*-
"""
选择窗口对话框（基础特性）。

让用户从「桌面上检测到的同名游戏窗口」里选择脚本要操作的目标：
  · 单开：选 1 个号（单选）。
  · 多开：勾多个号（轮流操作）。

每个窗口渲成一张卡片，带【实时缩略图】帮你认出哪个号（号按屏幕位置左→右编号，
和 window.locate_all 的排序一致）。选择结果写入 cfg["targets"]：
  multi / single_index / multi_indices。

窗口身份用「位置序号」而非 HWND——三个号标题相同、HWND 重启会变，按摆放位置认号最稳。
"""

import time

import customtkinter as ctk

from . import theme as T
from ..core import config as cfg_mod
from ..core import window as win_mod

try:
    from PIL import Image
except Exception:  # PIL 缺失时降级为无缩略图
    Image = None


class WindowPickerDialog(ctk.CTkToplevel):
    THUMB_H = 84   # 缩略图高度（像素），宽按窗口宽高比缩放

    def __init__(self, app, on_done=None):
        super().__init__(app)
        self.app = app
        self.fonts = app.fonts
        self.on_done = on_done

        self.cfg = cfg_mod.load_config()
        self.targets = dict(self.cfg.get("targets", {}))
        self.title_substr = self.cfg.get("window_title", "梦幻西游")
        self.offset = self.cfg.get("window_offset", [0, 0])

        self._wins = []          # [(GameWindow, rect, ctk_thumb_or_None)]
        self._thumbs = []        # 防 GC
        self._multi = bool(self.targets.get("multi", False))
        self._single_var = ctk.IntVar(value=int(self.targets.get("single_index", 0) or 0))
        self._multi_vars = {}    # index -> BooleanVar

        self.title("选择窗口")
        self.geometry("560x600")
        self.minsize(480, 420)
        self.configure(fg_color=T.BG)
        self.transient(app)

        self._build()
        self._enumerate()
        self.after(120, self._center)

    # ------------------------------------------------------------------
    def _center(self):
        try:
            self.lift()
            self.focus_force()
        except Exception:
            pass

    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # 头部
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=20, pady=(16, 6))
        ctk.CTkLabel(top, text="选择窗口", font=self.fonts["title"], text_color=T.TEXT).pack(anchor="w")
        ctk.CTkLabel(top, text="单开选 1 个号，多开勾多个号轮流操作。号按屏幕从左到右编号。"
                              "检测区可在「标定」里留空＝整窗，无需框大区域。",
                     font=self.fonts["small"], text_color=T.TEXT_DIM,
                     justify="left", wraplength=500).pack(anchor="w", pady=(4, 0))

        # 模式切换
        modebar = ctk.CTkFrame(self, fg_color="transparent")
        modebar.grid(row=1, column=0, sticky="ew", padx=20, pady=(4, 6))
        ctk.CTkLabel(modebar, text="模式", font=self.fonts["body_b"], text_color=T.TEXT).pack(side="left")
        self.seg_mode = ctk.CTkSegmentedButton(
            modebar, values=["单开", "多开"], command=self._on_mode,
            font=self.fonts["body"], fg_color=T.SURFACE_2,
            selected_color=T.ACCENT, selected_hover_color=T.ACCENT_HOVER,
            unselected_color=T.SURFACE_2, unselected_hover_color=T.BORDER)
        self.seg_mode.set("多开" if self._multi else "单开")
        self.seg_mode.pack(side="left", padx=(10, 0))
        ctk.CTkButton(modebar, text="刷新", font=self.fonts["body"], width=72, height=30,
                      corner_radius=T.RADIUS_SM, fg_color=T.SURFACE_2, hover_color=T.BORDER,
                      command=self._enumerate).pack(side="right")

        # 窗口卡片列表
        self.body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.body.grid(row=2, column=0, sticky="nsew", padx=16, pady=(2, 4))
        self.body.grid_columnconfigure(0, weight=1)
        T.tune_scroll_speed(self.body)

        # 底部
        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.grid(row=3, column=0, sticky="ew", padx=20, pady=(4, 16))
        bottom.grid_columnconfigure(0, weight=1)
        self.status_lbl = ctk.CTkLabel(bottom, text="", font=self.fonts["small"], text_color=T.TEXT_DIM)
        self.status_lbl.grid(row=0, column=0, sticky="w")
        ctk.CTkButton(bottom, text="取消", font=self.fonts["body"], width=84, height=36,
                      corner_radius=T.RADIUS_SM, fg_color=T.SURFACE_2, hover_color=T.BORDER,
                      command=self._cancel).grid(row=0, column=1, padx=(0, 8))
        ctk.CTkButton(bottom, text="确定", font=self.fonts["btn"], width=110, height=36,
                      corner_radius=T.RADIUS_SM, fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER,
                      command=self._confirm).grid(row=0, column=2)

    # ------------------------------------------------------------------
    def _on_mode(self, value):
        self._multi = (value == "多开")
        self._render()

    def _set_alpha(self, a):
        for w in (self.app, self):
            try:
                w.attributes("-alpha", a)
            except Exception:
                pass

    def _enumerate(self):
        """枚举同名窗口并各截一张缩略图（截图时临时把本助手透明化，避免拍进自己）。"""
        wins = win_mod.locate_all(self.title_substr, self.offset)
        self._wins = []
        self._thumbs = []
        if not wins:
            self._render()
            return

        # 透明化批量截缩略图（alpha=0 不被 mss 拍到，但不整窗重建，省闪烁）
        self._set_alpha(0.0)
        try:
            self.update_idletasks()
            time.sleep(0.12)
            for w in wins:
                rect = w.rect()
                thumb = self._make_thumb(rect)
                self._wins.append((w, rect, thumb))
        finally:
            self._set_alpha(1.0)
            self.lift()
            self.focus_force()
        self._render()

    def _make_thumb(self, rect):
        if rect is None or Image is None:
            return None
        try:
            bgr = win_mod.grab(rect)
            if bgr is None:
                return None
            rgb = bgr[:, :, ::-1]          # BGR -> RGB
            img = Image.fromarray(rgb)
            w, h = img.size
            scale = self.THUMB_H / max(1, h)
            size = (max(1, int(w * scale)), self.THUMB_H)
            cimg = ctk.CTkImage(light_image=img, dark_image=img, size=size)
            self._thumbs.append(cimg)
            return cimg
        except Exception:
            return None

    def _render(self):
        for c in self.body.winfo_children():
            c.destroy()
        self._multi_vars = {}

        if not self._wins:
            ctk.CTkLabel(self.body, text=f"没检测到标题含「{self.title_substr}」的窗口。\n"
                                         "请先打开游戏（可多开），再点上方「刷新」。",
                         font=self.fonts["body"], text_color=T.TEXT_DIM, justify="left").grid(
                             row=0, column=0, sticky="w", padx=12, pady=24)
            self.status_lbl.configure(text="未检测到窗口")
            return

        # 单开：若记忆的序号越界则回退 0
        if self._single_var.get() >= len(self._wins):
            self._single_var.set(0)
        sel_multi = set(self.targets.get("multi_indices") or [])

        for i, (w, rect, thumb) in enumerate(self._wins):
            card = ctk.CTkFrame(self.body, fg_color=T.SURFACE, corner_radius=T.RADIUS_SM,
                                border_width=1, border_color=T.BORDER)
            card.grid(row=i, column=0, sticky="ew", pady=5, padx=4)
            card.grid_columnconfigure(2, weight=1)

            # 选择控件
            if self._multi:
                var = self._multi_vars.get(i)
                if var is None:
                    # 默认：之前选过就沿用；没选过(空)则默认全选
                    default_on = (i in sel_multi) if sel_multi else True
                    var = ctk.BooleanVar(value=default_on)
                    self._multi_vars[i] = var
                ctk.CTkCheckBox(card, text="", width=24, variable=var,
                                fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER).grid(
                                    row=0, column=0, padx=(12, 6), pady=10)
            else:
                ctk.CTkRadioButton(card, text="", width=24, variable=self._single_var, value=i,
                                   fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER).grid(
                                       row=0, column=0, padx=(12, 6), pady=10)

            # 缩略图
            if thumb is not None:
                ctk.CTkLabel(card, text="", image=thumb).grid(row=0, column=1, padx=(2, 10), pady=8)
            else:
                ctk.CTkLabel(card, text="🖥", font=self.fonts["title"]).grid(
                    row=0, column=1, padx=(2, 10), pady=8)

            # 信息
            info = ctk.CTkFrame(card, fg_color="transparent")
            info.grid(row=0, column=2, sticky="w")
            ctk.CTkLabel(info, text=f"号{i + 1}", font=self.fonts["body_b"],
                         text_color=T.TEXT).pack(anchor="w")
            if rect:
                meta = f"{rect[2]}×{rect[3]}  @ ({rect[0]},{rect[1]})"
            else:
                meta = "（窗口已失效）"
            ctk.CTkLabel(info, text=meta, font=self.fonts["small"],
                         text_color=T.TEXT_DIM).pack(anchor="w")
            ttl = (w.title or "").strip()
            if ttl:
                ctk.CTkLabel(info, text=ttl, font=self.fonts["small"], text_color=T.TEXT_DIM,
                             wraplength=320, justify="left").pack(anchor="w")

        # 尺寸一致性提示（多开共用一套标定要求同尺寸）
        sizes = {(r[2], r[3]) for _, r, _ in self._wins if r}
        warn = "" if len(sizes) <= 1 else "  ⚠ 窗口尺寸不一致，多开共用标定可能点错，建议统一分辨率"
        self.status_lbl.configure(text=f"检测到 {len(self._wins)} 个窗口" + warn,
                                  text_color=T.WARN if warn else T.TEXT_DIM)

    # ------------------------------------------------------------------
    def _confirm(self):
        self.targets["multi"] = self._multi
        if self._multi:
            idxs = [i for i, v in self._multi_vars.items() if v.get()]
            # 没勾任何号 → 视为「全部」（存空列表，运行时取全部）
            self.targets["multi_indices"] = sorted(idxs) if len(idxs) != len(self._wins) else []
        else:
            self.targets["single_index"] = int(self._single_var.get())
        # 读盘再写，避免覆盖其它地方刚改的配置
        cfg = cfg_mod.load_config()
        cfg["targets"] = {**cfg.get("targets", {}), **self.targets}
        cfg_mod.save_config(cfg)
        self.app.cfg = cfg
        self._close()

    def _cancel(self):
        self._close()

    def _close(self):
        if callable(self.on_done):
            try:
                self.on_done()
            except Exception:
                pass
        self.destroy()
