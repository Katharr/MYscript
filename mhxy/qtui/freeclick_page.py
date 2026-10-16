"""自由点击 Qt model/view 清单；任务和截图事务沿用既有 core 入口。"""

import copy
from uuid import uuid4

from PySide6.QtCore import QMimeData, Qt
from PySide6.QtWidgets import QInputDialog, QLabel, QPushButton
from .widgets import ComboBox as QComboBox

from ..core import calib_profiles as calib, config as cfg_mod, vision
from ..tasks.freeclick import FreeClickTask
from .inventory_items_dialog import ItemsModel, capture_item, items_view
from .inventory_panel import _ConfigTaskPage
from . import theme as T
from .widgets import FlowLayout


class _StepsModel(ItemsModel):
    MIME = "application/x-mhxy-freeclick-step"

    def __init__(self, parent, edit, reorder):
        super().__init__(parent, edit)
        self.reorder = reorder
        self._drag_item = None

    def flags(self, index):
        flags = super().flags(index)
        if self.editable:
            flags |= Qt.ItemFlag.ItemIsDropEnabled
            if index.isValid():
                flags |= Qt.ItemFlag.ItemIsDragEnabled
        return flags

    def supportedDropActions(self):
        return Qt.DropAction.MoveAction

    def moveRows(self, source_parent, source_row, count, destination_parent, destination_child):
        if source_parent.isValid() or destination_parent.isValid() or count != 1:
            return False
        return self.reorder(source_row, destination_child - (destination_child > source_row))

    def mimeTypes(self):
        return [self.MIME]

    def mimeData(self, indexes):
        data = QMimeData()
        if indexes:
            row = indexes[0].row()
            self._drag_item = (row, copy.deepcopy(self.items[row]))
            data.setData(self.MIME, str(id(self)).encode("ascii"))
        return data

    def canDropMimeData(self, data, action, row, column, parent):
        return bool(self.editable and data.hasFormat(self.MIME)
                    and bytes(data.data(self.MIME)) == str(id(self)).encode("ascii")
                    and action == Qt.DropAction.MoveAction)

    def dropMimeData(self, data, action, row, column, parent):
        if not self.canDropMimeData(data, action, row, column, parent) or self._drag_item is None:
            return False
        source, item = self._drag_item
        if source >= len(self.items) or self.items[source] != item:
            return False
        destination = row if row >= 0 else parent.row() if parent.isValid() else len(self.items)
        target = min(len(self.items) - 1, destination - (destination > source))
        return self.reorder(source, target)


