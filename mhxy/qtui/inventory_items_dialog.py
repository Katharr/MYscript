"""Qt 物品清单及小型截图控件；资产只通过 core 的事务入口保存。"""

import copy
from pathlib import Path
import threading
from uuid import uuid4

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView,
    QInputDialog, QLabel, QPushButton, QStyledItemDelegate, QTableView,
    QAbstractItemView, QVBoxLayout, QWidget,
)

from ..core import calib_profiles as calib, calibration_assets, config as cfg_mod, vision
from ..core.inventory import ACTION_LABELS, ACTION_ORDER
from .sniper_page import _template_signature
from . import theme as T


def _load_item_thumb(path):
    """先关闭文件再解码，Windows 下缩略图读取不会长期占用待重标定文件。"""
    try:
        image = QImage.fromData(Path(path).read_bytes())
        if image.isNull():
            return None
        return image.scaled(240, 48, Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation)
    except Exception:
        return None


class _CropCanvas(QWidget):
    """只在冻结图片上框选；逻辑坐标按显示比例换回截图物理像素。"""

    def __init__(self, pixels, parent):
        super().__init__(parent)
        h, w = pixels.shape[:2]
        self.image = QImage(pixels.tobytes(), w, h, w * 3,
                            QImage.Format.Format_BGR888).copy()
        self.selection = QRect()
        self.origin = None
        self.setMinimumSize(320, 240)
        self.setCursor(Qt.CursorShape.CrossCursor)

    def image_rect(self):
        size = self.image.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
        return QRect((self.width() - size.width()) // 2,
                     (self.height() - size.height()) // 2, size.width(), size.height())

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#15191f"))
        painter.drawImage(self.image_rect(), self.image)
        painter.setPen(QPen(QColor("#45d6a2"), 2))
        painter.drawRect(self.selection)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.image_rect().contains(event.position().toPoint()):
            self.origin = event.position().toPoint()
            self.selection = QRect()
            self.update()

    def mouseMoveEvent(self, event):
        if self.origin is not None:
            self.selection = QRect(self.origin, event.position().toPoint()).normalized().intersected(self.image_rect())
            self.update()

    def mouseReleaseEvent(self, event):
        self.mouseMoveEvent(event)
        self.origin = None

    def crop_rect(self):
        box, selected = self.image_rect(), self.selection
        if selected.isEmpty():
            return None
        x = int((selected.x() - box.x()) * self.image.width() / box.width())
        y = int((selected.y() - box.y()) * self.image.height() / box.height())
        w = int(selected.width() * self.image.width() / box.width())
        h = int(selected.height() * self.image.height() / box.height())
        return [x, y, w, h] if w >= 6 and h >= 6 else None


def capture_item(parent, draft, prompt):
    """不写配置、不激活游戏；隐藏助手后截取选中游戏的可见画面。"""
    from ..core import window as win_mod

    win_mod.set_game_process(draft.get("window_process") or win_mod.DEFAULT_GAME_PROCESS_SPEC)
    title, offset = draft.get("window_title", "梦幻西游"), draft.get("window_offset", [0, 0])
    wins = win_mod.locate_all(title, offset)
    if not wins:
        raise ValueError("没找到游戏窗口，请先打开游戏。")
    labels = [f"窗口 {i + 1}" for i in range(len(wins))]
    label, ok = QInputDialog.getItem(parent, "截图窗口", "请先让目标游戏窗口完整可见：", labels, 0, False)
    if not ok:
        return None
    win = wins[labels.index(label)]
    hidden = [w for w in QApplication.topLevelWidgets() if w.isVisible()]
    result = {}
    dialog = QDialog(parent)
    dialog.setWindowTitle(prompt)
    dialog.resize(1000, 720)
    layout = QVBoxLayout(dialog)
    layout.addWidget(QLabel("在冻结的游戏画面上拖动框选；Esc 取消。", dialog))
    try:
        for widget in hidden:
            widget.hide()

        def grab():
            try:
                rect = win.rect()
                pixels = win_mod.grab(rect) if rect else None
                if pixels is None or pixels.size == 0:
                    raise ValueError("截图失败，请重试。")
                canvas = _CropCanvas(pixels, dialog)
                layout.insertWidget(1, canvas, 1)
                result.update(rect=rect, pixels=pixels, canvas=canvas)
                dialog.setWindowOpacity(1)
            except Exception as exc:
                result["error"] = exc
                dialog.reject()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, dialog)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        # exec 提供事件循环，让隐藏助手的窗口先完成重绘；截图不含助手。
        timer = QTimer(dialog)
        timer.setSingleShot(True)
        timer.timeout.connect(grab)
        timer.start(150)
        dialog.setWindowOpacity(0)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        if "error" in result:
            raise result["error"]
        if not accepted:
            return None
        roi = result["canvas"].crop_rect()
        if roi is None:
            raise ValueError("请框选至少 6×6 像素的目标。")
        x, y, w, h = roi
        rect = result["rect"]
        ref = win_mod.window_at_point(title, offset, rect[0] + x + w // 2, rect[1] + y + h // 2)
        if ref is None or ref.rect() != rect:
            raise ValueError("框选处不是目标游戏窗口，或窗口已移动，请重试。")
        try:
            pid = calib.get_or_create(draft, rect[2:4])
        except ValueError:
            profiles = calib.items(draft)
            choices = [str(p["id"]) for p in profiles]
            selected, ok = QInputDialog.getItem(dialog, "尺寸组已满", "替换尺寸组（保留模板文件）：", choices, 0, False)
            if not ok:
                return None
            calib.delete(draft, int(selected))
            pid = calib.get_or_create(draft, rect[2:4])
        return roi, result["pixels"][y:y + h, x:x + w].copy(), pid
    finally:
        if 'timer' in locals():
            timer.stop()
        dialog.close()
        dialog.deleteLater()
        for widget in hidden:
            widget.show()
        parent.window().raise_()


class ItemsModel(QAbstractTableModel):
    """行数据独立副本；持久化成功后才发送对应行的 Qt model 通知。"""

    thumbnails_ready = Signal(object)

    def __init__(self, parent, edit, actions=False):
        super().__init__(parent)
        self.items = []
        self.edit = edit
        self.actions = actions
        self.editable = True
        self.cache = {}
        self.pending = set()
        self.thumbnails_ready.connect(self._apply_thumbnails, Qt.ConnectionType.QueuedConnection)

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.items)

    def columnCount(self, parent=QModelIndex()):
        return 4 if self.actions else 3

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return ("名称", "缩略图", "模板路径", "动作")[section]
        return super().headerData(section, orientation, role)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self.items):
            return None
        item, col = self.items[index.row()], index.column()
        if role == Qt.ItemDataRole.DecorationRole and col == 1:
            return self.cache.get(_template_signature(item.get("template")))
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            if col == 0:
                return item.get("name", "")
            if col == 1:
                return "" if self.cache.get(_template_signature(item.get("template"))) else "未标定"
            if col == 2:
                return item.get("template", "")
            action = item.get("action", "use")
            return action if role == Qt.ItemDataRole.EditRole else ACTION_LABELS.get(action, action)
        if role == Qt.ItemDataRole.ToolTipRole:
            return str(item.get("template") or "图片缺失，可重新截图")

    def flags(self, index):
        flags = super().flags(index)
        if self.editable and index.column() in (0, 3):
            flags |= Qt.ItemFlag.ItemIsEditable
        return flags

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if role != Qt.ItemDataRole.EditRole or not self.editable or not index.isValid():
            return False
        key = {0: "name", 3: "action"}.get(index.column())
        return bool(key and self.edit(index.row(), key, str(value).strip()))

    def replace(self, items):
        items = copy.deepcopy(items)
        # 常见新增/删除/编辑只发局部通知；切换不同清单才 reset。
        if items == self.items:
            self.refresh_thumbnails()
            return
        if len(items) == len(self.items):
            for row, item in enumerate(items):
                if item != self.items[row]:
                    self.items[row] = item
                    self.dataChanged.emit(self.index(row, 0), self.index(row, self.columnCount() - 1))
        elif len(items) == len(self.items) + 1 and items[:-1] == self.items:
            row = len(self.items)
            self.beginInsertRows(QModelIndex(), row, row)
            self.items.append(items[-1])
            self.endInsertRows()
        elif len(items) + 1 == len(self.items):
            row = next((n for n in range(len(self.items)) if self.items[:n] + self.items[n + 1:] == items), None)
            if row is not None:
                self.beginRemoveRows(QModelIndex(), row, row)
                self.items.pop(row)
                self.endRemoveRows()
            else:
                self._reset(items)
        else:
            self._reset(items)
        self.refresh_thumbnails()

    def _reset(self, items):
        self.beginResetModel()
        self.items = items
        self.endResetModel()

    def move(self, source, target):
        self.beginMoveRows(QModelIndex(), source, source, QModelIndex(), target + (target > source))
        self.items.insert(target, self.items.pop(source))
        self.endMoveRows()

    def refresh_thumbnails(self):
        signatures = {_template_signature(it.get("template")) for it in self.items}
        self.cache = {sig: icon for sig, icon in self.cache.items() if sig in signatures}
        pending = {sig for sig in signatures if sig and sig[1] is not None
                   and sig not in self.cache and sig not in self.pending}
        if not pending:
            return
        self.pending.update(pending)
        signal = self.thumbnails_ready

        def work():
            results = [(sig, _load_item_thumb(sig[0])) for sig in pending]
            try:
                signal.emit(results)
            except RuntimeError:
                pass

        threading.Thread(target=work, name="mhxy-item-thumbnails", daemon=True).start()

    def _apply_thumbnails(self, results):
        current = {_template_signature(it.get("template")) for it in self.items}
        for signature, image in results:
            self.pending.discard(signature)
            if signature in current:
                self.cache[signature] = QIcon(QPixmap.fromImage(image)) if image is not None else None
                for row, item in enumerate(self.items):
                    if _template_signature(item.get("template")) == signature:
                        self.dataChanged.emit(self.index(row, 1), self.index(row, 1))


