"""可换行工具栏、表单规范与有界的列表尺寸；只负责展示。"""

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QPainter, QPainterPath, QPalette, QPen
from PySide6.QtWidgets import (
    QAbstractSpinBox, QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QHeaderView,
    QLabel, QLayout, QLineEdit, QSizePolicy, QSpinBox, QTableView, QToolButton, QWidget,
)

from . import theme as T


FIELD_WIDTHS = {
    "number": (128, 144), "stepper": (208, 208), "short": (176, 208),
    "name": (220, 360), "path": (240, 640), "hotkey": (88, 104),
}


def set_field_width(widget, kind):
    minimum, maximum = FIELD_WIDTHS[kind]
    widget.setMinimumWidth(minimum)
    widget.setMaximumWidth(maximum)
    widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    widget.setProperty("fieldWidth", kind)
    return widget


def number_with_unit(edit, unit):
    """值仍由原 QLineEdit 保存，单位贴近数值且不参与编辑。"""
    set_field_width(edit, "number")
    if not unit:
        return edit
    field = QWidget()
    row = QHBoxLayout(field)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(8)
    row.addWidget(edit)
    row.addWidget(QLabel(unit, field))
    field.setMaximumWidth(208)
    field.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
    return field


class FlowLayout(QLayout):
    """宽度不足时整项换行，不挤压按钮的最小尺寸。"""

    def __init__(self, parent=None, spacing=8):
        super().__init__(parent)
        self._items = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(spacing)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._arrange(QRect(0, 0, width, 0), False)

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            if not item.isEmpty():
                size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def sizeHint(self):
        return self.minimumSize()

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._arrange(rect, True)

    def _arrange(self, rect, apply):
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x, y, height = area.x(), area.y(), 0
        for item in self._items:
            if item.isEmpty():
                continue
            size = item.sizeHint().expandedTo(item.minimumSize())
            if x > area.x() and x + size.width() > area.right() + 1:
                x, y, height = area.x(), y + height + self.spacing(), 0
            if apply:
                item.setGeometry(QRect(x, y, size.width(), size.height()))
            x += size.width() + self.spacing()
            height = max(height, size.height())
        return y + height - rect.y() + margins.bottom()


def configure_form(form):
    form.setContentsMargins(0, 0, 0, 0)
    form.setHorizontalSpacing(16)
    form.setVerticalSpacing(12)
    form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    form.setFormAlignment(Qt.AlignmentFlag.AlignTop)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)


def set_window_summary(label, labels, error=""):
    label.setText("窗口检测失败：" + error if error else
                  f"已连接 {len(labels)} 个窗口" if labels else "未连接目标窗口")
    label.setToolTip(error or "\n".join(labels))
    label.setWordWrap(True)


class FormLayout(QFormLayout):
    def __init__(self, parent=None, *, label_width=None):
        super().__init__(parent)
        self._label_width = label_width
        configure_form(self)

    def addRow(self, *args):
        if len(args) == 2 and isinstance(args[0], str):
            label = QLabel(args[0])
            label.setWordWrap(True)
            label.setMinimumWidth(self._label_width or min(190, label.fontMetrics().horizontalAdvance(args[0]) + 2))
            label.setMaximumWidth(self._label_width or 190)
            if hasattr(args[1], "setFocus"):
                label.setBuddy(args[1])
            if hasattr(args[1], "increase_button"):
                args[1].increase_button.setAccessibleName("增加" + args[0])
                args[1].decrease_button.setAccessibleName("减少" + args[0])
            if isinstance(args[1], QLineEdit) and not args[1].property("fieldWidth"):
                set_field_width(args[1], "path")
            args = (label, args[1])
        super().addRow(*args)


