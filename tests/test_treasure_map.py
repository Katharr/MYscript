# -*- coding: utf-8 -*-
"""宝图参加识别失败时，复用背包挖宝流程而非放弃该号。"""
import unittest
from types import SimpleNamespace
from unittest import mock

import numpy as np

from mhxy.core import scan
from mhxy.tasks import treasure_map as tm


class TreasureMapFallbackTests(unittest.TestCase):
    def setUp(self):
        self.task = tm.TreasureMapTask()
        self.task._skip_collect = False
        self.task._start_state = tm.S_OPEN_ACTIVITY
        self.task.flags = {}
        self.task._focus = mock.Mock()
        self.task._calib_size = mock.Mock(return_value=None)
        self.task._jitter = lambda delay, ctx: delay
        self.task._interruptible_sleep = mock.Mock()
        self.task._recover_window = mock.Mock()
        self.ctx = mock.Mock()
        self.ctx.should_stop.return_value = False
        self.ctx.send_hotkey.return_value = True
        self.ctx.window.rect.return_value = (100, 200, 800, 600)
        self.rec = self.task._new_record(self.ctx)
        self.rec['state'] = tm.S_FIND_CARD
        self.loop = {'scroll_reset_top': False, 'scroll_max_tries': 2}
        self.scene = np.zeros((20, 20, 3), dtype=np.uint8)

    def find_card(self, card):
        with mock.patch.object(tm.win_mod, 'grab', return_value=self.scene), \
             mock.patch.object(tm.list_row, 'locate_card', return_value=card):
            self.task._step_once(self.ctx, self.rec, self.loop, {}, 0.85)

    def test_missing_entry_checks_bag_instead_of_recovering_or_abandoning(self):
        self.find_card(None)
        self.assertEqual(self.rec['state'], tm.S_DIG_OPEN_BAG)
        self.assertTrue(self.rec['phase_b'])
        self.assertFalse(self.rec['done'])
        self.assertEqual(self.rec['recover'], 0)
        self.ctx.send_hotkey.assert_called_once_with('close_panel')
        self.task._recover_window.assert_not_called()

    def test_entry_without_join_checks_bag_after_in_place_retries(self):
        self.find_card(SimpleNamespace(join_x=None))
        self.assertEqual(self.rec['state'], tm.S_DIG_OPEN_BAG)
        self.assertFalse(self.rec['done'])
        self.ctx.mouse.scroll.assert_not_called()
        self.ctx.mouse.click.assert_not_called()
        self.task._recover_window.assert_not_called()

    def test_existing_map_continues_into_digging(self):
        self.find_card(SimpleNamespace(join_x=None))
        self.task._step_once(self.ctx, self.rec, self.loop, {}, 0.85)
        self.assertEqual(self.rec['state'], tm.S_DIG_FIND)
        self.assertEqual(self.ctx.send_hotkey.call_args_list,
                         [mock.call('close_panel'), mock.call('open_bag')])
        with mock.patch.object(tm.win_mod, 'grab', return_value=self.scene), \
             mock.patch.object(tm.vision, 'match', return_value=(10, 15, 0.98)):
            self.task._step_once(self.ctx, self.rec, self.loop, {}, 0.85)
        self.ctx.mouse.double_click.assert_called_once_with(110, 215)
        self.assertEqual(self.rec['state'], tm.S_DIGGING)
        self.assertFalse(self.rec['done'])

    def test_empty_bag_closes_and_finishes_after_fallback(self):
        self.find_card(None)
        self.task._step_once(self.ctx, self.rec, self.loop, {}, 0.85)
        with mock.patch.object(tm.win_mod, 'grab', return_value=self.scene), \
             mock.patch.object(tm.vision, 'match', return_value=None):
            self.task._step_once(self.ctx, self.rec, self.loop, {}, 0.85)
        self.assertTrue(self.rec['done'])
        self.ctx.mouse.double_click.assert_not_called()
        self.assertEqual(self.ctx.send_hotkey.call_args_list,
                         [mock.call('close_panel'), mock.call('open_bag'),
                          mock.call('close_panel')])

    def test_successful_join_keeps_collecting_path(self):
        self.find_card(SimpleNamespace(join_x=300, join_y=400,
                                       anchor_score=0.95, join_score=0.9))
        self.ctx.mouse.click.assert_called_once_with(300, 400)
        self.ctx.send_hotkey.assert_not_called()
        self.assertEqual(self.rec['state'], tm.S_DIALOG)
        self.assertFalse(self.rec['phase_b'])

    def test_stopped_scan_does_not_close_activity_or_open_bag(self):
        with mock.patch.object(tm.scan, 'scroll_search',
                               return_value=scan.ScanResult('stopped')):
            self.task._step_once(self.ctx, self.rec, self.loop, {}, 0.85)
        self.ctx.send_hotkey.assert_not_called()
        self.assertEqual(self.rec['state'], tm.S_FIND_CARD)
        self.assertFalse(self.rec['done'])

    def test_stop_while_closing_activity_does_not_continue(self):
        self.ctx.send_hotkey.side_effect = lambda key: setattr(
            self.ctx.should_stop, 'return_value', True) or True
        self.find_card(None)
        self.ctx.send_hotkey.assert_called_once_with('close_panel')
        self.assertEqual(self.rec['state'], tm.S_FIND_CARD)
        self.assertFalse(self.rec['phase_b'])


if __name__ == '__main__':
    unittest.main()
