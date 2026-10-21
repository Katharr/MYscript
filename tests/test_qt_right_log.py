"""右侧日志：脚本空间、抽屉状态、滚动阅读和有界元数据回归。"""

import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QPoint, QPointF, QSettings, Qt
from PySide6.QtGui import QTextCursor, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractSlider, QWidget

from mhxy.core.config import DEFAULT_CONFIG
from mhxy.qtui import theme as T
from mhxy.qtui.log_panel import LogPanel
from mhxy.qtui.main import get_application
from mhxy.qtui.window import MainWindow


class QtRightLogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = get_application(["right-log-tests", "-platform", "offscreen"])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="mhxy_right_log_")
        self.addCleanup(self.directory.cleanup)
        self.settings = QSettings(str(Path(self.directory.name) / "ui.ini"), QSettings.Format.IniFormat)
        for target, kwargs in (
            ("mhxy.core.config.load_config", dict(side_effect=lambda: copy.deepcopy(DEFAULT_CONFIG))),
            ("mhxy.core.config.save_config", {}),
            ("mhxy.qtui.window.MainWindow.refresh_windows", {}),
            ("mhxy.qtui.window.MainWindow._fit_work_area", {}),
            ("mhxy.qtui.daily_page.DailyPage._request_summary", {}),
        ):
            mock = patch(target, **kwargs)
            mock.start()
            self.addCleanup(mock.stop)

    def widget(self, widget):
        self.addCleanup(widget.deleteLater)
        self.addCleanup(widget.close)
        return widget

    def settle(self):
        for _ in range(3):
            self.app.processEvents()

    def window(self, width=1120):
        window = self.widget(MainWindow(self.settings))
        window.scan_timer.stop()
        window.runtime_timer.stop()
        window.resize(width, 720)
        window.show()
        window.show_page("daily")
        self.settle()
        return window

    def panel(self, max_lines=5000):
        panel = self.widget(LogPanel(sidebar=True, collapsed=False, max_lines=max_lines))
        panel.resize(280, 600)
        panel.show()
        self.settle()
        return panel

    def test_log_uses_full_height_and_extra_width_goes_to_script(self):
        window = self.window()
        work = window.log_workspace
        self.assertFalse(work.overlay_mode)
        self.assertEqual(window.log_panel.width(), 280)
        self.assertEqual(work.script.width(), 640)
        self.assertGreater(window.log_panel.text_edit.height(), 400)
        window.resize(1440, 900)
        self.settle()
        self.assertEqual(window.log_panel.width(), 280)
        self.assertEqual(work.script.width(), 960)
        self.assertEqual(window.log_panel.y(), 0)  # splitter 内与脚本顶端对齐

    def test_narrow_drawer_preserves_script_geometry_and_escape_closes_it(self):
        window = self.window(800)
        work = window.log_workspace
        self.assertTrue(work.overlay_mode)
        self.assertFalse(work.overlay_open)
        before = work.script.geometry()
        work.open_button.click()
        self.settle()
        self.assertTrue(window.log_panel.isVisible())
        self.assertEqual(work.script.geometry(), before)
        self.assertEqual(window.log_panel.width(), 280)
        self.assertTrue(work.rect().contains(window.log_panel.geometry()))
        self.assertTrue(window.log_panel.testAttribute(Qt.WidgetAttribute.WA_StyledBackground))
        QTest.keyClick(window.log_panel.toggle_button, Qt.Key.Key_Escape)
        self.settle()
        self.assertFalse(work.overlay_open)
        self.assertEqual(work.script.geometry(), before)
        self.assertTrue(work.expanded)

    def test_dragged_width_and_wide_preference_survive_narrow_close_and_restore(self):
        window = self.window(1440)
        work = window.log_workspace
        work.splitter.moveSplitter(work.splitter.width() - 8 - 310, 1)
        self.settle()
        self.assertEqual(work.preferred_width, 310)
        window.resize(800, 640)
        self.settle()
        self.assertTrue(work.overlay_mode)
        window.close()
        self.settings.sync()
        self.assertTrue(self.settings.value("window/logExpanded", type=bool))
        self.assertEqual(self.settings.value("window/logWidth", type=int), 310)
        restored = self.window(1440)
        self.assertTrue(restored.log_workspace.expanded)
        self.assertEqual(restored.log_panel.width(), 310)

    def test_collapsed_sidebar_restores_and_alerts_do_not_force_it_open(self):
        window = self.window()
        work = window.log_workspace
        window.log_panel.toggle_button.click()
        window.log_panel.append("暂不可用", "warn", "窗口")
        window.log_panel.append("普通信息", source="窗口")
        self.assertFalse(work.expanded)
        self.assertTrue(work.rail.isVisible())
        self.assertEqual(work.alert_label.text(), "1")
        self.assertFalse(window.log_panel.isVisible())
        work.open_button.click()
        self.settle()
        self.assertTrue(window.log_panel.isVisible())
        self.assertFalse(work.alert_label.isVisible())
        work.close_log()
        window.close()
        restored = self.window()
        self.assertFalse(restored.log_workspace.expanded)
        self.assertFalse(restored.log_panel.isVisible())

    def test_complex_page_uses_drawer_instead_of_squeezing_script(self):
        window = self.window()
        page = self.widget(QWidget())
        page.setMinimumWidth(900)
        window.log_workspace.set_page(page)
        self.settle()
        self.assertTrue(window.log_workspace.overlay_mode)
        self.assertGreaterEqual(window.log_workspace.script.width(), 880)
        window.log_workspace.set_page(window.pages["daily"])
        self.settle()
        self.assertFalse(window.log_workspace.overlay_mode)
        self.assertTrue(window.log_panel.isVisible())

    def test_sidebar_minimum_width_reflows_toolbar_without_overlap(self):
        panel = self.panel()
        panel.resize(240, 600)
        panel.source_filter.addItem("一个很长很长的日志来源名称", "long")
        panel.source_filter.setCurrentIndex(1)
        self.settle()
        source, follow = panel.source_filter.geometry(), panel.follow_checkbox.geometry()
        self.assertFalse(source.intersects(follow))
        self.assertTrue(panel.rect().contains(source))
        self.assertTrue(panel.rect().contains(follow))
        self.assertEqual(panel.width(), 240)

    def test_follow_survives_wrapping_and_resize_but_pauses_for_user_scroll(self):
        panel = self.panel()
        for i in range(100):
            panel.append(f"第 {i} 行：这是一条需要自然换行的运行日志", source="任务")
        self.settle()
        bar = panel.text_edit.verticalScrollBar()
        self.assertTrue(panel.follow_checkbox.isChecked())
        self.assertEqual(bar.value(), bar.maximum())
        panel.resize(240, 500)
        self.settle()
        self.assertTrue(panel.follow_checkbox.isChecked())
        self.assertEqual(bar.value(), bar.maximum())
        bar.triggerAction(QAbstractSlider.SliderAction.SliderPageStepSub)
        self.settle()
        self.assertFalse(panel.follow_checkbox.isChecked())
        position = bar.value()
        panel.append("新消息", source="任务")
        self.settle()
        self.assertEqual(bar.value(), position)
        self.assertTrue(panel.new_button.isVisible())
        panel.new_button.click()
        self.settle()
        self.assertTrue(panel.follow_checkbox.isChecked())
        self.assertEqual(bar.value(), bar.maximum())
        self.assertFalse(panel.new_button.isVisible())

    def test_selection_and_scroll_survive_append_and_theme_rebuild(self):
        panel = self.panel()
        for i in range(100):
            panel.append(f"旧日志 {i}", source="任务")
        self.settle()
        bar = panel.text_edit.verticalScrollBar()
        bar.triggerAction(QAbstractSlider.SliderAction.SliderToMinimum)
        cursor = panel.text_edit.textCursor()
        cursor.setPosition(0)
        cursor.movePosition(QTextCursor.MoveOperation.NextCharacter, QTextCursor.MoveMode.KeepAnchor, 8)
        panel.text_edit.setTextCursor(cursor)
        selected = cursor.selectedText()
        self.assertFalse(panel.follow_checkbox.isChecked())
        panel.append("新日志", source="任务")
        self.settle()
        self.assertEqual(panel.text_edit.textCursor().selectedText(), selected)
        timestamps = list(panel._timestamps)
        panel._theme_changed()
        self.settle()
        self.assertEqual(panel.text_edit.textCursor().selectedText(), selected)
        self.assertEqual(bar.value(), 0)
        self.assertEqual(panel._timestamps, timestamps)

    def test_wheel_and_keyboard_pause_follow_even_with_pending_layout(self):
        panel = self.panel()
        for i in range(100):
            panel.append(f"日志 {i}", source="任务")
        self.settle()
        bar = panel.text_edit.verticalScrollBar()
        QTest.keyClick(panel.text_edit, Qt.Key.Key_PageUp)
        self.settle()
        self.assertFalse(panel.follow_checkbox.isChecked())
        self.assertLess(bar.value(), bar.maximum())
        panel.set_following(True)
        # 在未消费布局队列前追加，再立即滚轮；新布局不能把用户拉回末尾。
        panel.append("又一条日志", source="任务")
        viewport = panel.text_edit.viewport()
        local = QPointF(20, 20)
        wheel = QWheelEvent(local, QPointF(viewport.mapToGlobal(local.toPoint())),
                            QPoint(), QPoint(0, 120), Qt.MouseButton.NoButton,
                            Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
        self.app.sendEvent(viewport, wheel)
        self.settle()
        self.assertFalse(panel.follow_checkbox.isChecked())
        self.assertLess(bar.value(), bar.maximum())

    def test_filter_clear_and_eviction_keep_timestamp_and_alert_metadata_bounded(self):
        panel = self.panel(max_lines=3)
        panel.set_collapsed(True)
        panel.append("首条", "error", "甲")
        panel.append("12:34:56 已有时间", source="乙")
        panel.append("第三条", source="甲")
        self.assertEqual(panel._unread_alerts, 1)
        panel.append("末条", source="乙")
        self.assertEqual(panel._unread_alerts, 0)
        self.assertEqual(len(panel._timestamps), 3)
        self.assertEqual(len(panel._unread_flags), 3)
        panel.set_expanded(True)
        panel.source_filter.setCurrentIndex(panel.source_filter.findData("乙"))
        text = panel.toPlainText()
        self.assertIn("12:34:56", text)
        self.assertIn("末条", text)
        self.assertNotIn("第三条", text)
        self.assertTrue(text.startswith("[乙] 12:34:56"))
        panel.clear()
        self.assertEqual(panel.entries, [])
        self.assertEqual(panel._timestamps, [])
        self.assertEqual(panel._unread_flags, [])
        self.assertFalse(panel.new_button.isVisible())
        self.assertTrue(panel.follow_checkbox.isChecked())


if __name__ == "__main__":
    unittest.main()
