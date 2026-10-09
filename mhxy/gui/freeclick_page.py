# -*- coding: utf-8 -*-
"""FreeClickPage：独立页面，保留既有布局与任务控制。"""
import customtkinter as ctk
from . import theme as T
from ..core import calib_profiles as calib
from ..core import config as cfg_mod
from ..core.runtime import get_runtime
from ..tasks import get_task
from .widgets import Card, Pill, load_thumb, bind_wraplength


class FreeClickPage(ctk.CTkFrame):
    """自由点击：自己框一批截图当模板，运行时按清单顺序循环识别、认到就点。

    清单即顺序（用户拍板：左键按住行左侧拖动上下移动调序），行内「重新标定」「删除」。
    拖拽调序的实现要点（改前务必看懂，否则极易改回「拖了没用/拖飞」）：
      · 按下 → 记录被拖的行，只在 <B1-Motion> 里【改 _rows/_items 顺序 + 重新 grid】，
        不销毁重建任何控件——一重建，正在拖的那个控件就被销毁了，后续 motion 全丢。
      · 行落在哪个名次由「指针 y_root 与各行中线比较」得出（_index_at），
        搬完后指针必落在被拖行的上/下半区 → 结果稳定，不会左右横跳（无振荡）。
      · 按钮/绑定全部按【行对象】回调，绝不按当时的下标闭包：调序后下标会失效，
        按旧下标点「删除」会删错一行。
      · 落手才写盘；拖动期间只动内存副本 self._items。运行中禁止改清单。
    """

    TASK_NAME = "freeclick"
    LOG_SOURCE = "自由点击"
    ROW_H = 56          # 行高（几何兜底：winfo_height 还没算出来时按它估落点）

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.fonts = app.fonts
        self.runner = None
        self._thumbs = []       # 防止缩略图被 GC
        self._rows = []         # 行控件（显示顺序）
        self._items = []        # 清单的内存副本（与 _rows 同序；落手才写盘）
        self._sig = None        # 内容签名：没变就不重建（切页反复 refresh 也不卡）
        self._drag_idx = None
        self._drag_from = None
        self._drag_name = "?"
        self._selected_list_id = None
        self._lists = []
        self._edit_controls = []

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

        ctk.CTkLabel(bar, text="自由点击", font=self.fonts["title"], text_color=T.TEXT).grid(
            row=0, column=0, sticky="w")

        right = ctk.CTkFrame(bar, fg_color="transparent")
        right.grid(row=0, column=1, sticky="e")
        self.pill_game = Pill(right, self.fonts)
        self.pill_game.pack(side="left", padx=(0, 8))
        self.pill_mode = Pill(right, self.fonts)
        self.pill_mode.pack(side="left")

    # ---- 控制区：运行 / 清单 / 当前清单编辑 ----
    def _build_control(self):
        card = Card(self)
        card.grid(row=1, column=0, sticky="ew", padx=4, pady=(0, 14))
        card.grid_columnconfigure(0, weight=1)

        # 运行操作单独占一行，避免和清单管理混成一组。
        run_bar = ctk.CTkFrame(card, fg_color="transparent")
        run_bar.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 12))
        run_bar.grid_columnconfigure(1, weight=1)
        self.btn_run = ctk.CTkButton(
            run_bar, text="▶  开始自由点击", font=self.fonts["btn"], height=46, width=200,
            corner_radius=T.RADIUS_SM, fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER,
            text_color=T.ON_ACCENT, command=self._toggle_run)
        self.btn_run.grid(row=0, column=0, sticky="w")
        ctk.CTkButton(
            run_bar, text="选择窗口", font=self.fonts["body"], height=36, width=104,
            corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER,
            text_color=T.TEXT, border_width=1, border_color=T.BORDER,
            command=lambda: self.app.open_window_picker(self.refresh)).grid(row=0, column=2, sticky="e")

        ctk.CTkFrame(card, fg_color=T.BORDER, height=1).grid(
            row=1, column=0, sticky="ew", padx=16, pady=(0, 12))

        # 当前清单管理单独成组：下拉框可伸缩，操作按钮保持固定尺寸。
        list_bar = ctk.CTkFrame(card, fg_color="transparent")
        list_bar.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 10))
        list_bar.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(list_bar, text="当前清单", font=self.fonts["body_b"],
                     text_color=T.TEXT).grid(row=0, column=0, sticky="w", padx=(0, 12))
        self.list_menu = ctk.CTkOptionMenu(
            list_bar, values=["未命名清单"], height=36, width=180,
            font=self.fonts["body"], fg_color=T.SURFACE_2, button_color=T.BTN,
            button_hover_color=T.BTN_HOVER, text_color=T.TEXT,
            command=self._select_list)
        self.list_menu.grid(row=0, column=1, sticky="ew", padx=(0, 12))

        self.btn_new_list = ctk.CTkButton(
            list_bar, text="＋ 新建", font=self.fonts["small"], height=36, width=82,
            corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER,
            text_color=T.TEXT, border_width=1, border_color=T.BORDER,
            command=self._create_list)
        self.btn_new_list.grid(row=0, column=2, padx=(0, 8))
        self.btn_rename_list = ctk.CTkButton(
            list_bar, text="重命名", font=self.fonts["small"], height=36, width=76,
            corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER,
            text_color=T.TEXT, border_width=1, border_color=T.BORDER,
            command=self._rename_list)
        self.btn_rename_list.grid(row=0, column=3, padx=(0, 8))
        self.btn_delete_list = ctk.CTkButton(
            list_bar, text="删除", font=self.fonts["small"], height=36, width=64,
            corner_radius=T.RADIUS_SM, fg_color="transparent", hover_color=T.DANGER,
            text_color=T.TEXT, border_width=1, border_color=T.BORDER,
            command=self._delete_list)
        self.btn_delete_list.grid(row=0, column=4)

        edit_bar = ctk.CTkFrame(card, fg_color="transparent")
        edit_bar.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 10))
        edit_bar.grid_columnconfigure(1, weight=1)
        self.btn_add_shot = ctk.CTkButton(
            edit_bar, text="＋ 框选加截图", font=self.fonts["body"], height=36, width=124,
            corner_radius=T.RADIUS_SM, fg_color=T.SUCCESS, hover_color=T.SUCCESS_HOVER,
            text_color=T.ON_ACCENT, command=self._add_shot)
        self.btn_add_shot.grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.btn_refresh = ctk.CTkButton(
            edit_bar, text="刷新配置", font=self.fonts["body"], height=36, width=104,
            corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER,
            text_color=T.TEXT, border_width=1, border_color=T.BORDER,
            command=self.refresh)
        self.btn_refresh.grid(row=0, column=1, sticky="w")

        opts = ctk.CTkFrame(card, fg_color="transparent")
        opts.grid(row=4, column=0, sticky="ew", padx=16, pady=(2, 16))
        self.switch_mode = ctk.CTkSwitch(opts, text="实战模式", font=self.fonts["body"],
                                         progress_color=T.DANGER, command=self._toggle_mode)
        self.switch_mode.pack(anchor="w")
        self._edit_controls = [self.list_menu, self.btn_new_list, self.btn_rename_list,
                               self.btn_delete_list, self.btn_add_shot, self.btn_refresh]

    # ---- 主体：截图清单（铺满；日志已移到全局右栏）----
    def _build_body(self):
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=2, column=0, sticky="nsew", padx=4)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)

        card = Card(body)
        card.grid(row=0, column=0, sticky="nsew")
        card.grid_rowconfigure(1, weight=1)
        card.grid_columnconfigure(0, weight=1)
        head = ctk.CTkFrame(card, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 8))
        head.grid_columnconfigure(0, weight=1)
        self.lbl_list_title = ctk.CTkLabel(
            head, text="截图清单", font=self.fonts["h2"], text_color=T.TEXT,
            anchor="w")
        self.lbl_list_title.grid(row=0, column=0, sticky="ew")
        self.lbl_count = ctk.CTkLabel(head, text="", font=self.fonts["small"], text_color=T.TEXT_DIM)
        self.lbl_count.grid(row=0, column=1, sticky="e")
        self.list_frame = ctk.CTkScrollableFrame(card, fg_color="transparent")
        self.list_frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 12))
        self.list_frame.grid_columnconfigure(0, weight=1)
        T.tune_scroll_speed(self.list_frame)

    # ------------------------------------------------------------------
    # 数据刷新
    # ------------------------------------------------------------------
    def refresh(self):
        self.app.cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(self.app.cfg, self.TASK_NAME)
        dry = tc.get("dry_run", True)
        (self.switch_mode.select if not dry else self.switch_mode.deselect)()
        self._render_mode_pill(dry)
        lists = self._ensure_lists(tc)
        self._lists = lists
        selected = tc.get("selected_list")
        entry = next((x for x in lists if x.get("id") == selected), lists[0])
        self._selected_list_id = entry["id"]
        if tc.get("selected_list") != self._selected_list_id or not tc.get("lists"):
            tc["selected_list"] = self._selected_list_id
            tc["lists"] = lists
            cfg_mod.set_task_config(self.app.cfg, self.TASK_NAME, tc)
            cfg_mod.save_config(self.app.cfg)
        list_name = entry.get("name", "未命名清单")
        self.list_menu.configure(values=[x.get("name", "未命名清单") for x in lists])
        self.list_menu.set(list_name)
        self.lbl_list_title.configure(text=f"{list_name} · 截图清单")
        self.btn_delete_list.configure(state="disabled" if len(lists) <= 1 else "normal")
        self._render_items(entry.get("items") or [])
        self._set_edit_controls(not self._busy())

    @staticmethod
    def _ensure_lists(tc):
        lists = tc.get("lists") or []
        if not lists:
            import uuid
            lists = [{"id": uuid.uuid4().hex, "name": "清单 1", "items": [dict(x) for x in (tc.get("items") or [])]}]
        else:
            lists = [{"id": x.get("id") or __import__("uuid").uuid4().hex,
                      "name": x.get("name") or "未命名清单", "items": [dict(i) for i in (x.get("items") or [])]}
                     for x in lists]
        return lists

    def _current_list(self):
        return next((x for x in self._lists if x.get("id") == self._selected_list_id), None)

    def _save_lists(self):
        tc = cfg_mod.task_config(self.app.cfg, self.TASK_NAME)
        tc["lists"] = self._lists
        tc["selected_list"] = self._selected_list_id
        tc["items"] = list((self._current_list() or {}).get("items") or [])
        cfg_mod.set_task_config(self.app.cfg, self.TASK_NAME, tc)
        cfg_mod.save_config(self.app.cfg)

    def _select_list(self, name):
        entry = next((x for x in self._lists if x.get("name") == name), None)
        if entry is None or entry.get("id") == self._selected_list_id:
            return
        self._selected_list_id = entry["id"]
        self._save_lists()
        self._sig = None
        self.lbl_list_title.configure(text=f"{entry.get('name', '未命名清单')} · 截图清单")
        self._render_items(entry.get("items") or [])

    def _list_name(self, title, initial=""):
        dlg = ctk.CTkInputDialog(text="清单名称：", title=title)
        value = dlg.get_input()
        value = (value or "").strip()
        return value or None

    def _create_list(self):
        if self._busy():
            self._log_line("运行中不能改清单，请先停止。", "warn")
            return
        name = self._list_name("新建清单")
        if not name:
            return
        existing = {x.get("name") for x in self._lists}
        base, n = name, 2
        while name in existing:
            name = f"{base} {n}"
            n += 1
        import uuid
        entry = {"id": uuid.uuid4().hex, "name": name, "items": []}
        self._lists.append(entry)
        self._selected_list_id = entry["id"]
        self._save_lists()
        self.refresh()

    def _rename_list(self):
        if self._busy():
            self._log_line("运行中不能改清单，请先停止。", "warn")
            return
        entry = self._current_list()
        if not entry:
            return
        name = self._list_name(f"重命名清单（当前：{entry.get('name', '未命名清单')}）")
        if not name:
            return
        existing = {x.get("name") for x in self._lists if x is not entry}
        base, n = name, 2
        while name in existing:
            name = f"{base} {n}"
            n += 1
        entry["name"] = name
        self._save_lists()
        self.refresh()

    def _delete_list(self):
        if self._busy():
            self._log_line("运行中不能改清单，请先停止。", "warn")
            return
        if len(self._lists) <= 1:
            self._log_line("至少保留一份清单。", "warn")
            return
        current_idx = next((i for i, x in enumerate(self._lists)
                            if x.get("id") == self._selected_list_id), None)
        if current_idx is None:
            return
        import copy
        lists = copy.deepcopy(self._lists)
        removed = lists.pop(current_idx)
        selected = lists[min(current_idx, len(lists) - 1)]["id"]
        if self._commit_list_deletion(lists, selected, removed.get("items") or []):
            self.refresh()

    def _commit_list_deletion(self, lists, selected, removed):
        """删除只提交配置草稿；仍被其他清单引用的名称归属继续保留。"""
        import copy
        try:
            draft = copy.deepcopy(self.app.cfg)
            tc = cfg_mod.task_config(draft, self.TASK_NAME)
            entry = next(x for x in lists if x.get("id") == selected)
            tc["lists"] = lists
            tc["selected_list"] = selected
            tc["items"] = copy.deepcopy(entry.get("items") or [])
            retained = {it.get("name") for x in lists for it in (x.get("items") or [])}
            for it in removed:
                if it.get("name") not in retained:
                    tc.get("templates_calib", {}).pop(it.get("name"), None)
            cfg_mod.set_task_config(draft, self.TASK_NAME, tc)
            cfg_mod.save_config(draft)
        except Exception as exc:
            self._log_line(f"删除失败，本次修改未保留：{exc}", "error")
            return False
        self.app.cfg = draft
        self._lists = lists
        self._selected_list_id = selected
        self._sig = None
        return True

    def _set_edit_controls(self, enabled):
        for control in self._edit_controls:
            state = "normal" if enabled else "disabled"
            if enabled and control is self.btn_delete_list and len(self._lists) <= 1:
                state = "disabled"
            try:
                control.configure(state=state)
            except Exception:
                pass

    def _render_items(self, items):
        sig = [(it.get("name"), it.get("template")) for it in items]
        if sig == self._sig:
            return
        self._sig = list(sig)
        self._items = [dict(it) for it in items]
        self._rows = []
        self._thumbs.clear()
        for w in self.list_frame.winfo_children():
            w.destroy()
        self.lbl_count.configure(text=f"{len(self._items)} 张")

        if not self._items:
            empty = ctk.CTkLabel(self.list_frame, text="当前清单暂无截图，请使用上方「＋ 框选加截图」添加。",
                                 font=self.fonts["body"], text_color=T.TEXT_DIM, justify="left",
                                 anchor="w")
            empty.grid(row=0, column=0, sticky="ew", padx=12, pady=20)
            bind_wraplength(empty)
            return

        for i, it in enumerate(self._items):
            self._build_row(i, it)

    def _build_row(self, i, it):
        row = ctk.CTkFrame(self.list_frame, fg_color=T.SURFACE_2, corner_radius=T.RADIUS_SM)
        row.grid(row=i, column=0, sticky="ew", pady=4, padx=4)
        row.grid_columnconfigure(2, weight=1)

        # 左起第一格：拖动把手 + 名次（按住这里上下拖 = 调序）
        # 用「≡」而不是盲文点阵「⠿」：后者在雅黑下字形可疑（看着像坏字），「≡」各字体都有、一眼是拖柄。
        handle = ctk.CTkLabel(row, text=f"≡{i + 1}", font=self.fonts["small"],
                              text_color=T.TEXT_DIM, width=36, cursor="fleur")
        handle.grid(row=0, column=0, padx=(10, 6), pady=8)

        thumb = load_thumb(it.get("template"), self._thumbs)
        thumb_lbl = (ctk.CTkLabel(row, text="", image=thumb) if thumb is not None
                     else ctk.CTkLabel(row, text="🖼", font=self.fonts["h2"]))
        thumb_lbl.grid(row=0, column=1, padx=(0, 8), pady=8)

        name_lbl = ctk.CTkLabel(row, text=it.get("name", "?"), font=self.fonts["body_b"],
                                text_color=T.TEXT, justify="left", anchor="w")
        name_lbl.grid(row=0, column=2, sticky="ew", padx=(0, 8))
        bind_wraplength(name_lbl)

        # 按钮一律按【行对象】回调（见类 docstring：按下标闭包调序后会删错行）
        ctk.CTkButton(row, text="重新标定", font=self.fonts["small"], height=30, width=76,
                      corner_radius=T.RADIUS_SM, fg_color="transparent", hover_color=T.BTN_HOVER,
                      text_color=T.TEXT, border_width=1, border_color=T.BORDER,
                      command=lambda r=row: self._recalibrate(r)).grid(row=0, column=3, padx=(0, 8))
        ctk.CTkButton(row, text="删除", font=self.fonts["small"], height=30, width=52,
                      corner_radius=T.RADIUS_SM, fg_color="transparent", hover_color=T.DANGER,
                      text_color=T.TEXT, border_width=1, border_color=T.BORDER,
                      command=lambda r=row: self._delete_item(r)).grid(row=0, column=4, padx=(0, 10))

        # 左半边（把手/缩略图/名字/行底）可按住拖动；按钮不参与，免得点按钮变成拖行
        for w in (row, handle, thumb_lbl, name_lbl):
            w.bind("<ButtonPress-1>", lambda e, r=row: self._drag_start(r, e))
            w.bind("<B1-Motion>", self._drag_motion)
            w.bind("<ButtonRelease-1>", self._drag_end)

        row._fc_handle = handle      # 调序后要即时改名次，挂在行上最省事
        self._rows.append(row)

    # ------------------------------------------------------------------
    # 拖拽调序（左键按住行左侧上下移动）
    # ------------------------------------------------------------------
    def _drag_start(self, row, event):
        if self._busy():
            self._log_line("运行中不能改清单，请先停止。", "warn")
            return
        try:
            idx = self._rows.index(row)
        except ValueError:
            return
        self._drag_idx = idx
        self._drag_from = idx
        self._drag_name = self._items[idx].get("name", "?")
        self._mark_row(row, True)

    def _drag_motion(self, event):
        if self._drag_idx is None:
            return
        target = self._index_at(event.y_root)
        if target is None or target == self._drag_idx:
            return
        row = self._rows.pop(self._drag_idx)
        item = self._items.pop(self._drag_idx)
        self._rows.insert(target, row)
        self._items.insert(target, item)
        self._drag_idx = target
        self._regrid_rows()

    def _drag_end(self, event=None):
        idx, row = self._drag_idx, None
        if idx is not None and 0 <= idx < len(self._rows):
            row = self._rows[idx]
        if row is not None:
            self._mark_row(row, False)
        moved = idx is not None and idx != self._drag_from
        name = self._drag_name
        self._drag_idx = self._drag_from = None
        if moved:
            self._persist_items()
            self._log_line(f"顺序已保存：「{name}」现为第 {idx + 1} 项")

    def _index_at(self, y_root):
        """指针该插到第几名：与各行中线比较，取第一个「中线在指针下方」的行。"""
        n = len(self._rows)
        if n == 0:
            return None
        for i, r in enumerate(self._rows):
            try:
                top, h = r.winfo_rooty(), r.winfo_height()
            except Exception:
                continue
            if h < 5:
                h = self.ROW_H
            if y_root < top + h / 2.0:
                return i
        return n - 1

    def _regrid_rows(self):
        for i, r in enumerate(self._rows):
            r.grid_configure(row=i)
            h = getattr(r, "_fc_handle", None)
            if h is not None:
                h.configure(text=f"≡{i + 1}")
        try:
            self.list_frame.update_idletasks()   # 让 winfo_rooty 立刻反映新次序，落点才准
        except Exception:
            pass

    @staticmethod
    def _mark_row(row, on):
        try:
            row.configure(border_width=2 if on else 0,
                          border_color=T.ACCENT if on else T.SURFACE_2)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 加图 / 重新标定 / 删除
    # ------------------------------------------------------------------
    def _toast(self, msg, color=None):
        """给 grab_roi_on_app 用的提示回调（框选失败原因走日志面板）。"""
        self._log_line(msg, "warn")

    def _capture_shot(self, prompt):
        import copy
        from .calibrate_dialog import grab_roi_on_app
        draft = copy.deepcopy(self.app.cfg)
        try:
            rel, crop, pid = grab_roi_on_app(
                self.app, draft, prompt, with_crop=True, toast=self._toast,
                save_profile=False)
            if rel is None:
                return None
            if crop is None or crop.size == 0:
                self._log_line("截图失败，请重试。", "error")
                return None
            return draft, crop, pid
        except Exception as exc:
            self._log_line(f"标定失败：{exc}", "error")
            return None

    def _commit_shot(self, draft, lists, entry, name, rel_path, crop, pid):
        """清单、当前项镜像和归属一起保存，成功后才发布页面草稿。"""
        from ..core import calibration_assets, vision
        try:
            tc = cfg_mod.task_config(draft, self.TASK_NAME)
            tc["lists"] = lists
            tc["selected_list"] = self._selected_list_id
            tc["items"] = [dict(it) for it in entry["items"]]
            cfg_mod.set_task_config(draft, self.TASK_NAME, tc)
            calib.set_task_profile(draft, self.TASK_NAME, pid)
            calib.set_template_profile(draft, self.TASK_NAME, name, pid)
            encoded = vision.encode_png(crop)
            calibration_assets.commit_image(draft, rel_path, encoded)
        except Exception as exc:
            self._log_line(f"标定失败：{exc}", "error")
            return False
        self.app.cfg = draft
        self._lists = lists
        self._sig = None
        self._render_items(entry["items"])
        return True

    def _add_shot(self):
        if self._busy():
            self._log_line("运行中不能改清单，请先停止。", "warn")
            return
        if self._current_list() is None:
            self._log_line("请先创建清单。", "error")
            return
        captured = self._capture_shot("框选要点击的目标（按钮/图标，框紧一点）")
        if captured is None:
            return
        draft, crop, pid = captured
        name = self._ask_name()
        if not name:
            return
        import copy
        lists = copy.deepcopy(self._lists)
        entry = next(x for x in lists if x.get("id") == self._selected_list_id)
        rel_path = f"templates/fc_{name}.png"
        entry.setdefault("items", []).append({"name": name, "template": rel_path})
        if self._commit_shot(draft, lists, entry, name, rel_path, crop, pid):
            self._log_line(f"已添加截图：{name}（第 {len(entry['items'])} 项）", "hit")

    def _recalibrate(self, row):
        if self._busy():
            self._log_line("运行中不能改清单，请先停止。", "warn")
            return
        try:
            idx = self._rows.index(row)
        except ValueError:
            return
        if self._current_list() is None:
            return
        it = self._items[idx]
        name = it.get("name", "?")
        captured = self._capture_shot(f"重新框选「{name}」")
        if captured is None:
            return
        draft, crop, pid = captured
        import copy
        lists = copy.deepcopy(self._lists)
        entry = next(x for x in lists if x.get("id") == self._selected_list_id)
        entry["items"] = copy.deepcopy(self._items)
        # 保留当前顺序和既有路径；缺路径的旧项仍使用同一回退命名。
        rel_path = it.get("template") or f"templates/fc_{name}.png"
        entry["items"][idx]["template"] = rel_path
        if self._commit_shot(draft, lists, entry, name, rel_path, crop, pid):
            self._log_line(f"已重新标定：{name}（尺寸组 {calib.label_for(pid)}）", "hit")

    def _delete_item(self, row):
        if self._busy():
            self._log_line("运行中不能改清单，请先停止。", "warn")
            return
        try:
            idx = self._rows.index(row)
        except ValueError:
            return
        if self._current_list() is None or not (0 <= idx < len(self._items)):
            return
        import copy
        lists = copy.deepcopy(self._lists)
        entry = next(x for x in lists if x.get("id") == self._selected_list_id)
        entry["items"] = copy.deepcopy(self._items)
        removed = entry["items"].pop(idx)
        if not self._commit_list_deletion(lists, self._selected_list_id, [removed]):
            return
        self._render_items(entry["items"])
        # 删除记录保留模板文件。
        self._log_line(f"已删除：{removed.get('name', '?')}", "info")

    def _ask_name(self):
        dlg = ctk.CTkInputDialog(text="给这个点击目标起个名字（中英文均可）：", title="命名")
        raw = dlg.get_input()
        if raw is None:
            return None
        name = raw.strip().replace("/", "_").replace("\\", "_").replace(" ", "_")
        if not name:
            name = f"click_{len(self._items) + 1}"
        existing = {it.get("name") for it in self._items}
        base, n = name, 2
        while name in existing:
            name = f"{base}_{n}"
            n += 1
        return name

    def _persist_items(self):
        """把内存里的清单（顺序 + 内容）写回 config，并同步内容签名。"""
        entry = self._current_list()
        if entry is None:
            return
        entry["items"] = [dict(it) for it in self._items]
        self._save_lists()
        self._sig = [(it.get("name"), it.get("template")) for it in self._items]

    def _busy(self):
        return bool(self.runner and get_runtime().is_running(self.runner))

    # ------------------------------------------------------------------
    # 运行控制
    # ------------------------------------------------------------------
    def _toggle_run(self):
        if self._busy():
            get_runtime().stop(self.runner)
            self._log_line("正在停止…", "warn")
            self.btn_run.configure(text="停止中…", state="disabled")
            return
        # 启动
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
        self._set_edit_controls(False)
        self.app.on_task_started(self.LOG_SOURCE, self._toggle_run, self.runner)

    def _on_runner_finished(self):
        self.btn_run.configure(text="▶  开始自由点击", fg_color=T.ACCENT,
                               hover_color=T.ACCENT_HOVER, state="normal")
        self._set_edit_controls(True)

    def _toggle_mode(self):
        live = bool(self.switch_mode.get())  # 1=实战
        tc = cfg_mod.task_config(self.app.cfg, self.TASK_NAME)
        tc["dry_run"] = not live
        cfg_mod.set_task_config(self.app.cfg, self.TASK_NAME, tc)
        cfg_mod.save_config(self.app.cfg)
        self._render_mode_pill(not live)
        if live:
            self._log_line("⚠ 实战模式：命中会真的点击", "warn")
        else:
            self._log_line("演练模式（只识别不点击）", "info")

    def _render_mode_pill(self, dry):
        if dry:
            self.pill_mode.configure(text="演练", fg_color=T.PILL_OK_BG, text_color=T.SUCCESS)
        else:
            self.pill_mode.configure(text="实战", fg_color=T.PILL_DANGER_BG, text_color=T.DANGER)

    # ------------------------------------------------------------------
    # 日志与状态（由 App 的定时器驱动）
    # ------------------------------------------------------------------
    def pump(self):
        if self.runner:
            if not get_runtime().is_running(self.runner) and self.btn_run.cget("text") != "▶  开始自由点击":
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
