# -*- coding: utf-8 -*-
"""
标定对话框（全部在 GUI 内完成，无黑窗、无子进程）。

按任务的 `CALIBRATION` spec 驱动渲染（见 tasks/base.py Task.CALIBRATION），三类卡片：
1. 区域与按钮：框选区域 → 存为相对游戏窗口的 [x,y,w,h]，写入 tc["regions"][key]。
2. 标志模板：框选并裁图 → 存成 templates/tm_<key>.png，写入 tc["templates"][key]。
3. 监控清单（仅秒装备等 watchlist=True 的任务）：框选装备图标+名字加入清单。

框选时把本助手窗口透明化，确保截图里只有游戏画面。新增任务无需改本文件，只要在 Task 上写 CALIBRATION。
"""

import time

import customtkinter as ctk

from . import theme as T
from .roi_overlay import select_roi_on_screen
from ..core import config as cfg_mod
from ..core import window as win_mod
from ..core import vision
from ..core.teaming import TEAM_CALIBRATION
from ..tasks import get_task

# 非注册的「共享命名空间」标定 spec（key = 写入 cfg.tasks.<key>）。
# 组队是跨任务共享资产、不是可运行任务，故 get_task("teaming") 拿不到，走这里。
_VIRTUAL_SPECS = {"teaming": ("组队（全局共享）", TEAM_CALIBRATION)}


