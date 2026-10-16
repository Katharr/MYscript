"""任务标定编辑器；所有操作使用独立草稿，提交成功才更新 UI 和调用方。"""

import copy
from pathlib import Path
import re
import threading

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QColor, QIcon, QImage, QPixmap, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QInputDialog, QLabel, QPushButton,
    QVBoxLayout,
)
from .widgets import ComboBox as QComboBox, TableView as QTableView

from ..core import calib_profiles as calib, calibration_assets, config as cfg_mod, vision
from ..core.activity import ACTIVITY_CALIBRATION
from ..core.teaming import TEAM_CALIBRATION
from ..tasks import all_tasks, get_task
from .roi_overlay import capture_roi
from .sniper_page import _template_signature
from . import theme as T
from .widgets import FlowLayout


VIRTUAL_SPECS = {
    "teaming": ("组队（全局共享）", TEAM_CALIBRATION),
    "activity": ("活动页（全局共享）", ACTIVITY_CALIBRATION),
}


def _load_thumb(path):
    # 先关文件再解码，避免 Windows 缩略图 worker 占用正在重标定的资产。
    try:
        image = QImage.fromData(Path(path).read_bytes())
        return None if image.isNull() else image.scaled(
            200, 64, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    except Exception:
        return None


def task_spec(name):
    if name in VIRTUAL_SPECS:
        return VIRTUAL_SPECS[name]
    task = get_task(name)
    return (getattr(task, "title", name), getattr(task, "CALIBRATION", {}))


def calibration_tasks():
    return [(task.name, task.title) for task in all_tasks()] + [
        (name, title) for name, (title, spec) in VIRTUAL_SPECS.items()]


def resolve_profile(cfg, size, parent=None):
    """只修改调用方草稿；取消替换不删除组，不写盘。"""
    try:
        return calib.get_or_create(cfg, size)
    except ValueError:
        profiles = calib.items(cfg)
        if len(profiles) < calib.MAX_PROFILES:
            raise
    labels = [f"{p['label']} · {calib.size_text(p)}" for p in profiles]
    label, ok = QInputDialog.getItem(parent, "尺寸组已满", "选择要替换的尺寸组（保留模板文件）：",
                                    labels, 0, False)
    if not ok:
        return None
    calib.delete(cfg, profiles[labels.index(label)]["id"])
    return calib.get_or_create(cfg, size)


def capture_item(parent, draft, prompt):
    """供物品和自由点击复用；尺寸变更留在草稿，调用方负责整体提交。"""
    result = capture_roi(parent, draft, prompt)
    if result is None:
        return None
    roi, crop, size = result
    pid = resolve_profile(draft, size, parent)
    return None if pid is None else (roi, crop, pid)


class CalibrationDialog(QDialog):
    config_changed = Signal(object)
    thumbnails_ready = Signal(int, object)

    def __init__(self, parent=None, task_name="sniper", cfg=None, only=None, exclude=None):
        super().__init__(parent)
        self._cfg_supplied = cfg is not None
        self.cfg = copy.deepcopy(cfg) if cfg is not None else cfg_mod.load_config()
        self.task_name = task_name
        self.only, self.exclude = set(only) if only else None, set(exclude or [])
        self._busy = False
        self._generation = 0
        self._thumb_cache = {}
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(900, 640)
        layout = QVBoxLayout(self)
        self.tasks = QComboBox(self)
        for name, title in calibration_tasks():
            self.tasks.addItem(title, name)
        if self.tasks.findData(task_name) == -1:
            self.tasks.addItem(task_name, task_name)
        self.tasks.setCurrentIndex(self.tasks.findData(task_name))
        self.tasks.setEnabled(not (only or exclude))
        self.tasks.currentIndexChanged.connect(self._select_task)
        layout.addWidget(self.tasks)
        self.profile_note = QLabel(self)
        self.profile_note.setWordWrap(True)
        layout.addWidget(self.profile_note)
        self.view = QTableView(self)
        self.model = QStandardItemModel(0, 4, self)
        self.model.setHorizontalHeaderLabels(["标定项", "状态 / 缩略图", "尺寸组", "说明 / 路径"])
        self.view.setModel(self.model)
        self.view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.view.setIconSize(QSize(144, 64))
        self.view.verticalHeader().hide()
        self.view.verticalHeader().setDefaultSectionSize(72)
        self.view.set_column_widths((180, 144, 200))
        self.view.doubleClicked.connect(self.calibrate_selected)
        layout.addWidget(self.view, 1)
        actions = FlowLayout()
        self.capture_button = QPushButton("框选 / 重新标定", self)
        T.set_role(self.capture_button, "primary")
        self.capture_button.clicked.connect(self.calibrate_selected)
        self.clear_button = QPushButton("设为整窗检测", self)
        self.clear_button.clicked.connect(self.use_full_window)
        self.add_button = QPushButton("添加装备", self)
        self.add_button.clicked.connect(self.add_item)
        self.delete_button = QPushButton("删除装备", self)
        T.set_role(self.delete_button, "danger")
        self.delete_button.clicked.connect(self.delete_item)
        self.items_button = QPushButton("管理物品", self)
        self.items_button.clicked.connect(self.open_items)
        for button in (self.capture_button, self.clear_button, self.add_button, self.delete_button, self.items_button):
            actions.addWidget(button)
        layout.addLayout(actions)
        self.status = QLabel("", self)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.thumbnails_ready.connect(self._apply_thumbnails, Qt.ConnectionType.QueuedConnection)
        self.refresh()
        T.events().changed.connect(self._refresh_colors)

    def _refresh_colors(self):
        active = calib.active_id(self.cfg)
        tc = cfg_mod.task_config(self.cfg, self.task_name)
        for row, (group, key, _full) in enumerate(self.rows):
            if group == "watchlist":
                items = tc.get("watchlist") or []
                name = items[key].get("name") if key < len(items) else None
                pid = calib.template_profile(self.cfg, self.task_name, name)
            else:
                pid = (calib.task_profile(self.cfg, self.task_name) if group == "regions"
                       else calib.template_profile(self.cfg, self.task_name, key))
            self.model.item(row, 2).setForeground(QColor(T.SUCCESS if pid == active and calib.get(self.cfg, pid)
                                                       else T.TEXT_DIM))

    def _select_task(self):
        self.task_name = self.tasks.currentData()
        self.refresh()

    def _draft(self):
        return copy.deepcopy(self.cfg) if self._cfg_supplied else cfg_mod.load_config()

    def refresh(self):
        self._generation += 1
        title, spec = task_spec(self.task_name)
        self.setWindowTitle("标定 · " + title)
        self.model.removeRows(0, self.model.rowCount())
        tc = cfg_mod.task_config(self.cfg, self.task_name)
        active = calib.active_id(self.cfg)
        self.profile_note.setText("当前尺寸组：" + calib.size_text(calib.active(self.cfg)))
        self.rows, requests = [], {}
        self._requests = requests
        for group in ("regions", "templates"):
            for item in spec.get(group, []):
                key, name, hint = item[:3]
                if (self.only is not None and key not in self.only) or key in self.exclude:
                    continue
                full = group == "regions" and len(item) > 3 and bool(item[3])
                value = tc.get(group, {}).get(key)
                pid = (calib.task_profile(self.cfg, self.task_name) if group == "regions"
                       else calib.template_profile(self.cfg, self.task_name, key))
                self._append_row(group, key, name, hint, value, pid, active, full, requests)
        equipment = self.task_name == "sniper" or spec.get("watchlist", False)
        if equipment and self.only is None:
            for index, item in enumerate(tc.get("watchlist") or []):
                self._append_row("watchlist", index, item.get("name", "装备"), "图标和名字，不含价格",
                                 item.get("template"), calib.template_profile(self.cfg, self.task_name, item.get("name")),
                                 active, False, requests)
        self.add_button.setVisible(equipment and self.only is None)
        self.delete_button.setVisible(equipment and self.only is None)
        self.items_button.setVisible(self.task_name == "organize_bag")
        if self.model.rowCount():
            self.view.selectRow(0)
        self._thumb_cache = {sig: image for sig, image in self._thumb_cache.items() if sig in requests}
        pending = [sig for sig in requests if sig not in self._thumb_cache]
        for sig, rows in requests.items():
            if sig in self._thumb_cache:
                self._set_thumb(rows, self._thumb_cache[sig])
        if pending:
            signal, generation = self.thumbnails_ready, self._generation

            def work():
                results = [(sig, _load_thumb(sig[0])) for sig in pending]
                try:
                    signal.emit(generation, results)
                except RuntimeError:
                    pass

            threading.Thread(target=work, name="mhxy-calibration-thumbnails", daemon=True).start()

    def _append_row(self, group, key, name, hint, value, pid, active, full, requests):
        row = len(self.rows)
        self.rows.append((group, key, full))
        status = (str(value) if value else "整窗检测" if full else "未标定") if group == "regions" else "未标定"
        prof = calib.get(self.cfg, pid)
        badge = QStandardItem(("当前 · " if pid == active and prof else "")
                              + (f"{prof['label']} {calib.size_text(prof)}" if prof else "未记录"))
        badge.setForeground(QColor(T.SUCCESS if pid == active and prof else T.TEXT_DIM))
        self.model.appendRow([QStandardItem(name), QStandardItem(status), badge,
                              QStandardItem(hint + ("\n" + str(value) if value else ""))])
        for col in range(4):
            item = self.model.item(row, col)
            item.setToolTip(item.text())
        if group != "regions":
            sig = _template_signature(value)
            if sig and sig[1] is not None:
                requests.setdefault(sig, []).append(row)
        self._requests = requests

    def _set_thumb(self, rows, image):
        for row in rows:
            item = self.model.item(row, 1)
            item.setText("已标定" if image is not None else "未标定")
            item.setIcon(QIcon(QPixmap.fromImage(image)) if image is not None else QIcon())

    def _apply_thumbnails(self, generation, results):
        if generation != self._generation:
            return
        for signature, image in results:
            if signature in self._requests:
                self._thumb_cache[signature] = image
                self._set_thumb(self._requests[signature], image)

    def _selected(self):
        row = self.view.currentIndex().row()
        return self.rows[row] if 0 <= row < len(self.rows) else None

    def _grab_roi(self, draft, prompt):
        result = capture_roi(self, draft, prompt, allow_desktop_windows=self.task_name == "launch_login")
        if result is None:
            return None
        roi, crop, size = result
        pid = None if self.task_name == "launch_login" else resolve_profile(draft, size, self)
        if pid is None and self.task_name != "launch_login":
            return None
        return roi, crop, pid

    def _commit(self, draft, path=None, crop=None, leader=False):
        try:
            if path is None:
                cfg_mod.save_config(draft)
            else:
                if crop is None or crop.size == 0:
                    raise ValueError("截图为空，请重新框选。")
                encoded = vision.encode_png(crop)
                if leader:
                    from ..core import leader_history

                    published = copy.deepcopy(self.cfg)
                    leader_history.commit_calibration(published, draft, encoded)
                    draft = published
                else:
                    calibration_assets.commit_image(draft, path, encoded)
        except Exception as exc:
            self.status.setText(f"保存失败：{exc}")
            return False
        self.cfg = draft
        self.refresh()
        self.status.setText("已保存")
        self.config_changed.emit(draft)
        return True

    def calibrate_selected(self, *args):
        selected = self._selected()
        if self._busy or selected is None:
            return
        self._busy = True
        try:
            group, key, full = selected
            draft = self._draft()
            captured = self._grab_roi(draft, "框选标定项")
            if captured is None:
                return
            roi, crop, pid = captured
            tc = cfg_mod.task_config(draft, self.task_name)
            path = None
            if group == "regions":
                tc.setdefault(group, {})[key] = roi
            elif group == "templates":
                path = tc.get(group, {}).get(key) or f"templates/tm_{key}.png"
                if self.task_name == "teaming" and key == "leader_id":
                    from ..core.leader_history import ACTIVE_REL

                    path = ACTIVE_REL
                tc.setdefault(group, {})[key] = path
            else:
                item = tc["watchlist"][key]
                key = item["name"]
                path = item.get("template") or f"templates/{key}.png"
                item["template"] = path
            cfg_mod.set_task_config(draft, self.task_name, tc)
            if pid is not None:
                calib.set_task_profile(draft, self.task_name, pid)
                if path:
                    calib.set_template_profile(draft, self.task_name, key, pid)
            self._commit(draft, path, crop, self.task_name == "teaming" and key == "leader_id")
        except Exception as exc:
            self.status.setText(f"标定失败：{exc}")
        finally:
            self._busy = False

    def use_full_window(self):
        selected = self._selected()
        if self._busy or selected is None or not selected[2]:
            self.status.setText("只有支持整窗检测的区域可留空。")
            return
        try:
            draft = self._draft()
            tc = cfg_mod.task_config(draft, self.task_name)
            tc.setdefault("regions", {})[selected[1]] = None
            cfg_mod.set_task_config(draft, self.task_name, tc)
            self._commit(draft)
        except Exception as exc:
            self.status.setText(f"保存失败：{exc}")

    def add_item(self):
        if self._busy:
            return
        self._busy = True
        try:
            draft = self._draft()
            captured = self._grab_roi(draft, "框选装备图标和名字，不含价格")
            if captured is None:
                return
            roi, crop, pid = captured
            raw, ok = QInputDialog.getText(self, "添加装备", "装备名称（支持中文）：")
            if not ok:
                return
            name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", raw).strip().rstrip(".")
            if not name or name in (".", ".."):
                raise ValueError("装备名称不能为空。")
            tc = cfg_mod.task_config(draft, self.task_name)
            if any(it.get("name") == name for it in tc.get("watchlist", [])):
                raise ValueError("装备名称已存在。")
            path = f"templates/{name}.png"
            tc.setdefault("watchlist", []).append(dict(name=name, template=path, max_price=None))
            cfg_mod.set_task_config(draft, self.task_name, tc)
            if pid is not None:
                calib.set_task_profile(draft, self.task_name, pid)
                calib.set_template_profile(draft, self.task_name, name, pid)
            self._commit(draft, path, crop)
        except Exception as exc:
            self.status.setText(f"添加装备失败：{exc}")
        finally:
            self._busy = False

    def delete_item(self):
        selected = self._selected()
        if self._busy or selected is None or selected[0] != "watchlist":
            return
        try:
            draft = self._draft()
            tc = cfg_mod.task_config(draft, self.task_name)
            removed = tc["watchlist"].pop(selected[1])
            name = removed.get("name")
            if name not in tc.get("templates", {}) and not any(it.get("name") == name for it in tc["watchlist"]):
                tc.get("templates_calib", {}).pop(name, None)
            cfg_mod.set_task_config(draft, self.task_name, tc)
            self._commit(draft)
        except Exception as exc:
            self.status.setText(f"删除装备失败：{exc}")

    def open_items(self):
        from .inventory_items_dialog import InventoryItemsDialog

        if self._busy:
            return
        self._busy = True
        dialog = None
        try:
            dialog = InventoryItemsDialog(self, self._draft())
            dialog.config_changed.connect(self._items_saved)
            dialog.exec()
        except Exception as exc:
            self.status.setText(f"打开物品清单失败：{exc}")
        finally:
            if dialog is not None:
                dialog.deleteLater()
            self._busy = False

    def _items_saved(self, cfg):
        self.cfg = cfg
        self.refresh()
        self.config_changed.emit(cfg)

    def done(self, result):
        if not self._busy:
            self._generation += 1
            super().done(result)

    def reject(self):
        if not self._busy:
            super().reject()

    def closeEvent(self, event):
        if self._busy:
            event.ignore()
        else:
            super().closeEvent(event)


def open_calibration(page, task_name=None):
    """现有任务页的统一入口；阻止在运行或另一个编辑器打开时标定。"""
    if page._start_pending or page._runtime_running() or getattr(page, "_editor", None) is not None:
        return
    if getattr(page.window(), "_calibration_running", lambda: False)():
        return
    existing = getattr(page, "_calibration_dialog", None)
    if existing is not None:
        existing.raise_()
        existing.activateWindow()
        return
    dialog = CalibrationDialog(page, task_name or page.task_name,
                               page.cfg if page._cfg_supplied else None)
    page._calibration_dialog = dialog

    def saved(cfg):
        page.cfg = cfg
        page.config_changed.emit(cfg)
        page.refresh_config()

    def finished():
        page._calibration_dialog = None
        page.refresh_config()
        dialog.deleteLater()

    dialog.config_changed.connect(saved)
    dialog.finished.connect(finished)
    dialog.show()
