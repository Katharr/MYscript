"""可读性回归：窄窗口不压扁控件、核心列不塌缩、主题和勾选仍可操作。"""

import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QPoint, QRect, QSettings, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QLineEdit, QListWidget, QListWidgetItem, QPushButton,
    QVBoxLayout, QWidget,
)

from mhxy.core import config as C
from mhxy.qtui import theme as T
from mhxy.qtui.log_panel import LogPanel
from mhxy.qtui.main import get_application
from mhxy.qtui.widgets import FlowLayout
from mhxy.qtui.window import MainWindow


def contrast(first, second):
    def luminance(value):
        color = QColor(value)
        channels = [color.redF(), color.greenF(), color.blueF()]
        linear = [x / 12.92 if x <= .04045 else ((x + .055) / 1.055) ** 2.4 for x in channels]
        return sum(x * weight for x, weight in zip(linear, (.2126, .7152, .0722)))
    lo, hi = sorted((luminance(first), luminance(second)))
    return (hi + .05) / (lo + .05)


class QtReadabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = get_application(["readability-tests", "-platform", "offscreen"])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="mhxy_readability_")
        self.addCleanup(self.directory.cleanup)
        self.cfg = copy.deepcopy(C.DEFAULT_CONFIG)
        for target, kwargs in (
            ("mhxy.core.config.load_config", dict(side_effect=lambda: copy.deepcopy(self.cfg))),
            ("mhxy.core.config.save_config", {}),
            ("mhxy.qtui.window.MainWindow.refresh_windows", {}),
            ("mhxy.qtui.daily_page.DailyPage._request_summary", {}),
            ("mhxy.qtui.treasure_map_page._ActivityPage._request_calibration", {}),
        ):
            mock = patch(target, **kwargs)
            mock.start()
            self.addCleanup(mock.stop)
        self.addCleanup(T.apply_theme, self.app, "light")

    def widget(self, widget):
        self.addCleanup(widget.deleteLater)
        self.addCleanup(widget.close)
        return widget

    def window(self):
        window = self.widget(MainWindow(QSettings(str(Path(self.directory.name) / "ui.ini"),
                                                  QSettings.Format.IniFormat)))
        window.scan_timer.stop()
        window.runtime_timer.stop()
        window.resize(800, 640)
        window.show()
        self.app.processEvents()
        return window

    def test_narrow_task_pages_keep_text_and_actions_accessible(self):
        window = self.window()
        for key in ("dungeon", "daily", "freeclick", "sniper", "treasure_map", "general"):
            with self.subTest(page=key):
                window.show_page(key)
                self.app.processEvents()
                page = window.pages[key]
                self.assertEqual(window.width(), 800)
                self.assertEqual(window.height(), 640)
                self.assertTrue(page.rect().contains(page.stop_button.geometry()))
                self.assertTrue(page.rect().contains(page.start_button.geometry()))
                for control in page.findChildren(QWidget):
                    if (not isinstance(control, (QCheckBox, QComboBox, QLineEdit))
                            or not control.isVisible() or control.objectName() == "qt_spinbox_lineedit"):
                        continue
                    self.assertGreaterEqual(control.height(), control.fontMetrics().height() + 6)
                self.assertLessEqual(page.body.width(), page.body_scroll.viewport().width())
        window.show_page("dungeon")
        page = window.pages["dungeon"]
        self.app.processEvents()
        self.assertGreater(page.body_scroll.verticalScrollBar().maximum(), 0)
        page.body_scroll.ensureWidgetVisible(page.calibration_details)
        self.app.processEvents()
        self.assertGreater(page.body_scroll.verticalScrollBar().value(), 0)

    def test_short_form_labels_do_not_wrap_into_narrow_fragments(self):
        window = self.window()
        window.show_page("dungeon")
        self.app.processEvents()
        page = window.pages["dungeon"]
        label = page.form.labelForField(page.dungeon_combo)
        self.assertGreaterEqual(label.width(), label.fontMetrics().horizontalAdvance(label.text()))
        self.assertGreaterEqual(page.dungeon_combo.width(), 220)

    def test_high_scale_work_area_keeps_the_window_and_stop_action_reachable(self):
        window = self.window()
        window.show_page("dungeon")
        screen = Mock()
        screen.availableGeometry.return_value = QRect(0, 0, 960, 540)
        window._fit_work_area(screen)
        self.app.processEvents()
        self.assertLessEqual(window.height(), 540)
        self.assertLessEqual(window.minimumHeight(), 540)
        self.assertTrue(window.pages["dungeon"].rect().contains(window.pages["dungeon"].stop_button.geometry()))

    def test_name_column_keeps_space_when_data_is_empty_or_long(self):
        window = self.window()
        window.show_page("freeclick")
        self.app.processEvents()
        page = window.pages["freeclick"]
        empty_width = page.view.columnWidth(0)
        self.assertGreaterEqual(empty_width, 200)
        page.model.replace([dict(name="很长的物品名称" * 20, template="")])
        self.app.processEvents()
        self.assertEqual(page.view.columnWidth(0), empty_width)
        self.assertLess(page.view.columnWidth(2), page.view.columnWidth(0))
        self.assertIn("很长的物品名称", page.model.data(page.model.index(0, 0), Qt.ItemDataRole.ToolTipRole))

    def test_toolbar_reflows_whole_buttons_without_overlap(self):
        host = self.widget(QWidget())
        outer = QVBoxLayout(host)
        flow = FlowLayout()
        outer.addLayout(flow)
        buttons = [QPushButton("重新标定模板", host) for _ in range(4)]
        for button in buttons:
            flow.addWidget(button)
        host.resize(230, 300)
        host.show()
        self.app.processEvents()
        self.assertGreater(len({b.y() for b in buttons}), 1)
        for index, button in enumerate(buttons):
            self.assertTrue(host.rect().contains(button.geometry()))
            for other in buttons[index + 1:]:
                self.assertFalse(button.geometry().intersects(other.geometry()))
        host.resize(700, 180)
        self.app.processEvents()
        self.assertEqual(len({b.y() for b in buttons}), 1)

    def test_checkbox_and_list_check_state_still_support_mouse_and_keyboard(self):
        host = self.widget(QWidget())
        layout = QVBoxLayout(host)
        checkbox = QCheckBox("选择角色", host)
        layout.addWidget(checkbox)
        view = QListWidget(host)
        item = QListWidgetItem("选择任务", view)
        item.setCheckState(Qt.CheckState.Unchecked)
        layout.addWidget(view)
        host.resize(360, 220)
        host.show()
        self.app.processEvents()
        QTest.mouseClick(checkbox, Qt.MouseButton.LeftButton, pos=QPoint(50, checkbox.height() // 2))
        self.assertTrue(checkbox.isChecked())
        QTest.keyClick(checkbox, Qt.Key.Key_Space)
        self.assertFalse(checkbox.isChecked())
        rect = view.visualItemRect(item)
        QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton,
                         pos=QPoint(rect.left() + 20, rect.center().y()))
        self.assertEqual(item.checkState(), Qt.CheckState.Checked)
        view.setCurrentItem(item)
        view.setFocus()
        QTest.keyClick(view, Qt.Key.Key_Space)
        self.assertEqual(item.checkState(), Qt.CheckState.Unchecked)

    def test_existing_log_text_recolors_and_history_survives_theme_changes(self):
        panel = self.widget(LogPanel(collapsed=False))
        panel.append("示例错误", "error")
        panel.append("示例成功", "success")
        for appearance in ("dark", "light", "dark", "light"):
            T.apply_theme(self.app, appearance)
            document = panel.text_edit.document()
            self.assertEqual(document.firstBlock().begin().fragment().charFormat().foreground().color(), QColor(T.ERROR))
            self.assertEqual(document.lastBlock().begin().fragment().charFormat().foreground().color(), QColor(T.SUCCESS))
            self.assertEqual(len(panel.entries), 2)
            fresh = self.widget(QLineEdit())
            fresh.ensurePolished()
            self.assertEqual(fresh.palette().color(QPalette.ColorRole.Base), QColor(T.INPUT))

    def test_theme_text_and_meaningful_boundaries_have_sufficient_contrast(self):
        for appearance, colors in T.PALETTES.items():
            with self.subTest(theme=appearance):
                for foreground in ("TEXT", "TEXT_DIM", "SUCCESS", "WARNING", "ERROR"):
                    for background in ("SURFACE", "INPUT"):
                        self.assertGreaterEqual(contrast(colors[foreground], colors[background]), 4.5)
                self.assertGreaterEqual(contrast(colors["ON_ACCENT"], colors["ACCENT"]), 4.5)
                for background in ("SURFACE", "INPUT"):
                    self.assertGreaterEqual(contrast(colors["BORDER"], colors[background]), 3)


if __name__ == "__main__":
    unittest.main()