class ComboBox(QComboBox):
    """闭合时有界增长，展开时按最长选项显示，且不超出所在屏幕。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumContentsLength(12)
        set_field_width(self, "name")
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)

    def wheelEvent(self, event):
        # 包括已获得焦点的闭合下拉框；忽略后交给上层滚动区域。
        event.ignore()

    def showPopup(self):
        text_width = max((self.fontMetrics().horizontalAdvance(self.itemText(i))
                          for i in range(self.count())), default=0)
        available = self.screen().availableGeometry().width() - 24
        self.view().setMinimumWidth(min(max(self.width(), text_width + 56), available))
        super().showPopup()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        group = QPalette.ColorGroup.Active if self.isEnabled() else QPalette.ColorGroup.Disabled
        painter.setPen(QPen(self.palette().color(group, QPalette.ColorRole.Text), 1.8,
                            Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        x, y = self.width() - 16, self.height() / 2
        path = QPainterPath()
        path.moveTo(x - 4, y - 2)
        path.lineTo(x, y + 2)
        path.lineTo(x + 4, y - 2)
        painter.drawPath(path)


class _HorizontalStepper:
    """保留原生数值/校验/键盘接口，用左右按钮替代狭窄的上下箭头。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.setProperty("horizontalStepper", True)
        set_field_width(self, "stepper")
        self.decrease_button = QToolButton(self)
        self.increase_button = QToolButton(self)
        for button, text, label, direction in (
            (self.decrease_button, "−", "减少", -1),
            (self.increase_button, "+", "增加", 1),
        ):
            button.setText(text)
            button.setAccessibleName(label)
            button.setToolTip(label)
            button.setProperty("role", "stepButton")
            button.setAutoRepeat(True)
            button.setAutoRepeatDelay(400)
            button.setAutoRepeatInterval(100)
            button.clicked.connect(lambda checked=False, step=direction: self._step_from_button(step))
        self.valueChanged.connect(self._update_step_buttons)
        self._update_step_buttons()

    def _step_from_button(self, direction):
        previous = self.value()
        self.stepBy(direction)
        if self.value() != previous:
            # 任务页沿用 editingFinished 保存，不等待用户再点击其它控件。
            self.editingFinished.emit()

    def _update_step_buttons(self, *_):
        flags = self.stepEnabled()
        enabled = self.isEnabled() and not self.isReadOnly()
        self.decrease_button.setEnabled(enabled and bool(flags & QAbstractSpinBox.StepEnabledFlag.StepDownEnabled))
        self.increase_button.setEnabled(enabled and bool(flags & QAbstractSpinBox.StepEnabledFlag.StepUpEnabled))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_stepper()

    def _layout_stepper(self):
        self.decrease_button.setGeometry(1, 1, 36, self.height() - 2)
        self.increase_button.setGeometry(self.width() - 37, 1, 36, self.height() - 2)
        self.lineEdit().setGeometry(44, 1, max(1, self.width() - 88), self.height() - 2)

    def showEvent(self, event):
        super().showEvent(event)
        self._layout_stepper()
        self._update_step_buttons()

    def changeEvent(self, event):
        super().changeEvent(event)
        if hasattr(self, "decrease_button"):
            self._layout_stepper()
            self._update_step_buttons()

    def setReadOnly(self, read_only):
        super().setReadOnly(read_only)
        self._update_step_buttons()

    def wheelEvent(self, event):
        # 明确禁止滚轮修改数值，即使输入框已获得焦点。
        event.ignore()


class SpinBox(_HorizontalStepper, QSpinBox):
    pass


class DoubleSpinBox(_HorizontalStepper, QDoubleSpinBox):
    pass


class TableView(QTableView):
    """名称获得剩余空间；缩略图、状态、路径保持有界宽度。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._column_widths = ()
        self.setMinimumHeight(180)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAlternatingRowColors(True)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(T.ROW_HEIGHT)
        self.horizontalHeader().setMinimumSectionSize(64)
        self.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.horizontalHeader().setStretchLastSection(False)

    def set_column_widths(self, widths):
        """首列为名称；后续列指定期望宽度，窗口不足时详情列可横向滚动。"""
        self._column_widths = tuple(widths)
        self._fit_columns()

    def _fit_columns(self):
        if not self._column_widths:
            return
        for index, width in enumerate(self._column_widths, 1):
            self.setColumnWidth(index, width)
        self.setColumnWidth(0, max(200, self.viewport().width() - sum(self._column_widths)))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_columns()
