# -*- coding: utf-8 -*-
"""宝图离线回归：漏过对话、背包确认以及完整扫描后的挖空判据。"""
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
        self.ctx.window.region_to_screen_rect.return_value = (100, 200, 200, 200)
        self.task._bag_visible = mock.Mock(return_value=True)
        self.task._grab_scene = mock.Mock(return_value=None)
        self.rec = self.task._new_record(self.ctx)
        self.rec['state'] = tm.S_FIND_CARD
        self.loop = {'scroll_reset_top': False, 'scroll_max_tries': 2}
        self.scene = np.arange(20 * 20 * 3, dtype=np.uint8).reshape(20, 20, 3)
        self.task.flags['treasure_item'] = self.scene[:2, :2].copy()
        self.regions = {'bag_list': (0, 0, 200, 200)}

    def find_card(self, card):
        with mock.patch.object(tm.win_mod, 'grab', return_value=self.scene), \
             mock.patch.object(tm.list_row, 'locate_card', return_value=card) as locate:
            self.task._step_once(self.ctx, self.rec, self.loop, self.regions, 0.85)
        return locate

    def test_missing_entry_checks_bag_instead_of_recovering_or_abandoning(self):
        self.find_card(None)
        self.assertEqual(self.rec['state'], tm.S_DIG_FIND)
        self.assertTrue(self.rec['phase_b'])
        self.assertFalse(self.rec['done'])
        self.assertEqual(self.rec['recover'], 0)
        self.assertEqual(self.ctx.send_hotkey.call_args_list,
                         [mock.call('close_panel')])
        self.task._recover_window.assert_not_called()

    def test_entry_without_join_opens_bag_on_first_probe(self):
        self.loop['scroll_max_tries'] = 100
        locate = self.find_card(SimpleNamespace(join_x=None))
        locate.assert_called_once()
        self.assertEqual(self.rec['state'], tm.S_DIG_FIND)
        self.assertTrue(self.rec['phase_b'])
        self.assertEqual(self.ctx.send_hotkey.call_args_list,
                         [mock.call('close_panel')])
        self.assertFalse(self.rec['done'])
        self.ctx.mouse.scroll.assert_not_called()
        self.ctx.mouse.click.assert_not_called()
        self.task._recover_window.assert_not_called()

    def test_existing_map_continues_into_digging(self):
        self.find_card(SimpleNamespace(join_x=None))
        self.assertEqual(self.rec['state'], tm.S_DIG_FIND)
        self.assertEqual(self.ctx.send_hotkey.call_args_list,
                         [mock.call('close_panel')])
        with mock.patch.object(tm.win_mod, 'grab', return_value=self.scene), \
             mock.patch.object(tm.vision, 'match', return_value=(10, 15, 0.98)):
            self.task._step_once(self.ctx, self.rec, self.loop, self.regions, 0.85)
        self.ctx.mouse.double_click.assert_called_once_with(110, 215)
        self.assertEqual(self.rec['state'], tm.S_DIGGING)
        self.assertFalse(self.rec['done'])

    def test_empty_bag_needs_two_full_passes_after_fallback(self):
        self.find_card(None)
        with mock.patch.object(tm.win_mod, 'grab', return_value=self.scene), \
             mock.patch.object(tm.vision, 'match', return_value=None):
            self.task._step_once(self.ctx, self.rec, self.loop, self.regions, 0.85)
            self.assertFalse(self.rec['done'])
            self.assertEqual(self.rec['empty_passes'], 1)
            self.assertEqual(self.rec['state'], tm.S_DIG_FIND)
            self.ctx.send_hotkey.assert_called_once_with('close_panel')
            self.task._step_once(self.ctx, self.rec, self.loop, self.regions, 0.85)
        self.assertTrue(self.rec['done'])
        self.ctx.mouse.double_click.assert_not_called()
        self.assertEqual(self.ctx.send_hotkey.call_args_list,
                         [mock.call('close_panel'), mock.call('close_panel')])

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

    def test_battle_during_dialog_skips_wait_and_resets_recovery(self):
        self.rec.update(state=tm.S_DIALOG, recover=2, t_state=0)
        with mock.patch.object(tm.win_mod, 'grab', return_value=self.scene), \
             mock.patch.object(self.task, '_present', return_value=True), \
             mock.patch.object(self.task, '_match_scene') as match:
            self.task._do_dialog(self.ctx, self.rec, self.loop, self.regions, .85)
        self.assertEqual(self.rec['state'], tm.S_COLLECTING)
        self.assertEqual(self.rec['recover'], 0)
        match.assert_not_called()
        self.task._recover_window.assert_not_called()
        self.ctx.mouse.click.assert_not_called()

    def test_dialog_timeout_checks_existing_maps_instead_of_reopening_activity(self):
        self.rec.update(state=tm.S_DIALOG, t_state=0)
        with mock.patch.object(tm.win_mod, 'grab', return_value=self.scene):
            self.task._do_dialog(self.ctx, self.rec, self.loop, self.regions, .85)
        self.assertEqual(self.rec['state'], tm.S_DIG_OPEN_BAG)
        self.assertTrue(self.rec['phase_b'])
        self.assertFalse(self.rec['done'])
        self.task._recover_window.assert_not_called()
        self.ctx.send_hotkey.assert_not_called()

    def test_dialog_screenshot_failure_is_not_evidence_of_collection(self):
        self.rec.update(state=tm.S_DIALOG, t_state=0)
        with mock.patch.object(tm.win_mod, 'grab', return_value=None):
            self.task._do_dialog(self.ctx, self.rec, self.loop, self.regions, .85)
        self.task._recover_window.assert_called_once()
        self.assertFalse(self.rec['phase_b'])
        self.assertFalse(self.rec['done'])

    def test_already_open_bag_does_not_toggle_hotkey(self):
        self.rec['state'] = tm.S_DIG_OPEN_BAG
        self.task._do_dig_open_bag(self.ctx, self.rec, self.loop, self.regions, .85)
        self.ctx.send_hotkey.assert_not_called()
        self.assertEqual(self.rec['state'], tm.S_DIG_FIND)

    def test_closed_bag_is_opened_once_and_confirmed_on_later_step(self):
        self.rec['state'] = tm.S_DIG_OPEN_BAG
        self.task._bag_visible.return_value = False
        self.task._do_dig_open_bag(self.ctx, self.rec, self.loop, self.regions, .85)
        self.assertEqual(self.rec['state'], tm.S_DIG_WAIT_BAG)
        self.ctx.send_hotkey.assert_called_once_with('open_bag')
        self.task._do_dig_wait_bag(self.ctx, self.rec, self.loop, self.regions, .85)
        self.assertEqual(self.rec['state'], tm.S_DIG_WAIT_BAG)
        self.ctx.send_hotkey.assert_called_once_with('open_bag')
        self.task._bag_visible.return_value = True
        self.task._do_dig_wait_bag(self.ctx, self.rec, self.loop, self.regions, .85)
        self.assertEqual(self.rec['state'], tm.S_DIG_FIND)
        self.ctx.send_hotkey.assert_called_once_with('open_bag')

    def test_bag_not_opened_before_timeout_recovers_without_finishing(self):
        self.rec.update(state=tm.S_DIG_WAIT_BAG, t_state=0, empty_passes=1)
        self.task._bag_visible.return_value = False
        self.task._do_dig_wait_bag(self.ctx, self.rec, self.loop, self.regions, .85)
        self.task._recover_window.assert_called_once()
        self.assertFalse(self.rec['done'])
        self.assertEqual(self.rec['empty_passes'], 0)
        self.ctx.send_hotkey.assert_not_called()

    def test_unknown_bag_state_never_toggles_hotkey(self):
        self.task._bag_visible.return_value = None
        self.task._do_dig_open_bag(self.ctx, self.rec, self.loop, self.regions, .85)
        self.ctx.send_hotkey.assert_not_called()
        self.task._recover_window.assert_called_once()
        self.assertFalse(self.rec['done'])

    def test_battle_defers_bag_open_without_recovery(self):
        self.task._grab_scene.return_value = self.scene
        self.task._present = mock.Mock(return_value=True)
        self.rec['state'] = tm.S_DIG_OPEN_BAG
        self.task._do_dig_open_bag(self.ctx, self.rec, self.loop, self.regions, .85)
        self.ctx.send_hotkey.assert_not_called()
        self.task._recover_window.assert_not_called()
        self.assertEqual(self.rec['state'], tm.S_DIG_OPEN_BAG)

    def test_incomplete_scan_never_finishes_even_after_previous_empty_pass(self):
        results = [scan.ScanResult('exhausted', reached_top=True),
                   scan.ScanResult('exhausted', reached_end=True),
                   scan.ScanResult('stopped')]
        for result in results:
            with self.subTest(result=vars(result)):
                self.rec.update(state=tm.S_DIG_FIND, empty_passes=1)
                self.task._recover_window.reset_mock()
                with mock.patch.object(tm.scan, 'scroll_search', return_value=result) as search:
                    self.task._do_dig_find(self.ctx, self.rec, self.loop, self.regions, .85)
                self.assertFalse(self.rec['done'])
                self.assertEqual(self.rec['empty_passes'], 0)
                self.task._recover_window.assert_called_once()
                self.assertTrue(search.call_args.kwargs['require_full_scan'])
                self.assertTrue(search.call_args.kwargs['reset_to_top'])
        self.ctx.send_hotkey.assert_not_called()

    def test_second_pass_finds_map_and_continues_digging(self):
        self.rec['state'] = tm.S_DIG_FIND
        results = [scan.ScanResult('exhausted', reached_top=True, reached_end=True),
                   scan.ScanResult(scan.ACCEPT, payload=(10, 15, .98), reached_top=True)]
        with mock.patch.object(tm.scan, 'scroll_search', side_effect=results):
            self.task._do_dig_find(self.ctx, self.rec, self.loop, self.regions, .85)
            self.assertEqual(self.rec['empty_passes'], 1)
            self.task._do_dig_find(self.ctx, self.rec, self.loop, self.regions, .85)
        self.assertFalse(self.rec['done'])
        self.assertEqual(self.rec['state'], tm.S_DIGGING)
        self.assertEqual(self.rec['empty_passes'], 0)
        self.ctx.send_hotkey.assert_not_called()

    def test_screenshot_failure_during_scan_never_completes(self):
        self.rec.update(state=tm.S_DIG_FIND, empty_passes=1)
        with mock.patch.object(tm.win_mod, 'grab', return_value=None):
            self.task._do_dig_find(self.ctx, self.rec, self.loop, self.regions, .85)
        self.assertFalse(self.rec['done'])
        self.assertEqual(self.rec['empty_passes'], 0)
        self.task._recover_window.assert_called_once()
        self.ctx.mouse.double_click.assert_not_called()
        self.ctx.send_hotkey.assert_not_called()

    def test_bag_disappears_after_scan_invalidates_empty_evidence(self):
        self.rec.update(state=tm.S_DIG_FIND, empty_passes=1)
        self.task._bag_visible.side_effect = [True, False]
        result = scan.ScanResult('exhausted', reached_top=True, reached_end=True)
        with mock.patch.object(tm.scan, 'scroll_search', return_value=result):
            self.task._do_dig_find(self.ctx, self.rec, self.loop, self.regions, .85)
        self.assertFalse(self.rec['done'])
        self.assertEqual(self.rec['empty_passes'], 0)
        self.assertEqual(self.rec['state'], tm.S_DIG_OPEN_BAG)
        self.ctx.send_hotkey.assert_not_called()

    def test_hidden_bag_prevents_starting_scan(self):
        for visible in (False, None):
            with self.subTest(visible=visible):
                self.rec.update(state=tm.S_DIG_FIND, empty_passes=1)
                self.task._bag_visible.return_value = visible
                with mock.patch.object(tm.scan, 'scroll_search') as search:
                    self.task._do_dig_find(self.ctx, self.rec, self.loop, self.regions, .85)
                search.assert_not_called()
                self.assertFalse(self.rec['done'])
                self.assertEqual(self.rec['empty_passes'], 0)
                self.assertEqual(self.rec['state'], tm.S_DIG_OPEN_BAG)

    def test_double_click_rejected_does_not_count_as_using_map(self):
        self.ctx.mouse.double_click.return_value = False
        self.rec.update(state=tm.S_DIG_FIND, empty_passes=1)
        with mock.patch.object(tm.win_mod, 'grab', return_value=self.scene), \
             mock.patch.object(tm.vision, 'match', return_value=(10, 15, .98)):
            self.task._do_dig_find(self.ctx, self.rec, self.loop, self.regions, .85)
        self.assertFalse(self.rec['done'])
        self.assertNotEqual(self.rec['state'], tm.S_DIGGING)
        self.assertEqual(self.rec['empty_passes'], 0)
        self.task._recover_window.assert_called_once()

    def test_missing_or_outside_bag_region_never_confirms_empty(self):
        for region, rect in [(None, (100, 200, 200, 200)),
                             ((0, 0, 200, 200), (0, 0, 200, 200))]:
            with self.subTest(region=region, rect=rect):
                self.rec.update(state=tm.S_DIG_FIND, empty_passes=1)
                self.task._recover_window.reset_mock()
                self.ctx.window.region_to_screen_rect.return_value = rect
                regions = {'bag_list': region} if region else {}
                self.task._do_dig_find(self.ctx, self.rec, self.loop, regions, .85)
                self.assertFalse(self.rec['done'])
                self.assertEqual(self.rec['empty_passes'], 0)
                self.task._recover_window.assert_called_once()
        self.ctx.mouse.double_click.assert_not_called()

    def test_bag_visibility_distinguishes_hidden_from_unobservable(self):
        visible = tm.TreasureMapTask._bag_visible
        self.assertIsNone(visible(self.task, self.ctx, .85))
        self.task.flags['flag_bag_open'] = np.ones((2, 2, 3), dtype=np.uint8)
        for scene in (None, np.array([], dtype=np.uint8),
                      np.zeros((20, 20, 3), dtype=np.uint8),
                      np.full((20, 20, 3), (255, 0, 0), dtype=np.uint8)):
            with self.subTest(invalid_scene=None if scene is None else scene.shape):
                with mock.patch.object(tm.win_mod, 'grab', return_value=scene):
                    self.assertIsNone(visible(self.task, self.ctx, .85))
        textured = np.arange(20 * 20 * 3, dtype=np.uint8).reshape(20, 20, 3)
        with mock.patch.object(tm.win_mod, 'grab', return_value=textured), \
             mock.patch.object(tm.vision, 'match', return_value=None) as match:
            self.assertIs(visible(self.task, self.ctx, .85), False)
            match.return_value = (10, 15, .98)
            self.assertIs(visible(self.task, self.ctx, .85), True)

    def test_battle_detection_uses_separate_verified_threshold(self):
        self.task.flags['flag_battle'] = np.ones((2, 2, 3), dtype=np.uint8)
        with mock.patch.object(tm.vision, 'match', return_value=(10, 15, .819)) as match:
            self.assertTrue(self.task._in_battle(self.scene, {}, .85))
            self.assertEqual(match.call_args.args[2], .80)
            self.assertTrue(self.task._in_battle(self.scene, {'battle_threshold': .81}, .85))
            self.assertEqual(match.call_args.args[2], .81)

    def test_missing_item_template_cannot_be_mistaken_for_empty_bag(self):
        self.task.flags['treasure_item'] = None
        self.rec.update(state=tm.S_DIG_FIND, empty_passes=1)
        with mock.patch.object(tm.scan, 'scroll_search') as search:
            self.task._do_dig_find(self.ctx, self.rec, self.loop, self.regions, .85)
        search.assert_not_called()
        self.task._recover_window.assert_called_once()
        self.assertFalse(self.rec['done'])
        self.assertEqual(self.rec['empty_passes'], 0)
        self.ctx.send_hotkey.assert_not_called()

    def test_template_larger_than_bag_capture_is_not_a_valid_empty_scan(self):
        self.task.flags['treasure_item'] = np.tile(self.scene, (2, 2, 1))
        self.rec.update(state=tm.S_DIG_FIND, empty_passes=1)
        with mock.patch.object(tm.win_mod, 'grab', return_value=self.scene), \
             mock.patch.object(tm.vision, 'match', return_value=None) as match:
            self.task._do_dig_find(self.ctx, self.rec, self.loop, self.regions, .85)
        match.assert_not_called()
        self.task._recover_window.assert_called_once()
        self.assertFalse(self.rec['done'])
        self.assertEqual(self.rec['empty_passes'], 0)
        self.ctx.send_hotkey.assert_not_called()

    def test_close_bag_rejected_recovers_without_reporting_completed(self):
        self.rec.update(state=tm.S_DIG_FIND, empty_passes=1)
        self.task._close_bag = mock.Mock(return_value=False)
        result = scan.ScanResult('exhausted', reached_top=True, reached_end=True)
        with mock.patch.object(tm.scan, 'scroll_search', return_value=result):
            self.task._do_dig_find(self.ctx, self.rec, self.loop, self.regions, .85)
        self.task._close_bag.assert_called_once_with(self.ctx)
        self.task._recover_window.assert_called_once()
        self.assertFalse(self.rec['done'])
        self.assertNotIn('outcome', self.rec)

    def test_stop_before_scan_does_not_recover_or_input(self):
        self.ctx.should_stop.return_value = True
        self.task.flags['treasure_item'] = None
        with mock.patch.object(tm.scan, 'scroll_search') as search:
            self.task._do_dig_find(self.ctx, self.rec, self.loop, self.regions, .85)
        search.assert_not_called()
        self.task._recover_window.assert_not_called()
        self.ctx.send_hotkey.assert_not_called()
        self.assertFalse(self.rec['done'])

    def test_stop_during_close_does_not_recover_or_report_completed(self):
        self.rec.update(state=tm.S_DIG_FIND, empty_passes=1)
        def stop_during_close(ctx):
            self.ctx.should_stop.return_value = True
            return False
        self.task._close_bag = mock.Mock(side_effect=stop_during_close)
        result = scan.ScanResult('exhausted', reached_top=True, reached_end=True)
        with mock.patch.object(tm.scan, 'scroll_search', return_value=result):
            self.task._do_dig_find(self.ctx, self.rec, self.loop, self.regions, .85)
        self.task._recover_window.assert_not_called()
        self.assertFalse(self.rec['done'])
        self.assertNotIn('outcome', self.rec)

    def test_battle_starting_while_waiting_for_bag_defers_without_toggling(self):
        self.rec.update(state=tm.S_DIALOG, t_state=0, recover=1)
        with mock.patch.object(tm.win_mod, 'grab', return_value=self.scene):
            self.task._do_dialog(self.ctx, self.rec, self.loop, self.regions, .85)
        self.assertEqual(self.rec['state'], tm.S_DIG_OPEN_BAG)
        self.task._bag_visible.return_value = False
        self.task._do_dig_open_bag(self.ctx, self.rec, self.loop, self.regions, .85)
        self.assertEqual(self.rec['state'], tm.S_DIG_WAIT_BAG)
        self.ctx.send_hotkey.assert_called_once_with('open_bag')
        self.task._grab_scene.return_value = self.scene
        with mock.patch.object(self.task, '_present', return_value=True):
            self.task._do_dig_wait_bag(self.ctx, self.rec, self.loop, self.regions, .85)
            self.assertEqual(self.rec['state'], tm.S_DIG_OPEN_BAG)
            self.task._do_dig_open_bag(self.ctx, self.rec, self.loop, self.regions, .85)
        self.ctx.send_hotkey.assert_called_once_with('open_bag')
        self.task._recover_window.assert_not_called()
        self.assertEqual(self.rec['recover'], 1)
        self.task._bag_visible.return_value = True
        self.task._do_dig_open_bag(self.ctx, self.rec, self.loop, self.regions, .85)
        self.assertEqual(self.rec['state'], tm.S_DIG_FIND)
        self.ctx.send_hotkey.assert_called_once_with('open_bag')
        self.assertFalse(self.rec['done'])

    def test_rejected_next_map_click_does_not_increment_count(self):
        self.rec.update(state=tm.S_DIGGING, dug=2)
        self.ctx.mouse.click.return_value = False
        with mock.patch.object(tm.win_mod, 'grab', return_value=self.scene), \
             mock.patch.object(self.task, '_match_scene', return_value=(110, 215, .98)):
            self.task._do_digging(self.ctx, self.rec, self.loop, self.regions, .85)
        self.ctx.mouse.click.assert_called_once_with(110, 215)
        self.assertEqual(self.rec['dug'], 2)
        self.assertFalse(self.rec['done'])
        self.task._recover_window.assert_called_once()

    def test_bag_template_prefers_dedicated_marker(self):
        self.ctx.task_cfg.return_value = {'templates': {'flag_bag_open': 'bag.png'}}
        self.ctx.cfg = {'tasks': {'organize_bag': {'templates': {'sort_button': 'sort.png'}}}}
        marker = self.scene[:2, :2]
        with mock.patch.object(tm.vision, 'load_template', return_value=marker) as load:
            self.assertIs(self.task._bag_template(self.ctx), marker)
        load.assert_called_once_with('bag.png')

    def test_unreadable_dedicated_bag_template_reuses_existing_sort_marker(self):
        self.ctx.task_cfg.return_value = {'templates': {'flag_bag_open': 'missing.png'}}
        self.ctx.cfg = {'tasks': {'organize_bag': {'templates': {'sort_button': 'sort.png'}}}}
        marker = self.scene[:2, :2]
        with mock.patch.object(tm.vision, 'load_template', side_effect=[None, marker]) as load:
            self.assertIs(self.task._bag_template(self.ctx), marker)
        self.assertEqual(load.call_args_list, [mock.call('missing.png'), mock.call('sort.png')])

    def test_bag_template_uses_existing_sort_marker_without_new_calibration(self):
        self.ctx.task_cfg.return_value = {'templates': {}}
        self.ctx.cfg = {'tasks': {'organize_bag': {'templates': {'sort_button': 'sort.png'}}}}
        marker = self.scene[:2, :2]
        with mock.patch.object(tm.vision, 'load_template', return_value=marker) as load:
            self.assertIs(self.task._bag_template(self.ctx), marker)
        load.assert_called_once_with('sort.png')

    def test_bag_template_without_either_marker_remains_unobservable(self):
        self.ctx.task_cfg.return_value = {'templates': {}}
        self.ctx.cfg = {'tasks': {}}
        with mock.patch.object(tm.vision, 'load_template') as load:
            self.assertIsNone(self.task._bag_template(self.ctx))
        load.assert_not_called()


if __name__ == '__main__':
    unittest.main()
