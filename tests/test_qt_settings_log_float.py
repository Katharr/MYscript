"""spec10 的配置、日志积压及悬浮生命周期回归；不读写真实资产。"""

import copy
import queue
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtCore import QSettings
from PySide6.QtTest import QSignalSpy

from mhxy.core import config as cfg_mod
from mhxy.core.runner import TaskRunner
from mhxy.core.runtime import RuntimeService
from mhxy.gui import shortcuts
from mhxy.qtui.float_log import FloatLogWindow
from mhxy.qtui.log_panel import LogPanel
from mhxy.qtui.main import get_application
from mhxy.qtui.settings_page import SettingsPage
from mhxy.qtui.window import MainWindow


class QtSettingsLogFloatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = get_application(["spec10-tests", "-platform", "offscreen"])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="mhxy_spec10_")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        for target, value in (("CONFIG_PATH", self.root / "config.json"),
                              ("CONFIG_BACKUP_DIR", self.root / "backups")):
            p = patch.object(cfg_mod, target, value)
            p.start()
            self.addCleanup(p.stop)
        cfg_mod.save_config(copy.deepcopy(cfg_mod.DEFAULT_CONFIG))
        self.runtime = RuntimeService()
        for target, value in (("mhxy.qtui.window.scan_windows", []),
                              ("mhxy.core.runtime.get_runtime", self.runtime),
                              ("mhxy.qtui.task_page.get_runtime", self.runtime)):
            p = patch(target, return_value=value)
            p.start()
            self.addCleanup(p.stop)

    def widget(self, widget):
        self.addCleanup(widget.deleteLater)
        self.addCleanup(widget.close)
        return widget

    def window(self):
        window = self.widget(MainWindow(QSettings(str(self.root / "ui.ini"), QSettings.Format.IniFormat)))
        window.scan_timer.stop()
        window.runtime_timer.stop()
        return window

    def test_settings_hotkeys_roundtrip_with_legacy_parser(self):
        page = self.widget(SettingsPage())
        for key in shortcuts.HOTKEY_NAMES:
            for values in ((True, True, False), (False, False, True), (False, False, False)):
                for checkbox, value in zip(page.modifiers, values):
                    checkbox.setChecked(value)
                page.hotkey_key.setCurrentText(key)
                self.assertTrue(page.save(), page.status.text())
                cfg = cfg_mod.load_config()
                self.assertEqual(shortcuts._split_stop_hotkey(cfg["hotkey_stop"]), (*values, key))
        page.refresh()
        self.assertEqual(page.hotkey_key.currentText(), key)

    def test_settings_merge_latest_config_and_save_existing_fields(self):
        page = self.widget(SettingsPage())
        cfg = cfg_mod.load_config()
        cfg["account_launch"]["launcher_path"] = "new-launcher.exe"
        cfg["targets"]["single_index"] = 2
        cfg["tasks"]["sniper"]["dry_run"] = True
        cfg_mod.save_config(cfg)
        page.appearance.setCurrentIndex(page.appearance.findData("light"))
        page.failsafe.setCurrentIndex(page.failsafe.findData("bottom_left"))
        page.speed_fields["speed"].setText("2.5")
        page.speed_fields["px_per_step"].setText("20")
        page.speed_fields["second_buy_timeout_sec"].setText("1.2")
        spy = QSignalSpy(page.config_changed)
        self.assertTrue(page.save(), page.status.text())
        saved = cfg_mod.load_config()
        self.assertEqual(spy.count(), 1)
        self.assertEqual(saved["account_launch"], cfg["account_launch"])
        self.assertEqual(saved["targets"], cfg["targets"])
        self.assertTrue(saved["tasks"]["sniper"]["dry_run"])
        self.assertEqual(saved["appearance"], "light")
        self.assertEqual(saved["failsafe_corner"], "bottom_left")
        self.assertEqual(saved["humanize"]["speed"], 2.5)
        self.assertIsInstance(saved["humanize"]["px_per_step"], int)
        self.assertEqual(saved["tasks"]["sniper"]["loop"]["second_buy_timeout_sec"], 1.2)

    def test_invalid_settings_never_write_or_emit_config(self):
        page = self.widget(SettingsPage())
        spy = QSignalSpy(page.config_changed)
        before = cfg_mod.CONFIG_PATH.read_bytes()
        cases = [(page.speed_fields["speed"], value) for value in ("nan", "inf", "bad", "", "0", "3.1")]
        cases += [(page.speed_fields["px_per_step"], "12.5"), (page.threshold, "1.1"),
                  (page.window_title, " ")]
        with patch.object(cfg_mod, "save_config") as save:
            for edit, value in cases:
                page.refresh()
                edit.setText(value)
                self.assertFalse(page.save(), value)
                self.assertIn("未保存", page.status.text())
            save.assert_not_called()
        self.assertEqual(spy.count(), 0)
        self.assertEqual(cfg_mod.CONFIG_PATH.read_bytes(), before)

    def test_failed_save_is_visible_and_does_not_change_live_config(self):
        page = self.widget(SettingsPage())
        spy = QSignalSpy(page.config_changed)
        with patch.object(cfg_mod, "save_config", side_effect=cfg_mod.ConfigWriteRejected("配置护栏拒绝")):
            self.assertFalse(page.save())
        self.assertIn("配置护栏拒绝", page.status.text())
        self.assertEqual(spy.count(), 0)

    def test_settings_apply_light_theme_to_existing_and_new_pages(self):
        window = self.window()
        original_style = window.role_page.styleSheet()
        window.show_page("settings")
        settings = window.pages["settings"]
        settings.appearance.setCurrentIndex(settings.appearance.findData("light"))
        self.assertTrue(settings.save())
        self.assertEqual(self.app.property("appearance"), "light")
        self.assertIn("#ffffff", window.role_page.styleSheet())
        window.show_page("daily")
        self.assertIn("#f2f4f8", window.pages["daily"].styleSheet())
        window.show_page("settings")
        settings.appearance.setCurrentIndex(settings.appearance.findData("dark"))
        self.assertTrue(settings.save())
        self.assertEqual(window.role_page.styleSheet(), original_style)

    def test_log_filter_preserves_other_sources_and_clear_clears_history(self):
        panel = self.widget(LogPanel(collapsed=False, max_lines=4))
        panel.append("甲1", source="甲")
        panel.append("乙1", source="乙")
        self.assertEqual(panel.text_edit.toPlainText(), "[甲] 甲1\n[乙] 乙1")
        panel.source_filter.setCurrentIndex(panel.source_filter.findData("甲"))
        self.assertEqual(panel.toPlainText(), "[甲] 甲1")
        panel.append("乙2", source="乙")
        panel.append("甲2", source="甲")
        panel.append("乙3", source="乙")
        self.assertEqual(panel.text_edit.toPlainText(), "[甲] 甲2")
        panel.source_filter.setCurrentIndex(0)
        self.assertIn("乙1", panel.toPlainText())
        self.assertNotIn("甲1", panel.toPlainText())
        panel.clear_button.click()
        self.assertEqual(panel.entries, [])
        self.assertEqual(panel.source_filter.count(), 1)
        panel.source_filter.setCurrentIndex(0)
        self.assertEqual(panel.toPlainText(), "")

    def test_filtered_multiline_logs_trim_evicted_records_without_rebuilding(self):
        panel = self.widget(LogPanel(collapsed=False, max_lines=5))
        panel.append("甲1\n甲2\n甲3", source="甲")
        panel.source_filter.setCurrentIndex(panel.source_filter.findData("甲"))
        panel.append("甲4\n甲5", source="甲")
        for i in range(4):
            panel.append(str(i), source="乙")
        self.assertEqual(panel.text_edit.toPlainText(), "[甲] 甲4\n甲5")
        panel.source_filter.setCurrentIndex(0)
        self.assertEqual(panel.text_edit.blockCount(), 5)
        self.assertIn("[乙] 3", panel.text_edit.toPlainText())

    def test_collapsed_logs_cache_bounded_history_and_render_on_expand(self):
        panel = self.widget(LogPanel(max_lines=5))
        for i in range(25):
            panel.append(f"日志{i}", source="任务")
        self.assertEqual(len(panel.entries), 5)
        self.assertEqual(panel.text_edit.toPlainText(), "")
        panel.toggle_button.click()
        self.assertFalse(panel.is_collapsed)
        self.assertIn("日志24", panel.text_edit.toPlainText())
        self.assertNotIn("日志19", panel.text_edit.toPlainText())
        self.assertLessEqual(panel.text_edit.blockCount(), 5)
        panel.toggle_button.click()
        self.assertTrue(panel.is_collapsed)

    def test_log_payload_and_source_menu_are_bounded(self):
        panel = self.widget(LogPanel(max_lines=80))
        for i in range(200):
            panel.append("x" * 10000, source=f"来源{i}")
        self.assertEqual(len(panel.entries), 80)
        self.assertLessEqual(panel.source_filter.count(), panel.MAX_SOURCES + 1)
        self.assertTrue(all(len(message) <= panel.MAX_MESSAGE_CHARS for _, message, _ in panel.entries))

    def test_runtime_overload_is_bounded_nonblocking_and_keeps_latest_logs(self):
        runner = TaskRunner(SimpleNamespace(name="test"), {})
        runner.runtime = self.runtime
        worker = threading.Thread(target=lambda: [runner._log(str(i)) for i in range(15000)], daemon=True)
        worker.start()
        worker.join(3)
        self.assertFalse(worker.is_alive(), "日志生产者被缓存阻塞")
        self.assertEqual(self.runtime._logs.qsize(), 5000)
        self.assertEqual(runner.log_queue.qsize(), 5000)
        first_batch = self.runtime.drain_logs(64)
        self.assertEqual(len(first_batch), 64)
        self.assertEqual(first_batch[0][2], "10000")
        self.assertEqual(self.runtime.drain_logs()[-1][2], "14999")
        self.assertTrue(runner.log_queue.empty())

    def test_main_drawer_routes_hidden_page_and_launch_logs_once(self):
        window = self.window()
        window.show_page("daily")
        page = window.pages["daily"]
        runner = SimpleNamespace(label="日常一条龙", log_queue=queue.Queue())
        page.runner = runner
        page.log_panel.append("检查失败", "error", "日常一条龙")
        window.show_page("about")
        self.runtime.publish(runner, "后台日志", "info")
        window._pump_task_logs()
        self.assertTrue(page.log_panel.isHidden())
        self.assertEqual(len(window.log_panel.entries), 2)
        self.assertIn("后台日志", window.log_panel.toPlainText())
        page.runner = None
        for i in range(100):
            self.runtime.publish(runner, str(i), "info")
        window._pump_task_logs()
        self.assertEqual(self.runtime._logs.qsize(), 36)
        window.log_panel.clear()
        window._pump_task_logs()
        self.assertEqual(len(window.log_panel.entries), 36)

    def test_float_close_restores_main_without_shutdown_and_saves_preferences(self):
        window = self.window()
        window.show()
        window.collapse_to_float()
        floating = window.float_log
        self.assertTrue(window.isHidden())
        self.assertTrue(floating.isVisible())
        floating.topmost.setChecked(False)
        floating.close()
        self.assertTrue(window.isVisible())
        self.assertFalse(window._closed)
        self.assertIsNone(window.float_log)
        self.assertFalse(cfg_mod.load_config()["float_log"]["topmost"])
        self.assertRegex(cfg_mod.load_config()["float_log"]["geometry"], r"\d+x\d+[+-]\d+[+-]\d+")

    def test_float_geometry_recovers_from_invalid_or_offscreen_preferences(self):
        for geometry in ("bad", "520x140+99999-99999", "520x140-10+20"):
            floating = FloatLogWindow({"geometry": geometry})
            self.addCleanup(floating.dispose)
            self.assertTrue(any(s.availableGeometry().contains(floating.geometry()) for s in self.app.screens()))

    def active_page(self, window):
        window.show_page("daily")
        page = window.pages["daily"]
        runner = SimpleNamespace(label="日常一条龙", stop_event=threading.Event(), outcome="running")
        runner.is_running = lambda: True
        runner.stop = runner.stop_event.set
        page.runner = runner
        self.runtime._active = self.runtime._last = runner
        return page, runner

    def test_float_shows_runtime_state_and_stop_requests_all_tasks(self):
        window = self.window()
        _page, runner = self.active_page(window)
        window.collapse_to_float()
        self.assertIn("日常一条龙 · 运行中", window.float_log.state_label.text())
        window.float_log.stop_button.click()
        self.assertTrue(runner.stop_event.is_set())
        self.assertIn("停止中", window.float_log.state_label.text())
        self.assertFalse(window.float_log.stop_button.isEnabled())
        window.float_log.expand_button.click()
        self.assertIsNone(window.float_log)

    def test_emergency_hotkey_and_corner_still_stop_when_main_is_hidden(self):
        window = self.window()
        _page, runner = self.active_page(window)
        window.collapse_to_float()
        window.cfg["hotkey_stop"] = "shift+Pause"
        for corner in (False, True):
            runner.stop_event.clear()
            window._hotkey_down = False
            pressed = {0x10, 0x13} if not corner else set()
            with patch("mhxy.core.emergency.get_cursor", return_value=(10, 10)), \
                 patch("mhxy.core.emergency.vk_down", side_effect=lambda vk: vk in pressed), \
                 patch("mhxy.core.emergency.in_failsafe_corner", return_value=corner):
                window._pump_launch()
            self.assertTrue(runner.stop_event.is_set())

    def test_main_shutdown_disposes_float_and_stops_pending_start(self):
        window = self.window()
        page, runner = self.active_page(window)
        page._start_pending = True
        page._cancel_event = threading.Event()
        window.collapse_to_float()
        floating = window.float_log
        window.close()
        self.assertTrue(window._closed)
        self.assertIsNone(window.float_log)
        self.assertFalse(floating.isVisible())
        self.assertTrue(page._cancel_event.is_set())
        self.assertTrue(runner.stop_event.is_set())


if __name__ == "__main__":
    unittest.main()
