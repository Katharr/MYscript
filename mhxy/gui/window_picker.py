# -*- coding: utf-8 -*-
"""
选择窗口对话框（基础特性）。

让用户从「桌面上检测到的同名游戏窗口」里勾选脚本要操作的目标（**只有复选框一种控件**）：
  · 勾 1 个 = 单开；勾多个 = 多开（轮流操作）。没有「模式」开关，模式由勾选数量派生。

每个窗口渲成一张卡片，带【实时缩略图】+【角色名】帮你认出哪个号（号按屏幕位置左→右编号，
和 window.locate_all 的排序一致；角色名读客户端文件，认不出才退回「号1/号2」）。
选择结果写入 cfg["targets"]：multi（=勾选数>1）/ single_index / multi_indices——
字段仍是老结构，故 tasks 与 window.resolve_targets 全都不用改。

窗口身份用「位置序号」而非 HWND——三个号标题相同、HWND 重启会变，按摆放位置认号最稳。
"""

import threading
import time

import customtkinter as ctk

from . import theme as T
from ..core import accounts
from ..core import config as cfg_mod
from ..core import window as win_mod

try:
    from PIL import Image
except Exception:  # PIL 缺失时降级为无缩略图
    Image = None


class WindowPickerDialog(ctk.CTkToplevel):
    THUMB_H = 84   # 缩略图高度（像素），宽按窗口宽高比缩放

    def __init__(self, app, on_done=None, captain_ns=None):
        """captain_ns: 不为 None 时，每张窗口卡多一个「队长」单选——直接在带缩略图的卡片上指定
        哪个号当队长（解决下拉框「号123」看不出是哪个窗口的问题）。确定时把队长在所选窗口里的
        序号写入 cfg.tasks.<captain_ns>.captain_index（如组队走 captain_ns="teaming"）。"""
        super().__init__(app)
        self.app = app
        self.fonts = app.fonts
        self.on_done = on_done
        self.captain_ns = captain_ns

        self.cfg = cfg_mod.load_config()
        self.targets = dict(self.cfg.get("targets", {}))
        self.title_substr = self.cfg.get("window_title", "梦幻西游")
        self.offset = self.cfg.get("window_offset", [0, 0])

        self._wins = []          # [(GameWindow, rect, ctk_thumb_or_None)]
        self._labels = []        # 与 _wins 同序的显示名（「角色名（等级）」，认不出是「号N」）
        self._thumbs = []        # 防 GC
        self._multi_vars = {}    # index -> BooleanVar（唯一的勾选控件）
        self._sel = set(self.targets.get("multi_indices") or [])
        if not self._sel and not bool(self.targets.get("multi", False)):
            # 老配置（单开只存了 single_index）→ 复选里把那一个勾上
            self._sel = {int(self.targets.get("single_index", 0) or 0)}
        self._captain_var = ctk.IntVar(value=0)   # 队长的【绝对窗口序号】(0起)；仅 captain_ns 时用
        self._captain_inited = False

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
        self.grid_rowconfigure(1, weight=1)

        # 头部（标题 + 刷新）
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=20, pady=(16, 6))
        ctk.CTkLabel(top, text="选择窗口", font=self.fonts["title"], text_color=T.TEXT).pack(side="left")
        ctk.CTkButton(top, text="刷新", font=self.fonts["body"], width=72, height=30,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self._enumerate).pack(side="right")

        # 窗口卡片列表
        self.body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.body.grid(row=1, column=0, sticky="nsew", padx=16, pady=(2, 4))
        self.body.grid_columnconfigure(0, weight=1)
        T.tune_scroll_speed(self.body)

        # 底部
        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.grid(row=2, column=0, sticky="ew", padx=20, pady=(4, 16))
        bottom.grid_columnconfigure(0, weight=1)
        self.status_lbl = ctk.CTkLabel(bottom, text="", font=self.fonts["small"], text_color=T.TEXT_DIM)
        self.status_lbl.grid(row=0, column=0, sticky="w")
        ctk.CTkButton(bottom, text="取消", font=self.fonts["body"], width=84, height=36,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self._cancel).grid(row=0, column=1, padx=(0, 8))
        ctk.CTkButton(bottom, text="确定", font=self.fonts["btn"], width=110, height=36,
                      corner_radius=T.RADIUS_SM, fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER, text_color=T.ON_ACCENT,
                      command=self._confirm).grid(row=0, column=2)

    # ------------------------------------------------------------------
    def _update_status(self):
        """底部状态：检测到几个 / 勾了几个（勾 0 个时提示不可确定）。"""
        if not self._wins:
            return
        n = len(self._wins)
        sel = sum(1 for v in self._multi_vars.values() if v.get())
        sizes = {(r[2], r[3]) for _, r, _ in self._wins if r}
        warn = "" if len(sizes) <= 1 else "  ⚠ 窗口尺寸不一致，多开可能点错"
        cap = int(self.targets.get("max_windows", 0) or 0)
        if cap and sel > cap:
            warn += f"  ⚠ 超过上限 {cap} 个，只会操作前 {cap} 个"
        if sel == 0:
            self.status_lbl.configure(text="请至少勾选 1 个窗口", text_color=T.DANGER)
            return
        self.status_lbl.configure(text=f"检测到 {n} 个窗口 · 已选 {sel} 个" + warn,
                                  text_color=T.WARN if warn else T.TEXT_DIM)

    def _set_alpha(self, a):
        for w in (self.app, self):
            try:
                w.attributes("-alpha", a)
            except Exception:
                pass

    def _enumerate(self):
        """枚举同名窗口并各截一张缩略图（截图时临时把本助手透明化，避免拍进自己）。"""
        # 刷新前先把当前勾选记下来，免得重渲染后又退回记忆里的旧选择
        if self._multi_vars:
            self._sel = {i for i, v in self._multi_vars.items() if v.get()}
        wins = win_mod.locate_all(self.title_substr, self.offset)
        self._wins = []
        self._thumbs = []
        self._labels = []
        self._label_token = object()      # 使上一次刷新尚未返回的 OCR 结果失效
        if not wins:
            self._render()
            return

        # OCR 首次加载模型较慢，先按「号N」即时绘制，识别完成后再后台刷新真实名字。
        self._labels = [accounts.fallback_label(i) for i in range(len(wins))]
        token = object()
        self._label_token = token

        # 透明化批量截缩略图（alpha=0 不被 mss 拍到，但不整窗重建，省闪烁）
        self._set_alpha(0.0)
        try:
            self.update_idletasks()
            time.sleep(0.12)
            for w in wins:
                rect = w.rect()
                thumb = self._make_thumb(rect)
                self._wins.append((w, rect, thumb))
        except Exception:
            pass
        # 保持透明直到 OCR 结束，避免主界面盖住游戏标签条；后台回填时统一恢复。
        self._render()

        def read_labels():
            try:
                labels = accounts.labels_for(wins)
            except Exception:
                labels = [accounts.fallback_label(i) for i in range(len(wins))]

            def apply():
                if token is not getattr(self, "_label_token", None):
                    return
                try:
                    if not self.winfo_exists():
                        return
                    # OCR 期间用户可能已勾选/取消窗口，重绘前先保留即时选择。
                    self._sel = {i for i, var in self._multi_vars.items() if var.get()}
                    self._labels = labels
                    self._render()
                except Exception:
                    pass
                finally:
                    try:
                        self._set_alpha(1.0)
                        self.lift()
                        self.focus_force()
                    except Exception:
                        pass

            try:
                self.app.ui_post(apply)
            except Exception:
                pass

        threading.Thread(target=read_labels, daemon=True).start()

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
            empty = ctk.CTkLabel(self.body, text=f"没检测到「{self.title_substr}」窗口，"
                                         "请先打开游戏再点「刷新」。",
                         font=self.fonts["body"], text_color=T.TEXT_DIM, justify="left")
            empty.grid(row=0, column=0, sticky="ew", padx=12, pady=24)
            T.bind_wraplength(empty)
            self.status_lbl.configure(text="未检测到窗口")
            return

        # 队长初值：把存的 captain_index（所选窗口里的序号）映射回【绝对窗口序号】，只算一次。
        if self.captain_ns and not self._captain_inited:
            team_tc = cfg_mod.task_config(self.cfg, self.captain_ns)
            ci = int(team_tc.get("captain_index", 0) or 0)
            sel = sorted(self._sel) if self._sel else list(range(len(self._wins)))
            cap_abs = sel[ci] if 0 <= ci < len(sel) else (sel[0] if sel else 0)
            self._captain_var.set(cap_abs)
            self._captain_inited = True

        for i, (w, rect, thumb) in enumerate(self._wins):
            card = ctk.CTkFrame(self.body, fg_color=T.SURFACE, corner_radius=T.RADIUS_SM,
                                border_width=1, border_color=T.BORDER)
            card.grid(row=i, column=0, sticky="ew", pady=5, padx=4)
            card.grid_columnconfigure(2, weight=1)

            # 勾选框（唯一的选择控件）：勾 1 个=单开，勾多个=多开
            var = self._multi_vars.get(i)
            if var is None:
                # 默认：之前选过就沿用；没选过(空)则默认全选
                default_on = (i in self._sel) if self._sel else True
                var = ctk.BooleanVar(value=default_on)
                self._multi_vars[i] = var
            ctk.CTkCheckBox(card, text="", width=24, variable=var, command=self._update_status,
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
            info.grid(row=0, column=2, sticky="ew")
            lbl = self._labels[i] if i < len(self._labels) else accounts.fallback_label(i)
            ctk.CTkLabel(info, text=lbl, font=self.fonts["body_b"],
                         text_color=T.TEXT).pack(anchor="w")
            if rect:
                meta = f"{rect[2]}×{rect[3]}  @ ({rect[0]},{rect[1]})"
            else:
                meta = "（窗口已失效）"
            ctk.CTkLabel(info, text=meta, font=self.fonts["small"],
                         text_color=T.TEXT_DIM).pack(anchor="w")
            ttl = (w.title or "").strip()
            if ttl:
                ttl_lbl = ctk.CTkLabel(info, text=ttl, font=self.fonts["small"], text_color=T.TEXT_DIM,
                             justify="left")
                ttl_lbl.pack(fill="x")
                T.bind_wraplength(ttl_lbl)

            # 队长单选（带缩略图的卡上直接指定）
            if self.captain_ns:
                ctk.CTkRadioButton(card, text="队长", width=24, variable=self._captain_var, value=i,
                                   font=self.fonts["small"], fg_color=T.ACCENT,
                                   hover_color=T.ACCENT_HOVER).grid(row=0, column=3, padx=(6, 12))

        self._update_status()

    # ------------------------------------------------------------------
    def _confirm(self):
        idxs = sorted(i for i, v in self._multi_vars.items() if v.get())
        if not idxs:
            self.status_lbl.configure(text="请至少勾选 1 个窗口", text_color=T.DANGER)
            return
        all_n = len(self._wins)
        # 勾 1 个=单开；勾多个=多开。multi 只是派生标志，字段结构不变（tasks 照旧读它）。
        self.targets["multi"] = len(idxs) > 1
        self.targets["single_index"] = idxs[0]
        # 存空列表＝运行时取全部；否则存所选绝对序号（勾满=空，和新窗口自动纳入的旧语义一致）
        self.targets["multi_indices"] = idxs if len(idxs) != all_n else []
        captain_index = None
        if self.captain_ns:
            cap_abs = self._captain_var.get()
            if cap_abs not in idxs:            # 队长没勾上 → 落到第一个勾选的号
                cap_abs = idxs[0]
            captain_index = idxs.index(cap_abs)
        # 读盘再写，避免覆盖其它地方刚改的配置
        cfg = cfg_mod.load_config()
        cfg["targets"] = {**cfg.get("targets", {}), **self.targets}
        if captain_index is not None:
            team_tc = cfg_mod.task_config(cfg, self.captain_ns)
            team_tc["captain_index"] = captain_index
            cfg_mod.set_task_config(cfg, self.captain_ns, team_tc)
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