class FreeClickPage(_ConfigTaskPage):
    TASK_NAME = "freeclick"
    TITLE = "自由点击"
    LOG_SOURCE = "自由点击"
    START_TEXT = "开始自由点击"

    def __init__(self, *args, **kwargs):
        self._lists = []
        self._selected_list_id = None
        kwargs.setdefault("task_factory", FreeClickTask)
        super().__init__(*args, **kwargs)

    def _build_ui(self):
        super()._build_ui()
        bar = FlowLayout()
        self.list_combo = QComboBox(self)
        self.list_combo.currentIndexChanged.connect(self._select_list)
        self.list_combo.setMinimumWidth(240)
        self.content_layout.addWidget(self.list_combo)
        self._edit_controls.append(self.list_combo)
        for text, callback in (("新建清单", self.create_list), ("重命名清单", self.rename_list),
                               ("删除清单", self.delete_list)):
            button = QPushButton(text, self)
            if text == "删除清单":
                T.set_role(button, "danger")
            button.clicked.connect(callback)
            bar.addWidget(button)
            self._edit_controls.append(button)
        self.content_layout.addLayout(bar)
        tools = FlowLayout()
        for text, callback in (("框选加截图", self.add_shot), ("重新标定", self.recalibrate),
                               ("上移", lambda: self.move_step(-1)), ("下移", lambda: self.move_step(1)),
                               ("删除步骤", self.delete_step), ("刷新配置", self.refresh_config)):
            button = QPushButton(text, self)
            button.clicked.connect(callback)
            tools.addWidget(button)
            self._edit_controls.append(button)
        self.content_layout.addLayout(tools)
        region_tools = FlowLayout()
        for text, callback in (("框选识别区", lambda: self.set_region(False)),
                               ("识别区恢复整窗", lambda: self.set_region(True))):
            button = QPushButton(text, self)
            button.clicked.connect(callback)
            region_tools.addWidget(button)
            self._edit_controls.append(button)
        self.content_layout.addLayout(region_tools)
        self.model = _StepsModel(self, self.edit_step, self.reorder_step)
        self.view = items_view(self, self.model)
        self.view.setDragEnabled(True)
        self.view.setAcceptDrops(True)
        self.view.setDropIndicatorShown(True)
        self.view.setDragDropMode(self.view.DragDropMode.InternalMove)
        self.view.setDragDropOverwriteMode(False)
        self.view.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.content_layout.addWidget(self.view, 1)
        self.view.setToolTip("拖动行或点上移/下移排序，双击名称编辑。")
        self.region_status = QLabel(self)
        self.content_layout.addWidget(self.region_status)

    def refresh_config(self):
        if self._busy():
            return self.cfg
        super().refresh_config()
        tc = cfg_mod.task_config(self.cfg, self.task_name)
        self._lists = copy.deepcopy(tc.get("lists") or [])
        if not self._lists:
            self._lists = [dict(id=uuid4().hex, name="清单 1", items=copy.deepcopy(tc.get("items") or []))]
        for entry in self._lists:
            if not entry.get("id"):
                entry["id"] = uuid4().hex
            entry.setdefault("items", [])
        selected = tc.get("selected_list")
        self._selected_list_id = selected if any(x["id"] == selected for x in self._lists) else self._lists[0]["id"]
        self._render()
        # 旧 items 首次进入时迁移，但失败不发布内存草稿。
        if not tc.get("lists") or tc.get("selected_list") != self._selected_list_id:
            self._save_lists(copy.deepcopy(self._lists), self._selected_list_id)
        return self.cfg

    def _current_list(self, lists=None, selected=None):
        return next((entry for entry in (self._lists if lists is None else lists)
                     if entry["id"] == (self._selected_list_id if selected is None else selected)), None)

    def _render(self):
        self.list_combo.blockSignals(True)
        self.list_combo.clear()
        for entry in self._lists:
            self.list_combo.addItem(entry.get("name", "未命名清单"), entry["id"])
        self.list_combo.setCurrentIndex(self.list_combo.findData(self._selected_list_id))
        self.list_combo.blockSignals(False)
        self.model.replace((self._current_list() or {}).get("items") or [])
        scene = cfg_mod.task_config(self.cfg, self.task_name).get("regions", {}).get("scene")
        self.region_status.setText("识别区：已标定" if scene else "识别区：整窗检测")

    def _save_lists(self, lists, selected, *, draft=None, image=None, profile=None, move=None):
        draft = self._draft() if draft is None else draft
        tc = cfg_mod.task_config(draft, self.task_name)
        entry = self._current_list(lists, selected)
        tc.update(lists=lists, selected_list=selected, items=copy.deepcopy(entry.get("items") or []))
        # 名称归属可能由其它清单共享；只清理已无任何引用的归属。
        retained = {it.get("name") for ls in lists for it in ls.get("items") or []}
        tc["templates_calib"] = {name: pid for name, pid in tc.get("templates_calib", {}).items()
                                 if name in retained}
        cfg_mod.set_task_config(draft, self.task_name, tc)
        if profile is not None:
            name, pid = profile
            calib.set_task_profile(draft, self.task_name, pid)
            calib.set_template_profile(draft, self.task_name, name, pid)
        if not self._commit(draft, image):
            return False
        self._lists, self._selected_list_id = lists, selected
        if move is not None:
            self.model.move(*move)
        self._render()
        return True

    def _select_list(self, index):
        if self._busy() or index < 0:
            return
        selected = self.list_combo.itemData(index)
        if selected != self._selected_list_id:
            if not self._save_lists(copy.deepcopy(self._lists), selected):
                self._render()

    def _ask_name(self, title, existing, initial=""):
        value, ok = QInputDialog.getText(self, title, "名称：", text=initial)
        value = value.strip()
        if not ok:
            return None
        if not value or value in existing:
            self.log_panel.append("名称不能为空或重复。", "warn", self.LOG_SOURCE)
            return None
        return value

    def create_list(self):
        if self._busy():
            return
        name = self._ask_name("新建清单", {ls.get("name") for ls in self._lists})
        if name:
            entry = dict(id=uuid4().hex, name=name, items=[])
            self._save_lists(copy.deepcopy(self._lists) + [entry], entry["id"])

    def rename_list(self):
        if self._busy():
            return
        current = self._current_list()
        if current is None:
            return
        name = self._ask_name("重命名清单", {ls.get("name") for ls in self._lists if ls is not current}, current.get("name", ""))
        if name:
            lists = copy.deepcopy(self._lists)
            self._current_list(lists)["name"] = name
            self._save_lists(lists, self._selected_list_id)

    def delete_list(self):
        if self._busy() or len(self._lists) <= 1:
            return
        lists = copy.deepcopy(self._lists)
        row = next(n for n, ls in enumerate(lists) if ls["id"] == self._selected_list_id)
        lists.pop(row)
        self._save_lists(lists, lists[min(row, len(lists) - 1)]["id"])

    def edit_step(self, row, key, value):
        if self._busy() or key != "name" or not value or not 0 <= row < len(self.model.items):
            return False
        if any(it.get("name") == value for n, it in enumerate(self.model.items) if n != row):
            self.log_panel.append("名称不能重复。", "warn", self.LOG_SOURCE)
            return False
        lists = copy.deepcopy(self._lists)
        entry = self._current_list(lists)
        old = entry["items"][row].get("name")
        entry["items"][row]["name"] = value
        draft = self._draft()
        tc = cfg_mod.task_config(draft, self.task_name)
        if old in tc.get("templates_calib", {}):
            tc["templates_calib"][value] = tc["templates_calib"][old]
        cfg_mod.set_task_config(draft, self.task_name, tc)
        return self._save_lists(lists, self._selected_list_id, draft=draft)

    def move_step(self, delta):
        source = self.view.currentIndex().row()
        target = source + delta
        return self.reorder_step(source, target)

    def reorder_step(self, source, target):
        if self._busy() or not (0 <= source < len(self.model.items) and 0 <= target < len(self.model.items)):
            return False
        if source == target:
            return True
        lists = copy.deepcopy(self._lists)
        items = self._current_list(lists)["items"]
        items.insert(target, items.pop(source))
        if self._save_lists(lists, self._selected_list_id, move=(source, target)):
            self.view.selectRow(target)
            return True
        return False

    def delete_step(self):
        row = self.view.currentIndex().row()
        if self._busy() or not 0 <= row < len(self.model.items):
            return
        lists = copy.deepcopy(self._lists)
        self._current_list(lists)["items"].pop(row)
        self._save_lists(lists, self._selected_list_id)

    def add_shot(self):
        self._capture_shot()

    def recalibrate(self):
        row = self.view.currentIndex().row()
        if 0 <= row < len(self.model.items):
            self._capture_shot(row)

    def _capture_shot(self, row=None):
        if self._busy() or self._current_list() is None:
            return
        self._capturing = True
        self._set_editable(False)
        try:
            draft = self._draft()
            captured = capture_item(self, draft, "框选要点击的目标")
            if captured is None:
                return
            _roi, crop, pid = captured
            lists = copy.deepcopy(self._lists)
            items = self._current_list(lists)["items"]
            if row is None:
                name = self._ask_name("截图命名", {it.get("name") for it in items})
                if not name:
                    return
                item = dict(name=name, template=f"templates/fc_{uuid4().hex}.png")
                items.append(item)
            else:
                item = items[row]
                name = item.get("name", "?")
                item.setdefault("template", f"templates/fc_{uuid4().hex}.png")
                if not item["template"]:
                    item["template"] = f"templates/fc_{uuid4().hex}.png"
            self._save_lists(lists, self._selected_list_id, draft=draft,
                             image=(item["template"], vision.encode_png(crop)), profile=(name, pid))
        except Exception as exc:
            self.log_panel.append(f"标定失败：{exc}", "error", self.LOG_SOURCE)
        finally:
            self._capturing = False
            self._set_editable(True)

    def set_region(self, full_window=False):
        if self._busy():
            return
        self._capturing = True
        self._set_editable(False)
        try:
            draft = self._draft()
            pid = None
            roi = None
            if not full_window:
                captured = capture_item(self, draft, "框选自由点击识别区")
                if captured is None:
                    return
                roi, _crop, pid = captured
            tc = cfg_mod.task_config(draft, self.task_name)
            tc.setdefault("regions", {})["scene"] = roi
            cfg_mod.set_task_config(draft, self.task_name, tc)
            if pid is not None:
                calib.set_task_profile(draft, self.task_name, pid)
            if self._commit(draft):
                self._render()
        except Exception as exc:
            self.log_panel.append(f"标定失败：{exc}", "error", self.LOG_SOURCE)
        finally:
            self._capturing = False
            self._set_editable(True)