# ----------------------------------------------------------------------
# 「无弹窗」直接标定：供「标定队长ID」这类只标一项的快捷入口复用——点一下就直接在
# 当前屏幕框选，不再开 CalibrateDialog 窗口。逻辑与 CalibrateDialog._grab_roi 同源
# （藏起界面→当前屏幕框选→按落点反查参照窗口算相对坐标），抽成模块级函数共享。
# ----------------------------------------------------------------------
def grab_roi_on_app(app, cfg, prompt, with_crop=False, toast=None, alpha_windows=None):
    """在【当前屏幕】框选一块区域，返回 (rel_roi, crop)；失败/取消返回 (None, None)。
    不激活/不切前台：你把哪个号摆在前面就标到哪个，框完按落点反查参照窗口算相对坐标。
    toast: 可选回调 toast(msg, color)；alpha_windows: 框选期间临时隐身的窗口（默认仅 app）。"""
    title = cfg.get("window_title", "梦幻西游")
    offset = cfg.get("window_offset", [0, 0])
    windows = alpha_windows or (app,)

    def _set_alpha(a):
        for w in windows:
            try:
                w.attributes("-alpha", a)
            except Exception:
                pass

    hint_wins = win_mod.locate_all(title, offset)
    if not hint_wins:
        if toast:
            toast(f"没找到游戏窗口（标题含「{title}」），请先打开游戏。", T.WARN)
        return None, None
    hr = hint_wins[0].rect()
    center = (hr[0] + hr[2] // 2, hr[1] + hr[3] // 2) if hr else None

    _set_alpha(0.0)
    try:
        app.update_idletasks()
    except Exception:
        pass
    time.sleep(0.12)

    try:
        result = select_roi_on_screen(app, prompt, around_point=center, with_crop=with_crop)
    finally:
        _set_alpha(1.0)
        try:
            app.lift()
            app.focus_force()
        except Exception:
            pass

    if with_crop:
        roi_abs, crop = result
    else:
        roi_abs, crop = result, None
    if roi_abs is None:
        return None, None

    cx = roi_abs[0] + roi_abs[2] // 2
    cy = roi_abs[1] + roi_abs[3] // 2
    ref = win_mod.window_at_point(title, offset, cx, cy)
    wr = ref.rect() if ref else None
    if wr is None:
        if toast:
            toast("框选区域不在任何游戏窗口内：请把要标的窗口移到前面，框在它的画面里。", T.WARN)
        return None, None
    cfg.setdefault("targets", {})["base_size"] = [wr[2], wr[3]]
    cfg_mod.save_config(cfg)
    rel = [roi_abs[0] - wr[0], roi_abs[1] - wr[1], roi_abs[2], roi_abs[3]]
    return rel, crop


def calibrate_template_direct(app, task_name, key, name, toast=None):
    """无弹窗直接框选并裁图存成模板，写入 cfg.tasks.<task_name>.templates[key]。返回 True=已保存。
    供「标定队长ID」按钮直接调用（task_name="teaming", key="leader_id"）。"""
    cfg = cfg_mod.load_config()
    rel, crop = grab_roi_on_app(app, cfg, f"框选「{name}」（会裁下来存成模板图）",
                                with_crop=True, toast=toast)
    if rel is None:
        return False
    if crop is None or crop.size == 0:
        if toast:
            toast("截图失败，请重试。", T.DANGER)
        return False
    rel_path = f"templates/tm_{key}.png"
    if not vision.save_image(rel_path, crop):
        if toast:
            toast("保存模板图失败。", T.DANGER)
        return False
    tc = cfg_mod.task_config(cfg, task_name)
    tc.setdefault("templates", {})[key] = rel_path
    cfg_mod.set_task_config(cfg, task_name, tc)
    cfg_mod.save_config(cfg)
    return True


def _pick_cols(n):
    """按缩略图张数选画廊列数。用户拍板：标定太长时按「尽量均匀分行」原则适当加宽、增加每行卡片数。
    策略：在 [2, 上限] 列里优先选「行数最少」（加宽降低高度），并列时选「末行最满（最均匀）」。
    上限随 n 增大（小数量不滥用宽列）：n≤4→2 列，≤6→3 列，更多→4 列。"""
    import math
    if n <= 1:
        return 2
    max_cols = 2 if n <= 4 else (3 if n <= 6 else 4)
    best, best_key = 2, None
    for c in range(2, max_cols + 1):
        rows = math.ceil(n / c)
        empty = c - (n - (rows - 1) * c)   # 末行空位数（越小越均匀）
        key = (rows, empty)                # 先比行数，再比空位
        if best_key is None or key < best_key:
            best, best_key = c, key
    return best


class CalibrateDialog(ctk.CTkToplevel):
    def __init__(self, app, task_name="sniper", on_done=None, only=None, exclude=None):
        """only: 可选的 key 白名单——只渲染这些区域/模板项（其余隐藏）。None=渲染全部项。
        exclude: 可选的 key 黑名单——隐藏这些项（如组队全套标定排除 leader_id，因它已被
        「标定队长ID」按钮单独标）。only 与 exclude 写入的都是同一命名空间，多处入口天然同步。"""
        super().__init__(app)
        self.app = app
        self.fonts = app.fonts
        self.on_done = on_done
        self.task_name = task_name

        task_cls = get_task(task_name)
        if task_cls is not None:
            self.spec = getattr(task_cls, "CALIBRATION",
                                {"regions": [], "templates": [], "watchlist": False})
            title_name = getattr(task_cls, "title", task_name)
        elif task_name in _VIRTUAL_SPECS:
            title_name, self.spec = _VIRTUAL_SPECS[task_name]
        else:
            self.spec = {"regions": [], "templates": [], "watchlist": False}
            title_name = task_name

        if only:
            only = set(only)
            self.spec = {
                "regions": [it for it in self.spec.get("regions", []) if it[0] in only],
                "templates": [it for it in self.spec.get("templates", []) if it[0] in only],
                "watchlist": False,
            }
        if exclude:
            exclude = set(exclude)
            self.spec = {
                "regions": [it for it in self.spec.get("regions", []) if it[0] not in exclude],
                "templates": [it for it in self.spec.get("templates", []) if it[0] not in exclude],
                "watchlist": self.spec.get("watchlist", False),
            }

        self.cfg = cfg_mod.load_config()
        self.tc = cfg_mod.task_config(self.cfg, task_name)

        # 缩略图画廊列数：模板/装备越多列越多，行尽量匀（_pick_cols）；窗口随列数适当加宽。
        n_items = len(self.spec.get("templates", []))
        if self.spec.get("watchlist"):
            n_items = max(n_items, len(self.tc.get("watchlist", [])))
        has_gallery = bool(self.spec.get("templates")) or bool(self.spec.get("watchlist"))
        self.n_cols = max(2, _pick_cols(n_items)) if has_gallery else 2
        win_w = {2: 680, 3: 800, 4: 940}.get(self.n_cols, 680)

        self.title(f"标定 · {title_name}")
        self.geometry(f"{win_w}x640")
        self.minsize(560, 420)
        self.configure(fg_color=T.BG)
        self.transient(app)

        self.region_rows = {}
        self.template_rows = {}          # 兼容保留（模板改走缩略图画廊后已不用）
        self.template_grid = None        # 模板缩略图画廊容器（_render_templates 填充）
        self._thumbs = []                # 防缩略图被 GC（_refresh 开头清空再重建）
        self._full_window_keys = set()   # 支持「留空=整窗」的大检测区 key
        self.item_list = None

        self._build()
        self._refresh()
        self.after(120, self._center_on_app)

    # ------------------------------------------------------------------
    def _center_on_app(self):
        try:
            self.lift()
            self.focus_force()
        except Exception:
            pass

    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        body.grid(row=0, column=0, sticky="nsew", padx=4, pady=(4, 0))
        body.grid_columnconfigure(0, weight=1)
        T.tune_scroll_speed(body)

        row = 0
        top = ctk.CTkFrame(body, fg_color="transparent")
        top.grid(row=row, column=0, sticky="ew", padx=16, pady=(14, 8)); row += 1
        ctk.CTkLabel(top, text="标定向导", font=self.fonts["title"], text_color=T.TEXT).pack(anchor="w")
        sub = ctk.CTkLabel(top, text="先把游戏切到对应界面，再按提示逐项框选。框选时本助手会临时隐身。",
                     justify="left", font=self.fonts["small"], text_color=T.TEXT_DIM)
        sub.pack(fill="x", pady=(4, 0))
        T.bind_wraplength(sub)

        # ① 区域与按钮
        regions = self.spec.get("regions", [])
        if regions:
            rcard = self._card(body, row); row += 1
            ctk.CTkLabel(rcard, text="① 区域与按钮", font=self.fonts["h2"], text_color=T.TEXT).grid(
                row=0, column=0, columnspan=3, sticky="w", padx=16, pady=(14, 6))
            rhint = ctk.CTkLabel(rcard, text="这些只是记录屏幕上一块位置（坐标），本身没有图片，标好显示「● 已框选」即可。",
                         font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left")
            rhint.grid(row=1, column=0, columnspan=3, sticky="ew", padx=16, pady=(0, 4))
            T.bind_wraplength(rhint, padding=32)
            for i, item in enumerate(regions):
                key, name, desc = item[0], item[1], item[2]
                full_window = len(item) > 3 and bool(item[3])   # 第4元素=True 表示可整窗
                if full_window:
                    self._full_window_keys.add(key)
                self._spec_row(rcard, 2 + i, key, name, desc, self.region_rows,
                               lambda k=key, n=name: self._calibrate_region(k, n),
                               full_window=full_window)
            ctk.CTkFrame(rcard, fg_color="transparent", height=8).grid(row=99, column=0)

        # ② 标志模板（框选裁图）
        templates = self.spec.get("templates", [])
        if templates:
            tcard = self._card(body, row); row += 1
            ctk.CTkLabel(tcard, text="② 标志模板（框选裁图）", font=self.fonts["h2"], text_color=T.TEXT).grid(
                row=0, column=0, sticky="w", padx=16, pady=(14, 6))
            thint = ctk.CTkLabel(tcard, text="框小而独特的区域（按钮/文字/图标），别框会变的数字或背景。",
                         font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left")
            thint.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 6))
            T.bind_wraplength(thint, padding=32)
            grid = ctk.CTkFrame(tcard, fg_color="transparent")
            grid.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 10))
            for c in range(self.n_cols):
                grid.grid_columnconfigure(c, weight=1, uniform="tpl")
            self.template_grid = grid

        # ③ 监控清单（仅 watchlist=True 的任务）
        if self.spec.get("watchlist"):
            self._build_watchlist_card(body, row); row += 1

        # 底部固定「完成」
        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.grid(row=1, column=0, sticky="ew", padx=20, pady=(4, 16))
        bottom.grid_columnconfigure(0, weight=1)
        self.status_lbl = ctk.CTkLabel(bottom, text="", font=self.fonts["small"], text_color=T.TEXT_DIM)
        self.status_lbl.grid(row=0, column=0, sticky="w")
        ctk.CTkButton(bottom, text="完成", font=self.fonts["btn"], width=110, height=38,
                      corner_radius=T.RADIUS_SM, fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER, text_color=T.ON_ACCENT,
                      command=self._close).grid(row=0, column=1, sticky="e")

    def _card(self, parent, grid_row):
        c = ctk.CTkFrame(parent, fg_color=T.SURFACE, corner_radius=T.RADIUS,
                         border_width=1, border_color=T.BORDER)
        c.grid(row=grid_row, column=0, sticky="ew", padx=16, pady=(8, 8))
        c.grid_columnconfigure(0, weight=1)
        return c

    def _spec_row(self, card, grid_row, key, name, desc, store, command, full_window=False):
        row = ctk.CTkFrame(card, fg_color=T.SURFACE_2, corner_radius=T.RADIUS_SM)
        row.grid(row=grid_row, column=0, sticky="ew", padx=12, pady=4)
        row.grid_columnconfigure(0, weight=1)
        txt = ctk.CTkFrame(row, fg_color="transparent")
        txt.grid(row=0, column=0, sticky="w", padx=12, pady=8)
        ctk.CTkLabel(txt, text=name, font=self.fonts["body_b"], text_color=T.TEXT).pack(anchor="w")
        ctk.CTkLabel(txt, text=desc, font=self.fonts["small"], text_color=T.TEXT_DIM).pack(anchor="w")
        status = ctk.CTkLabel(row, text="", font=self.fonts["small"], width=90)
        status.grid(row=0, column=1, padx=6)
        # 可整窗的大检测区：多给一个「用整窗」按钮（把该区清空＝整窗检测）
        if full_window:
            ctk.CTkButton(row, text="用整窗", font=self.fonts["small"], width=58, height=30,
                          corner_radius=T.RADIUS_SM, fg_color="transparent", hover_color=T.BORDER, text_color=T.TEXT,
                          border_width=1, border_color=T.BORDER,
                          command=lambda k=key, n=name: self._use_full_window(k, n)).grid(
                              row=0, column=2, padx=(0, 4))
        ctk.CTkButton(row, text="框选", font=self.fonts["body"], width=72, height=30,
                      corner_radius=T.RADIUS_SM, fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER, text_color=T.ON_ACCENT,
                      command=command).grid(row=0, column=3, padx=(6, 12))
        store[key] = status

    def _use_full_window(self, key, name):
        """把某个可整窗的大检测区清空 → 运行时用整个窗口当检测区。"""
        self.tc.setdefault("regions", {})[key] = None
        self._save()
        self._refresh()
        self._toast(f"「{name}」已设为整窗检测（无需框选）", T.SUCCESS)

    def _build_watchlist_card(self, parent, grid_row):
        icard = self._card(parent, grid_row)
        head = ctk.CTkFrame(icard, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 6))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text="③ 要抢的装备", font=self.fonts["h2"], text_color=T.TEXT).grid(
            row=0, column=0, sticky="w")
        ctk.CTkButton(head, text="＋ 框选添加", font=self.fonts["body"], width=110, height=32,
                      corner_radius=T.RADIUS_SM, fg_color=T.SUCCESS, hover_color=T.SUCCESS_HOVER,
                      text_color=T.BG, command=self._add_item).grid(row=0, column=1, sticky="e")
        ihint = ctk.CTkLabel(icard, text="提示：连「图标 + 名字」一起框，别框价格（价格会变，框了反而认不出）。",
                     font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left")
        ihint.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 6))
        T.bind_wraplength(ihint, padding=32)
        self.item_list = ctk.CTkFrame(icard, fg_color="transparent")
        self.item_list.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 12))
        for c in range(self.n_cols):
            self.item_list.grid_columnconfigure(c, weight=1, uniform="wl")

    # ------------------------------------------------------------------
    # 框选：藏起自己 -> 截图框选 -> 复原。返回 (rel_roi, crop)
    # ------------------------------------------------------------------
    def _grab_roi(self, prompt, with_crop=False):
        # 框选逻辑抽进模块级 grab_roi_on_app（与「无弹窗直接标定」共享）：不激活/不切前台，
        # 只在【当前屏幕】框选，框完按落点反查参照窗口算相对坐标——你把哪个号摆前面就标到哪个。
        # 这里多传 self 让对话框自身也一并隐身，且复用 self.cfg（_save() 随后会回存 task_config）。
        return grab_roi_on_app(self.app, self.cfg, prompt, with_crop=with_crop,
                               toast=self._toast, alpha_windows=(self.app, self))

    # ---- 区域标定 ----
    def _calibrate_region(self, key, name):
        rel, _crop = self._grab_roi(f"框选「{name}」")
        if rel is None:
            return
        self.tc.setdefault("regions", {})[key] = rel
        self._save()
        self._refresh()
        self._toast(f"已记录 {name}：{rel}", T.SUCCESS)

    # ---- 标志模板标定（裁图存盘）----
    def _calibrate_template(self, key, name):
        rel, crop = self._grab_roi(f"框选「{name}」（会裁下来存成模板图）", with_crop=True)
        if rel is None:
            return
        if crop is None or crop.size == 0:
            self._toast("截图失败，请重试。", T.DANGER)
            return
        rel_path = f"templates/tm_{key}.png"
        if not vision.save_image(rel_path, crop):
            self._toast("保存模板图失败。", T.DANGER)
            return
        self.tc.setdefault("templates", {})[key] = rel_path
        self._save()
        self._refresh()
        self._toast(f"已记录模板 {name}", T.SUCCESS)

    # ---- 添加装备（watchlist）----
    def _add_item(self):
        rel, crop = self._grab_roi("框选要抢的装备（图标+名字）", with_crop=True)
        if rel is None:
            return
        if crop is None or crop.size == 0:
            self._toast("截图失败，请重试。", T.DANGER)
            return
        name = self._ask_name()
        if not name:
            return
        rel_path = f"templates/{name}.png"
        if not vision.save_image(rel_path, crop):
            self._toast("保存模板图失败。", T.DANGER)
            return
        self.tc.setdefault("watchlist", []).append(
            {"name": name, "template": rel_path, "max_price": None})
        self._save()
        self._refresh()
        self._toast(f"已添加装备：{name}", T.SUCCESS)

    def _ask_name(self):
        dlg = ctk.CTkInputDialog(text="给这件装备起个名字（中英文均可）：", title="命名")
        raw = dlg.get_input()
        if raw is None:
            return None
        name = raw.strip().replace("/", "_").replace("\\", "_").replace(" ", "_")
        if not name:
            name = f"item_{int(time.time())}"
        existing = {it["name"] for it in self.tc.get("watchlist", [])}
        base, n = name, 2
        while name in existing:
            name = f"{base}_{n}"
            n += 1
        return name

    def _delete_item(self, idx):
        wl = self.tc.get("watchlist", [])
        if 0 <= idx < len(wl):
            removed = wl.pop(idx)
            self._save()
            self._refresh()
            self._toast(f"已删除：{removed.get('name', '?')}", T.TEXT_DIM)

    # ------------------------------------------------------------------
    def _refresh(self):
        self._thumbs.clear()
        regions = self.tc.get("regions", {})
        for key, status in self.region_rows.items():
            if regions.get(key):
                status.configure(text="● 已框选", text_color=T.SUCCESS)
            elif key in self._full_window_keys:
                status.configure(text="○ 整窗(默认)", text_color=T.TEXT_DIM)
            else:
                status.configure(text="○ 未标定", text_color=T.TEXT_DIM)
        self._render_templates()
        self._render_watchlist()

    # ---- 模板缩略图画廊（每次 _refresh 整段重画，照搬 leader_gallery 的范式）----
    def _render_templates(self):
        if self.template_grid is None:
            return
        from .app import load_thumb     # 延迟导入避免与 app.py 循环依赖
        for w in self.template_grid.winfo_children():
            w.destroy()
        saved = self.tc.get("templates", {})
        for i, (key, name, desc) in enumerate(self.spec.get("templates", [])):
            r, col = divmod(i, self.n_cols)
            rel = saved.get(key)
            thumb = load_thumb(rel, self._thumbs, max_h=46) if rel else None
            self._thumb_card(self.template_grid, r, col, name=name,
                             thumb=thumb, has_path=bool(rel), rel=rel,
                             btn_text=("重新标定" if thumb is not None else "去标定"),
                             btn_cmd=lambda k=key, n=name: self._calibrate_template(k, n))

    # ---- 装备缩略图画廊（watchlist）----
    def _render_watchlist(self):
        if self.item_list is None:
            return
        from .app import load_thumb
        for w in self.item_list.winfo_children():
            w.destroy()
        wl = self.tc.get("watchlist", [])
        if not wl:
            empty = ctk.CTkLabel(self.item_list, text="还没有装备，点右上「＋ 框选添加」。",
                         font=self.fonts["body"], text_color=T.TEXT_DIM)
            empty.grid(row=0, column=0, columnspan=self.n_cols, sticky="ew", padx=12, pady=16)
            T.bind_wraplength(empty, padding=20)
            return
        for i, it in enumerate(wl):
            r, col = divmod(i, self.n_cols)
            rel = it.get("template")
            thumb = load_thumb(rel, self._thumbs, max_h=46) if rel else None
            self._thumb_card(self.item_list, r, col, name=it.get("name", "?"),
                             thumb=thumb, has_path=bool(rel), rel=rel,
                             btn_text="删除", danger_btn=True,
                             btn_cmd=lambda idx=i: self._delete_item(idx))

    # ---- 单张缩略图卡片（模板/装备共用）：名字+状态徽标 / 缩略图(占位) / 操作按钮，三态等高 ----
    def _thumb_card(self, parent, r, col, name, thumb, has_path, rel,
                    btn_text, btn_cmd, danger_btn=False):
        ok = thumb is not None
        card = ctk.CTkFrame(parent, fg_color=T.SURFACE_2, corner_radius=T.RADIUS_SM,
                            border_width=2, border_color=(T.SUCCESS if ok else T.BORDER))
        card.grid(row=r, column=col, sticky="nsew", padx=6, pady=6)
        card.grid_columnconfigure(0, weight=1)

        head = ctk.CTkFrame(card, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 0))
        head.grid_columnconfigure(0, weight=1)
        nm = ctk.CTkLabel(head, text=name, font=self.fonts["body_b"], text_color=T.TEXT,
                          justify="left", anchor="w")
        nm.grid(row=0, column=0, sticky="ew")
        T.bind_wraplength(nm)
        ctk.CTkLabel(head, text=("● 已裁图" if ok else "○ 未标定"), font=self.fonts["small"],
                     text_color=(T.SUCCESS if ok else T.TEXT_DIM)).grid(row=0, column=1, sticky="e", padx=(6, 0))

        # 缩略图 / 占位框：固定高度让有图/无图卡片等高，网格不参差
        holder = ctk.CTkFrame(card, fg_color=T.SURFACE, corner_radius=T.RADIUS_SM,
                              border_width=1, border_color=T.BORDER, height=58)
        holder.grid(row=1, column=0, sticky="ew", padx=10, pady=8)
        holder.grid_propagate(False)
        holder.grid_columnconfigure(0, weight=1)
        holder.grid_rowconfigure(0, weight=1)
        if ok:
            ctk.CTkLabel(holder, text="", image=thumb).grid(row=0, column=0)
        elif has_path:
            ctk.CTkLabel(holder, text="（图片丢失）", font=self.fonts["small"],
                         text_color=T.WARN).grid(row=0, column=0)
        else:
            ctk.CTkLabel(holder, text="○ 未标定", font=self.fonts["small"],
                         text_color=T.TEXT_DIM).grid(row=0, column=0)

        ctk.CTkButton(card, text=btn_text, font=self.fonts["small"], height=30,
                      corner_radius=T.RADIUS_SM, text_color=T.TEXT,
                      fg_color=("transparent" if danger_btn else T.BTN),
                      hover_color=(T.DANGER if danger_btn else T.BTN_HOVER),
                      border_width=1, border_color=T.BORDER,
                      command=btn_cmd).grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 10))

    def _save(self):
        cfg_mod.set_task_config(self.cfg, self.task_name, self.tc)
        cfg_mod.save_config(self.cfg)

    def _toast(self, msg, color=T.TEXT_DIM):
        self.status_lbl.configure(text=msg, text_color=color)

    def _close(self):
        if callable(self.on_done):
            try:
                self.on_done()
            except Exception:
                pass
        self.destroy()
