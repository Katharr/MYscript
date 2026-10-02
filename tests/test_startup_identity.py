# -*- coding: utf-8 -*-
"""已开游戏的启动识别回归：模拟唤窗，不启动 GUI，不读真实游戏资料。"""
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from start import _wake_existing_windows
from mhxy.core import accounts, window as win_mod
from mhxy.gui.app import App, GeneralPage


class StartupIdentityTests(unittest.TestCase):
    def setUp(self):
        self.pending = []
        self.app = SimpleNamespace(
            cfg={'window_title': 'game', 'window_offset': [1, 2]},
            ui_post=self.pending.append, log_line=Mock(), _kick_locate=Mock(),
            _game_connected=(True, '号1 · 单开'))

    def drain(self):
        for callback in self.pending:
            callback()

    def test_each_woken_window_identified_before_ui_refresh(self):
        wins = [object(), object()]
        events = []

        def wake(title, offset, on_woken):
            self.assertEqual((title, offset), ('game', [1, 2]))
            for win in wins:
                events.append(('wake', win))
                on_woken(win)
            return 2, 2

        with patch.object(win_mod, 'wake_all_to_front', side_effect=wake), \
             patch.object(accounts, 'labels_for', side_effect=lambda wins: events.append(('identify', wins[0]))):
            _wake_existing_windows(self.app)
        self.assertEqual(events, [('wake', wins[0]), ('identify', wins[0]),
                                  ('wake', wins[1]), ('identify', wins[1])])
        self.app.log_line.assert_not_called()
        self.app._kick_locate.assert_not_called()
        self.assertEqual(len(self.pending), 1)
        self.drain()
        self.assertIsNone(self.app._game_connected)
        self.app._kick_locate.assert_called_once_with()

    def test_visible_windows_use_same_scan_without_minimized_gate(self):
        win = Mock()
        win._win.isMinimized = False
        def wake(title, offset, on_woken):
            on_woken(win)
            return 1, 1
        with patch.object(win_mod, 'wake_all_to_front', side_effect=wake), \
             patch.object(accounts, 'labels_for', return_value=['示例角色（50）']) as labels:
            _wake_existing_windows(self.app)
        labels.assert_called_once_with([win])
        self.drain()
        self.app._kick_locate.assert_called_once_with()

    def test_identity_exception_isolates_one_window_and_refreshes_ui(self):
        wins = [object(), object()]
        def wake(title, offset, on_woken):
            for win in wins:
                on_woken(win)
            return 2, 2
        with patch.object(win_mod, 'wake_all_to_front', side_effect=wake), \
             patch.object(accounts, 'labels_for', side_effect=[RuntimeError('OCR unavailable'), ['示例角色']]) as labels:
            _wake_existing_windows(self.app)
        self.assertEqual(labels.call_count, 2)
        self.drain()
        self.app._kick_locate.assert_called_once_with()
        self.assertTrue(any(call.args[1] == 'warn' for call in self.app.log_line.call_args_list))

    def test_no_windows_does_not_trigger_ocr_or_ui_refresh(self):
        with patch.object(win_mod, 'wake_all_to_front', return_value=(0, 0)), \
             patch.object(accounts, 'labels_for') as labels:
            _wake_existing_windows(self.app)
        self.drain()
        labels.assert_not_called()
        self.app._kick_locate.assert_not_called()
        self.app.log_line.assert_not_called()

    def test_wake_exception_reported_on_ui_queue(self):
        with patch.object(win_mod, 'wake_all_to_front', side_effect=RuntimeError('test error')):
            _wake_existing_windows(self.app)
        self.app.log_line.assert_not_called()
        self.drain()
        self.app.log_line.assert_called_once()
        self.assertEqual(self.app.log_line.call_args.args[1], 'warn')

    def test_full_role_order_drives_target_summary_and_general_page(self):
        wins = [SimpleNamespace(_win=SimpleNamespace(_hWnd=101)),
                SimpleNamespace(_win=SimpleNamespace(_hWnd=102))]
        names = {101: {'label': '示例甲（50）'}, 102: {'label': '示例乙（60）'}}
        targets = {'multi': True, 'multi_indices': [0, 1]}
        with patch.object(accounts, '_identity', copy.deepcopy(names)), \
             patch.object(accounts, '_cache', {'order': ['示例甲（50）']}), \
             patch.object(accounts, '_compute') as compute:
            connected, summary = App._compute_target_state(wins, targets)
            self.assertTrue(connected)
            self.assertEqual(summary, '示例甲（50）、示例乙（60） · 多开')
            self.assertEqual(GeneralPage._targets_summary({'targets': targets}),
                             '多开 · 示例甲（50）、示例乙（60）')
            self.assertEqual(accounts.cached_labels_for([wins[1]]), ['示例乙（60）'])
            self.assertEqual(accounts.cached_labels(), ['示例甲（50）', '示例乙（60）'])
            compute.assert_not_called()


if __name__ == '__main__':
    unittest.main()
