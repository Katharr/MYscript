import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtTest import QTest
from mhxy.core import config as C
from mhxy.qtui.main import get_application
from mhxy.qtui.window import MainWindow
from mhxy.qtui.role_strip import WindowState


class WorkspaceV3Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = get_application(['ui-v3-tests', '-platform', 'offscreen'])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cfg = copy.deepcopy(C.DEFAULT_CONFIG)
        for target, options in (
            ('mhxy.core.config.load_config', dict(side_effect=lambda: copy.deepcopy(self.cfg))),
            ('mhxy.core.config.save_config', dict(side_effect=lambda cfg: self.cfg.update(copy.deepcopy(cfg)))),
            ('mhxy.qtui.window.scan_windows', dict(return_value=[])),
        ):
            p = patch(target, **options)
            p.start()
            self.addCleanup(p.stop)
        self.window = MainWindow(QSettings(str(Path(self.temp.name) / 'ui.ini'), QSettings.Format.IniFormat))
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.window.close)
        self.window.show()
        self.app.processEvents()

    def test_task_tabs_select_matching_page_with_keyboard_and_preserve_parameters(self):
        self.window.show_page('tasks')
        tabs = self.window.task_tabs
        self.assertEqual([tabs.tabText(i) for i in range(tabs.count())],
                         ['宝图', '运镖', '秘境降妖', '刷副本', '秒装备'])
        self.assertEqual(self.window.current_page, 'treasure_map')
        original = self.window.stack.currentWidget()
        original.skip_collect.click()
        self.assertTrue(original.skip_collect.isChecked())
        tabs.setFocus()
        QTest.keyClick(tabs, Qt.Key.Key_Right)
        self.assertEqual(self.window.current_page, 'escort')
        self.assertEqual(self.window.stack.currentWidget().start_button.text(), '开始运镖')
        QTest.keyClick(tabs, Qt.Key.Key_End)
        self.assertEqual(self.window.current_page, 'sniper')
        QTest.keyClick(tabs, Qt.Key.Key_Home)
        self.assertIs(self.window.stack.currentWidget(), original)
        self.assertTrue(original.skip_collect.isChecked())
        self.assertTrue(self.window.nav_buttons['tasks'].isChecked())
    def test_running_and_stopping_lock_tabs_and_keep_stop_reachable(self):
        self.window.show_page('escort')
        page = self.window.pages['escort']
        page._start_pending = True
        self.window._update_strip()
        self.assertFalse(self.window.task_tabs.isEnabled())
        self.window.show_page('sniper')
        self.assertEqual(self.window.current_page, 'escort')
        page._start_pending = False
        self.window._update_strip()
        self.assertTrue(self.window.task_tabs.isEnabled())

    def test_toolbar_stays_above_log_overlay_and_role_strip_spans_workspace(self):
        self.window.resize(800, 640)
        self.window.show_page('escort')
        self.window.log_button.click()
        self.app.processEvents()
        self.assertTrue(self.window.log_workspace.overlay_open)
        for button in (self.window.window_tools.wake_button, self.window.window_tools.close_button,
                       self.window.log_button):
            self.assertTrue(button.isVisible())
            point = button.mapTo(self.window, button.rect().center())
            self.assertIs(self.window.childAt(point), button)
        self.assertEqual(self.window.log_panel.mapTo(self.window, QPoint()).y(), 0)
        self.assertFalse(self.window.log_panel.toggle_button.isVisible())
        self.assertEqual(self.window.role_strip.width(), self.window.log_workspace.script.width() - 48)
        QTest.keyClick(self.window.log_panel.text_edit, Qt.Key.Key_Escape)
        self.app.processEvents()
        self.assertFalse(self.window.log_workspace.overlay_open)

    def test_general_tools_start_original_tasks_and_open_inventory_editor(self):
        self.window.show_page('general')
        page = self.window.pages['general']
        for button, task in ((page.team_button, 'dungeon'), (page.disband_button, 'disband'),
                             (page.organize_button, 'organize_bag')):
            with patch('mhxy.qtui.task_page.TaskPage.toggle_run') as start:
                button.click()
                start.assert_called_once()
                self.assertEqual(page.tool_task, task)
        with patch('mhxy.qtui.inventory_panel.InventoryItemsDialog') as dialog:
            page.items_button.click()
            dialog.return_value.exec.assert_called_once()

    def test_tool_runner_and_mode_summary_match_during_run_and_after_failure(self):
        self.window._apply_windows([WindowState(101, '测试甲', 'ready'), WindowState(102, '测试乙', 'ready')], '')
        self.window.select_all_button.click()
        self.window.show_page('general')
        page = self.window.pages['general']
        with patch.object(page.runtime, 'start', return_value=(True, [])), \
             patch.object(page.runtime, 'is_running', return_value=True) as running:
            page.team_button.click()
            self.assertEqual(page.runner.task.name, 'dungeon')
            self.assertEqual(page.mode_label.text(), '实战')
            self.assertTrue(page.stop_button.isVisible())
            self.window._update_strip()
            self.window.log_workspace.open_log()
            self.assertTrue(self.window.log_stop_button.isVisible())
            self.assertTrue(self.window.log_stop_button.isEnabled())
            point = self.window.log_stop_button.mapTo(self.window, self.window.log_stop_button.rect().center())
            self.assertIs(self.window.childAt(point), self.window.log_stop_button)
            for _ in range(100):
                if not page._start_pending:
                    break
                QTest.qWait(10)
            self.assertFalse(page._start_pending)
            running.return_value = False
            page.runner.outcome = 'failed'
            self.window._pump_task_logs()
            self.assertEqual(page.mode_label.text(), '演练')
            self.assertEqual(page.start_button.text(), '整理背包')

    def test_daily_dungeon_mode_uses_same_fallback_as_runner_and_can_roll_back(self):
        self.cfg['tasks']['dungeon']['selected'] = 'removed_dungeon'
        self.window.show_page('daily')
        page = self.window.pages['daily']
        for i in range(page.steps_list.count()):
            if page.steps_list.item(i).data(Qt.ItemDataRole.UserRole) == 'dungeon':
                page.steps_list.setCurrentRow(i)
                break
        page.step_live_mode.setChecked(True)
        self.assertFalse(C.task_config(self.cfg, 'taohaiqu')['dry_run'])
        self.assertNotIn('removed_dungeon', self.cfg['tasks'])
        with patch.object(C, 'save_config', side_effect=OSError('模拟保存失败')):
            page.step_live_mode.setChecked(False)
        self.assertFalse(C.task_config(self.cfg, 'taohaiqu')['dry_run'])
        self.assertIn('保存失败', page.summary_label.text())
    def test_legacy_dark_config_does_not_override_v3_light_baseline(self):
        self.cfg['appearance'] = 'dark'
        other = MainWindow(QSettings(str(Path(self.temp.name) / 'legacy.ini'), QSettings.Format.IniFormat))
        try:
            self.assertEqual(self.app.property('appearance'), 'light')
            other._config_changed(copy.deepcopy(self.cfg))
            self.assertEqual(self.app.property('appearance'), 'light')
            self.assertEqual(self.cfg['appearance'], 'dark')
            other.show_page('settings')
            self.assertEqual(other.pages['settings'].appearance.currentData(), 'light')
        finally:
            other.close()
            other.deleteLater()

    def test_daily_inline_mode_and_configuration_actions_use_clicked_row(self):
        self.window.show_page('daily')
        page = self.window.pages['daily']
        self.app.processEvents()
        index = page.steps_list.model().index(0, 0)
        name = index.data(Qt.ItemDataRole.UserRole)
        rect = page.steps_list.visualRect(index)
        mode, _, configure = page.steps_list.itemDelegate().action_rects(rect)
        QTest.mouseClick(page.steps_list.viewport(), Qt.MouseButton.LeftButton, pos=mode.center())
        self.assertFalse(C.task_config(self.cfg, name)['dry_run'])
        QTest.mouseClick(page.steps_list.viewport(), Qt.MouseButton.LeftButton, pos=configure.center())
        self.assertEqual(self.window.current_page, name)

    def test_explicit_theme_choice_after_upgrade_survives_restart(self):
        self.window.show_page('settings')
        settings = self.window.pages['settings']
        settings.appearance.setCurrentIndex(settings.appearance.findData('dark'))
        self.assertTrue(settings.save())
        self.assertEqual(self.app.property('appearance'), 'dark')
        other = MainWindow(self.window._settings)
        try:
            self.assertEqual(self.app.property('appearance'), 'dark')
        finally:
            other.close()
            other.deleteLater()

    def test_inline_mode_toggles_actual_configuration_while_summary_is_pending(self):
        self.cfg['tasks']['escort']['dry_run'] = False
        self.window.show_page('daily')
        page = self.window.pages['daily']
        page._summary_rows.clear()
        row = next(i for i in range(page.steps_list.count())
                   if page.steps_list.item(i).data(Qt.ItemDataRole.UserRole) == 'escort')
        page._toggle_row_mode(row)
        self.assertTrue(C.task_config(self.cfg, 'escort')['dry_run'])