class _ActionDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        editor = QComboBox(parent)
        for action in ACTION_ORDER:
            editor.addItem(ACTION_LABELS[action], action)
        if index.data(Qt.ItemDataRole.EditRole) not in ACTION_ORDER:
            action = index.data(Qt.ItemDataRole.EditRole)
            editor.addItem(ACTION_LABELS.get(action, action), action)
        return editor

    def setEditorData(self, editor, index):
        editor.setCurrentIndex(editor.findData(index.data(Qt.ItemDataRole.EditRole)))

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentData())


def items_view(parent, model):
    from PySide6.QtCore import QSize

    view = QTableView(parent)
    view.setModel(model)
    view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    view.setIconSize(QSize(180, 48))
    view.verticalHeader().setDefaultSectionSize(56)
    view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    view.horizontalHeader().setStretchLastSection(True)
    if model.actions:
        view.setItemDelegateForColumn(3, _ActionDelegate(view))
    return view


class InventoryItemsDialog(QDialog):
    config_changed = Signal(object)

    def __init__(self, parent=None, cfg=None):
        super().__init__(parent)
        self.cfg = copy.deepcopy(cfg) if cfg is not None else cfg_mod.load_config()
        self.setWindowTitle("整理背包 · 物品清单")
        self.resize(800, 560)
        self._adding = False
        self.setStyleSheet(f"QDialog {{ background: {T.SURFACE}; }} "
                          f"QTableView, QComboBox {{ background: {T.BG}; color: {T.TEXT}; }} "
                          f"QHeaderView::section {{ background: {T.SURFACE}; color: {T.TEXT_DIM}; }}")
        layout = QVBoxLayout(self)
        tools = QHBoxLayout()
        for text, callback in (("添加一个", lambda: self.add_items(False)),
                               ("批量添加", lambda: self.add_items(True)),
                               ("重新截图", self.recalibrate), ("删除", self.delete_item)):
            button = QPushButton(text, self)
            button.clicked.connect(callback)
            tools.addWidget(button)
        layout.addLayout(tools)
        layout.addWidget(QLabel("双击名称或动作即可编辑；每项修改保存后立即生效。", self))
        self.model = ItemsModel(self, self.edit_item, actions=True)
        self.view = items_view(self, self.model)
        layout.addWidget(self.view)
        self.status = QLabel(self)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)
        self.model.replace(cfg_mod.task_config(self.cfg, "organize_bag").get("items") or [])

    def _commit(self, draft, image=None):
        try:
            if image:
                calibration_assets.commit_image(draft, *image)
            else:
                cfg_mod.save_config(draft)
        except Exception as exc:
            self.status.setText(f"保存失败，本次修改未保留：{exc}")
            return False
        self.cfg = draft
        self.model.replace(cfg_mod.task_config(draft, "organize_bag").get("items") or [])
        self.status.setText("已保存")
        self.config_changed.emit(draft)
        return True

    def _draft(self):
        # 对话框期间由所属任务页锁住配置编辑，草稿独立于已发布对象。
        return copy.deepcopy(self.cfg)

    def edit_item(self, row, key, value):
        if self._adding or not 0 <= row < len(self.model.items):
            return False
        if key == "action" and value not in ACTION_LABELS:
            return False
        if key == "name" and (not value or any(it.get("name") == value for n, it in enumerate(self.model.items) if n != row)):
            self.status.setText("名称不能为空或重复。")
            return False
        draft = self._draft()
        tc = cfg_mod.task_config(draft, "organize_bag")
        old = tc["items"][row].get("name")
        tc["items"][row][key] = value
        if key == "name" and old in tc.get("templates_calib", {}):
            tc["templates_calib"][value] = tc["templates_calib"].pop(old)
        cfg_mod.set_task_config(draft, "organize_bag", tc)
        return self._commit(draft)

    def delete_item(self):
        row = self.view.currentIndex().row()
        if self._adding or not 0 <= row < len(self.model.items):
            return
        draft = self._draft()
        tc = cfg_mod.task_config(draft, "organize_bag")
        removed = tc["items"].pop(row)
        tc.get("templates_calib", {}).pop(removed.get("name"), None)
        cfg_mod.set_task_config(draft, "organize_bag", tc)
        self._commit(draft)  # 删除仅移除配置归属，保留图片。

    def add_items(self, batch=False):
        if self._adding:
            return
        self._adding = True
        try:
            while True:
                draft = self._draft()
                captured = capture_item(self, draft, "框选物品图标和名字")
                if captured is None:
                    break
                _roi, crop, pid = captured
                labels = [ACTION_LABELS[a] for a in ACTION_ORDER]
                label, ok = QInputDialog.getItem(self, "物品动作", "整理方式：", labels, 0, False)
                if not ok:
                    break
                tc = cfg_mod.task_config(draft, "organize_bag")
                used = {it.get("name") for it in tc.get("items") or []}
                n = 1
                while f"物品 {n:03d}" in used:
                    n += 1
                name, path = f"物品 {n:03d}", f"templates/ob_{uuid4().hex}.png"
                tc.setdefault("items", []).append(dict(name=name, template=path, action=ACTION_ORDER[labels.index(label)]))
                cfg_mod.set_task_config(draft, "organize_bag", tc)
                calib.set_task_profile(draft, "organize_bag", pid)
                calib.set_template_profile(draft, "organize_bag", name, pid)
                if not self._commit(draft, (path, vision.encode_png(crop))) or not batch:
                    break
        except Exception as exc:
            self.status.setText(f"添加物品失败：{exc}")
        finally:
            self._adding = False

    def recalibrate(self):
        row = self.view.currentIndex().row()
        if self._adding or not 0 <= row < len(self.model.items):
            return
        self._adding = True
        try:
            draft = self._draft()
            captured = capture_item(self, draft, "重新框选物品")
            if captured is None:
                return
            _roi, crop, pid = captured
            tc = cfg_mod.task_config(draft, "organize_bag")
            item = tc["items"][row]
            path = item.get("template") or f"templates/ob_{uuid4().hex}.png"
            item["template"] = path
            cfg_mod.set_task_config(draft, "organize_bag", tc)
            calib.set_task_profile(draft, "organize_bag", pid)
            calib.set_template_profile(draft, "organize_bag", item["name"], pid)
            self._commit(draft, (path, vision.encode_png(crop)))
        except Exception as exc:
            self.status.setText(f"重新截图失败：{exc}")
        finally:
            self._adding = False

    def reject(self):
        if not self._adding:
            super().reject()

    def closeEvent(self, event):
        if self._adding:
            event.ignore()
        else:
            super().closeEvent(event)
