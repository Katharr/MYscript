"""字段尺寸和交互回归：滚轮只滚动页面，不修改任何字段。"""

import copy
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QScrollArea, QScrollBar, QStyle, QStyleOptionSlider, QVBoxLayout, QWidget,
)

from mhxy.core import config as C
from mhxy.qtui import theme as T
from mhxy.qtui.main import get_application
from mhxy.qtui.settings_page import SettingsPage
from mhxy.qtui.secret_realm_page import SecretRealmPage
from mhxy.qtui.widgets import ComboBox, DoubleSpinBox, SpinBox


class QtFieldControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = get_application(["field-controls", "-platform", "offscreen"])

    def setUp(self):
        T.apply_theme(self.app, "light")
        self.addCleanup(T.apply_theme, self.app, "light")

    def widget(self, widget):
        self.addCleanup(widget.deleteLater)
        self.addCleanup(widget.close)
        return widget

    def controls(self, parent):
        integer, decimal, combo = SpinBox(parent), DoubleSpinBox(parent), ComboBox(parent)
        integer.setValue(5)
        decimal.setValue(5.5)
        combo.addItems(["第一个选项", "第二个选项", "第三个选项"])
        combo.setCurrentIndex(1)
        return integer, decimal, combo

    def wheel(self, target, delta, modifiers=Qt.KeyboardModifier.NoModifier):
        window = target.window()
        QTest.wheelEvent(window.windowHandle(), target.mapTo(window, target.rect().center()),
                         QPoint(0, delta), QPoint(), modifiers)
        self.app.processEvents()

    def test_wheel_never_edits_values_or_selections_even_when_focused(self):
        host = self.widget(QWidget())
        layout = QVBoxLayout(host)
        controls = self.controls(host)
        for control in controls:
            layout.addWidget(control)
        host.resize(450, 240)
        host.show()
        self.app.processEvents()
        for control in controls:
            changed = Mock()
            (control.currentIndexChanged if isinstance(control, ComboBox) else control.valueChanged).connect(changed)
            for focused in (False, True):
                if focused:
                    control.setFocus()
                else:
                    control.clearFocus()
                self.app.processEvents()
                for delta in (-120, 120):
                    for modifiers in (Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.ControlModifier):
                        self.wheel(control, delta, modifiers)
            changed.assert_not_called()
        self.assertEqual([controls[0].value(), controls[1].value(), controls[2].currentIndex()], [5, 5.5, 1])

    def test_wheel_over_fields_and_step_buttons_scrolls_the_page(self):
        host = self.widget(QWidget())
        layout = QVBoxLayout(host)
        scroll = QScrollArea(host)
        scroll.setWidgetResizable(True)
        content = QWidget(scroll)
        rows = QVBoxLayout(content)
        controls = self.controls(content)
        for control in controls:
            rows.addWidget(control)
        rows.addStretch()
        content.setMinimumHeight(1000)
        scroll.setWidget(content)
        layout.addWidget(scroll)
        host.resize(450, 340)
        host.show()
        self.app.processEvents()
        for target in (*controls, controls[0].lineEdit(), controls[0].increase_button):
            with self.subTest(target=type(target).__name__):
                scroll.verticalScrollBar().setValue(0)
                self.app.processEvents()
                target.setFocus()
                self.wheel(target, -120)
                self.assertGreater(scroll.verticalScrollBar().value(), 0)
        self.assertEqual([controls[0].value(), controls[1].value(), controls[2].currentIndex()], [5, 5.5, 1])

    def test_step_buttons_commit_values_and_respect_limits_read_only_and_disabled(self):
        for cls in (SpinBox, DoubleSpinBox):
            with self.subTest(control=cls.__name__):
                control = self.widget(cls())
                control.setRange(0, 10)
                control.setValue(5)
                control.show()
                self.app.processEvents()
                committed = Mock()
                control.editingFinished.connect(committed)
                control.increase_button.click()
                self.assertEqual(control.value(), 6)
                committed.assert_called_once_with()
                control.setValue(10)
                self.assertFalse(control.increase_button.isEnabled())
                control.setValue(0)
                self.assertFalse(control.decrease_button.isEnabled())
                control.setReadOnly(True)
                self.assertFalse(control.increase_button.isEnabled())
                control.setReadOnly(False)
                control.setEnabled(False)
                self.assertFalse(control.increase_button.isEnabled())
                control.setEnabled(True)
                self.assertTrue(control.increase_button.isEnabled())

    def test_stepper_keeps_keyboard_input_suffix_and_text_space_after_theme_changes(self):
        control = self.widget(DoubleSpinBox())
        control.setRange(0, 999999)
        control.setDecimals(1)
        control.setSuffix(" 分钟")
        control.setSpecialValueText("不限")
        control.show()
        for theme in ("dark", "light"):
            T.apply_theme(self.app, theme)
            self.app.processEvents()
            control.setFocus()
            control.selectAll()
            QTest.keyClicks(control, "12.5")
            QTest.keyClick(control, Qt.Key.Key_Return)
            self.assertEqual(control.value(), 12.5)
            QTest.keyClick(control, Qt.Key.Key_Up)
            self.assertEqual(control.value(), 13.5)
            self.assertIn("分钟", control.text())
            self.assertGreaterEqual(control.lineEdit().width(), 100)
            self.assertGreaterEqual(control.increase_button.width(), 36)
            self.assertGreaterEqual(control.increase_button.height(), 34)
            self.assertFalse(control.lineEdit().geometry().intersects(control.increase_button.geometry()))
            self.assertFalse(control.lineEdit().geometry().intersects(control.decrease_button.geometry()))
        control.setValue(0)
        self.assertEqual(control.lineEdit().text(), "不限")

    def test_settings_fields_stay_compact_and_save_aligns_with_field_column(self):
        with patch.object(C, "load_config", side_effect=lambda: copy.deepcopy(C.DEFAULT_CONFIG)):
            page = self.widget(SettingsPage())
            page.resize(920, 600)
            page.show()
            self.app.processEvents()
        self.assertLessEqual(page.threshold.width(), 144)
        self.assertLessEqual(page.window_title.width(), 360)
        self.assertLessEqual(page.appearance.width(), 208)
        for edit in page.speed_fields.values():
            self.assertLessEqual(edit.width(), 144)
        self.assertEqual(page.save_button.width(), 128)
        self.assertEqual(page.save_button.mapTo(page, QPoint()).x(), page.threshold.mapTo(page, QPoint()).x())

    def test_activity_unit_labels_keep_numeric_configuration_and_button_saves(self):
        with patch.object(C, "load_config", side_effect=lambda: copy.deepcopy(C.DEFAULT_CONFIG)), \
             patch.object(C, "save_config") as save, \
             patch("mhxy.qtui.treasure_map_page._ActivityPage._request_calibration"):
            page = self.widget(SecretRealmPage())
            time = page.parameters["time_limit_min"]
            self.assertEqual(time.suffix(), " 分钟")
            self.assertEqual(page.form.labelForField(time).text(), "时间上限")
            self.assertEqual(page.parameters["battle_timeout_sec"].suffix(), " 秒")
            before = time.value()
            time.increase_button.click()
            saved = save.call_args.args[0]
            self.assertEqual(C.task_config(saved, "secret_realm")["loop"]["time_limit_min"], before + 1)
            time.setValue(0)
            self.assertEqual(time.value(), 0)
            self.assertEqual(time.text(), "不限")

    def test_scrollbar_buttons_have_large_targets_and_work_in_both_directions(self):
        for orientation in (Qt.Orientation.Vertical, Qt.Orientation.Horizontal):
            bar = self.widget(QScrollBar(orientation))
            bar.setRange(0, 1000)
            bar.setPageStep(100)
            bar.setSingleStep(10)
            bar.setValue(400)
            bar.resize(24, 240) if orientation == Qt.Orientation.Vertical else bar.resize(240, 24)
            bar.show()
            self.app.processEvents()
            option = QStyleOptionSlider()
            bar.initStyleOption(option)
            for subcontrol, expected in ((QStyle.SubControl.SC_ScrollBarSubLine, 390),
                                         (QStyle.SubControl.SC_ScrollBarAddLine, 400)):
                rect = bar.style().subControlRect(QStyle.ComplexControl.CC_ScrollBar, option, subcontrol, bar)
                self.assertGreaterEqual(rect.width(), 24)
                self.assertGreaterEqual(rect.height(), 24)
                QTest.mouseClick(bar, Qt.MouseButton.LeftButton, pos=rect.center())
                self.assertEqual(bar.value(), expected)


if __name__ == "__main__":
    unittest.main()
