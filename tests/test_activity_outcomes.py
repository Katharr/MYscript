# -*- coding: utf-8 -*-
"""活动结果离线门禁：假窗口/输入/时钟，tests 包隔离配置。"""
import threading
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest import mock

import numpy as np

from mhxy.core import rotation
from mhxy.core import scan
from mhxy.tasks import escort, treasure_map, secret_realm

CASES = [(escort, escort.EscortTask, 'escorts'),
         (treasure_map, treasure_map.TreasureMapTask, 'dug'),
         (secret_realm, secret_realm.SecretRealmTask, 'runs')]


class Window:
    def __init__(self, present=True, activates=True):
        self.present, self.activates, self.calls = present, activates, 0

    def rect(self):
        return (0, 0, 800, 600) if self.present else None

    def activate(self):
        self.calls += 1
        return self.activates


class Context:
    def __init__(self, name, windows=None, dry=False, cfg=None, stop=None):
        self.windows = windows if windows is not None else [Window()]
        self.window = self.windows[0] if self.windows else None
        self.cfg = cfg if cfg is not None else {
            'targets': {'multi': True}, 'humanize': {'interval_jitter': 0},
            'tasks': {name: {'dry_run': dry, 'regions': {}, 'templates': {},
                             'loop': {'match_threshold': .85, 'time_limit_min': .1}}}}
        self.stop = stop if stop is not None else threading.Event()
        self.log, self.mouse = mock.Mock(), mock.Mock()
        self.send_hotkey = mock.Mock(return_value=False)
        self.label = '测试窗口'

    def task_cfg(self, name):
        return self.cfg['tasks'][name]

    def should_stop(self):
        return self.stop.is_set()

    def select_windows(self):
        return self.windows

    def window_labels(self, wins):
        return ['测试窗口'] * len(wins)

    def make_child(self, window, label):
        return Context(next(iter(self.cfg['tasks'])), [window], cfg=self.cfg, stop=self.stop)


class Clock:
    now = 100.0

    def time(self):
        return self.now

    def sleep(self, *args):
        self.now += 1


class ActivityOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.clock = Clock()
        self.stack.enter_context(mock.patch.object(rotation.time, 'time', self.clock.time))
        self.stack.enter_context(mock.patch.object(rotation, '_sleep', self.clock.sleep))
        self.stack.enter_context(mock.patch('mhxy.core.inventory_maintenance.AutoInventoryMaintenance.check', return_value=True))
        self.stack.enter_context(mock.patch.object(escort.win_mod, 'grab', return_value=None))

    def task(self, cls):
        task = cls()
        task._is_admin = lambda: True
        task._load_flags = lambda tc: {}
        task._interruptible_sleep = lambda ctx, seconds: self.clock.sleep()
        task._save_capture = mock.Mock()
        return task

    def test_no_targets_and_stop_priority(self):
        for mod, cls, counter in CASES:
            with self.subTest(task=cls.name):
                ctx = Context(cls.name, [])
                task = self.task(cls)
                self.assertEqual(task.run(ctx), 'failed')
                ctx.stop.set()
                self.assertEqual(task.run(ctx), 'stopped')

    def test_missing_hotkey_done_is_failure_in_real_rotation(self):
        for mod, cls, counter in CASES:
            with self.subTest(task=cls.name):
                ctx = Context(cls.name)
                self.assertEqual(self.task(cls).run(ctx), 'failed')
                ctx.mouse.assert_not_called()

    def test_run_aggregates_five_states_using_rotation_evidence(self):
        scenarios = [('completed', 'completed', 1, 'completed'),
                     ('failed', 'completed', 0, 'failed'),
                     (None, 'timeout', 0, 'timeout'),
                     (None, 'timeout', 1, 'partial'),
                     ('completed', 'stopped', 1, 'stopped')]
        for mod, cls, counter in CASES:
            for outcome, reason, count, expected in scenarios:
                with self.subTest(task=cls.name, expected=expected):
                    ctx, task = Context(cls.name), self.task(cls)
                    def run(rc):
                        rc.termination_reason = reason
                        for rec in rc.records:
                            rec[counter] = count
                            rec['done'] = outcome is not None
                            if outcome:
                                rec['outcome'] = outcome
                    with mock.patch.object(rotation, 'run_rotation', side_effect=run):
                        self.assertEqual(task.run(ctx), expected)

    def test_mixed_windows_partial_and_completed_without_counter(self):
        for mod, cls, counter in CASES:
            with self.subTest(task=cls.name):
                ctx, task = Context(cls.name), self.task(cls)
                records = [{'done': True, 'outcome': 'completed', counter: 0},
                           {'done': True, 'outcome': 'failed', counter: 0}]
                self.assertEqual(task._activity_run_outcome(ctx, records, 'completed', counter), 'partial')
                self.assertEqual(task._activity_run_outcome(ctx, records[:1], 'completed', counter), 'completed')

    def test_window_gone_or_foreground_failure_skips_until_timeout(self):
        for mod, cls, counter in CASES:
            for window in (Window(present=False), Window(activates=False)):
                with self.subTest(task=cls.name, present=window.present):
                    ctx, task = Context(cls.name, [window]), self.task(cls)
                    task._step_once = mock.Mock()
                    self.assertEqual(task.run(ctx), 'timeout')
                    task._step_once.assert_not_called()
                    self.assertEqual(ctx.mouse.mock_calls, [])

    def test_dry_run_only_exits_on_deadline_or_stop_and_never_inputs(self):
        for mod, cls, counter in CASES:
            for stopped in (False, True):
                with self.subTest(task=cls.name, stopped=stopped):
                    ctx, task = Context(cls.name, dry=True), self.task(cls)
                    if stopped:
                        task._interruptible_sleep = lambda ctx, seconds: ctx.stop.set()
                    self.assertEqual(task.run(ctx), 'stopped' if stopped else 'timeout')
                    ctx.send_hotkey.assert_not_called()
                    self.assertEqual(ctx.mouse.mock_calls, [])

    def test_chain_tuple_step_return_and_done_remain_compatible(self):
        for mod, cls, counter in CASES:
            with self.subTest(task=cls.name):
                ctx, task = Context(cls.name), self.task(cls)
                rec, step = task.make_chain_driver(ctx)
                self.assertFalse(rec['done'])
                self.assertTrue(callable(step))
                self.assertIsNone(step())
                self.assertTrue(rec['done'])
                self.assertEqual(rec[counter], 0)
                self.assertEqual(rec['state'], mod.S_OPEN_ACTIVITY)

    def test_recovery_exhaustion_keeps_old_done_and_counter(self):
        for mod, cls, counter in CASES:
            with self.subTest(task=cls.name):
                ctx, task = Context(cls.name), self.task(cls)
                rec, step = task.make_chain_driver(ctx)
                rec[counter] = 1
                task._recover_window(ctx, rec, {'max_stuck_recover': 1}, {})
                self.assertTrue(rec['done'])
                self.assertEqual(rec[counter], 1)
                self.assertEqual(rec['outcome'], 'failed')
                self.assertEqual(task._activity_run_outcome(ctx, [rec], 'completed', counter), 'partial')

    def test_escort_normal_finish_and_trip_timeout_keep_counter(self):
        ctx, task = Context('escort'), self.task(escort.EscortTask)
        rec, step = task.make_chain_driver(ctx)
        rec.update(state=escort.S_ESCORTING, escorts=1, t_trip=0)
        task._do_escorting(ctx, rec, {'escort_timeout_sec': 1}, {}, .85)
        self.assertTrue(rec['done'])
        self.assertEqual(rec['escorts'], 1)
        self.assertEqual(rec['outcome'], 'timeout')
        self.assertEqual(task._activity_run_outcome(ctx, [rec], 'completed', 'escorts'), 'partial')
        rec, step = task.make_chain_driver(ctx)
        task._finish_escort(ctx, rec)
        self.assertEqual(task._activity_run_outcome(ctx, [rec], 'completed', 'escorts'), 'completed')

    def test_treasure_legacy_scan_without_coverage_evidence_does_not_complete(self):
        ctx, task = Context('treasure_map'), self.task(treasure_map.TreasureMapTask)
        rec, step = task.make_chain_driver(ctx)
        task.flags['treasure_item'] = np.arange(12, dtype=np.uint8).reshape(2, 2, 3)
        task._close_bag = mock.Mock()
        task._bag_visible = mock.Mock(return_value=True)
        task._recover_window = mock.Mock()
        with mock.patch.object(treasure_map.scan, 'scroll_search', return_value=scan.ScanResult('exhausted')):
            task._do_dig_find(ctx, rec, {}, {}, .85)
        self.assertFalse(rec['done'])
        self.assertEqual(rec['dug'], 0)
        task._close_bag.assert_not_called()
        task._recover_window.assert_called_once()

    def test_treasure_two_confirmed_empty_passes_complete_and_close_once(self):
        ctx, task = Context('treasure_map'), self.task(treasure_map.TreasureMapTask)
        rec, step = task.make_chain_driver(ctx)
        task.flags['treasure_item'] = np.arange(12, dtype=np.uint8).reshape(2, 2, 3)
        rec['state'] = treasure_map.S_DIG_FIND
        task._close_bag = mock.Mock()
        task._bag_visible = mock.Mock(return_value=True)
        result = scan.ScanResult('exhausted', reached_top=True, reached_end=True)
        with mock.patch.object(treasure_map.scan, 'scroll_search', return_value=result):
            task._do_dig_find(ctx, rec, {}, {}, .85)
            self.assertFalse(rec['done'])
            task._close_bag.assert_not_called()
            task._do_dig_find(ctx, rec, {}, {}, .85)
        self.assertTrue(rec['done'])
        self.assertEqual(rec['dug'], 0)
        task._close_bag.assert_called_once_with(ctx)
        self.assertEqual(task._activity_run_outcome(ctx, [rec], 'completed', 'dug'), 'completed')

    def test_treasure_stopped_scan_does_not_finish_or_close(self):
        ctx, task = Context('treasure_map'), self.task(treasure_map.TreasureMapTask)
        rec, step = task.make_chain_driver(ctx)
        task.flags['treasure_item'] = np.arange(12, dtype=np.uint8).reshape(2, 2, 3)
        task._close_bag = mock.Mock()
        task._bag_visible = mock.Mock(return_value=True)
        task._recover_window = mock.Mock()
        with mock.patch.object(treasure_map.scan, 'scroll_search', return_value=SimpleNamespace(found=False, stopped=True)):
            task._do_dig_find(ctx, rec, {}, {}, .85)
        self.assertFalse(rec['done'])
        task._close_bag.assert_not_called()

    def test_treasure_mixed_summary_reports_unconfirmed_windows(self):
        ctx, task = Context('treasure_map'), self.task(treasure_map.TreasureMapTask)
        task.make_chain_driver(ctx)
        records = []
        for label, outcome in [('已挖空号', 'completed'), ('异常号', 'failed'), ('超时号', None)]:
            child = Context('treasure_map')
            child.label = label
            rec = task._new_record(child)
            rec['state'] = treasure_map.S_DIG_FIND
            if outcome:
                rec.update(outcome=outcome, done=True)
            records.append(rec)
        task._log_summary(ctx, records, 'timeout', 2.8)
        lines = [call.args[0] for call in ctx.log.call_args_list]
        self.assertIn('已挖空号：确认挖空。', lines)
        self.assertTrue(any('异常号：异常退出，未确认挖空' in line for line in lines))
        self.assertTrue(any('超时号：超时，未确认挖空' in line for line in lines))
        self.assertIn('确认挖空 1/3 个号，其余号未确认挖空。', lines)
        self.assertNotIn('所有号均已确认挖空。', lines)

    def test_treasure_stopped_summary_preserves_completed_window(self):
        ctx, task = Context('treasure_map'), self.task(treasure_map.TreasureMapTask)
        task.make_chain_driver(ctx)
        completed, pending = task._new_record(ctx), task._new_record(ctx)
        completed.update(done=True, outcome='completed')
        ctx.stop.set()
        task._log_summary(ctx, [completed, pending], 'stopped', 1.0)
        lines = [call.args[0] for call in ctx.log.call_args_list]
        self.assertTrue(any('确认挖空。' in line for line in lines))
        self.assertTrue(any('手动停止，未确认挖空' in line for line in lines))
        self.assertIn('确认挖空 1/2 个号，其余号未确认挖空。', lines)

    def test_secret_leave_timeout_keeps_runs_but_reports_partial(self):
        ctx, task = Context('secret_realm'), self.task(secret_realm.SecretRealmTask)
        rec, step = task.make_chain_driver(ctx)
        rec.update(state=secret_realm.S_LEAVE, t_state=0)
        task._do_leave(ctx, rec, {'step_timeout_sec': 1}, {}, .85)
        self.assertTrue(rec['done'])
        self.assertEqual(rec['runs'], 1)
        self.assertEqual(task._activity_run_outcome(ctx, [rec], 'completed', 'runs'), 'partial')

    def test_secret_timeout_evidence_survives_next_round_and_resets_on_new_record(self):
        ctx, task = Context('secret_realm'), self.task(secret_realm.SecretRealmTask)
        ctx.task_cfg('secret_realm')['loop']['max_runs'] = 2
        rec, step = task.make_chain_driver(ctx)
        rec.update(state=secret_realm.S_LEAVE, t_state=0)
        task._do_leave(ctx, rec, {'step_timeout_sec': 1}, {}, .85)
        self.assertFalse(rec['done'])
        self.assertEqual(rec['state'], secret_realm.S_OPEN_ACTIVITY)
        task._match_scene = mock.Mock(return_value=(10, 20, .99))
        task._do_leave(ctx, rec, {}, {}, .85)
        self.assertEqual(rec['runs'], 2)
        self.assertEqual(task._activity_run_outcome(ctx, [rec], 'completed', 'runs'), 'partial')
        fresh, step = task.make_chain_driver(ctx)
        self.assertNotIn('leave_timed_out', fresh)
        self.assertNotIn('outcome', fresh)

    def test_escort_rejected_click_still_counts_start_and_confirm_timeout_still_monitors(self):
        ctx, task = Context('escort'), self.task(escort.EscortTask)
        rec, step = task.make_chain_driver(ctx)
        rec.update(state=escort.S_DIALOG)
        task._match_scene = mock.Mock(return_value=(10, 20, .99))
        ctx.mouse.click.return_value = False
        task._do_dialog(ctx, rec, {}, {}, .85)
        self.assertEqual(rec['escorts'], 1)
        self.assertEqual(rec['state'], escort.S_CONFIRM)
        self.assertNotIn('outcome', rec)
        task._match_scene.return_value = None
        rec['t_state'] = 0
        task._do_confirm(ctx, rec, {'confirm_timeout_sec': 1}, {}, .85)
        self.assertEqual(rec['state'], escort.S_ESCORTING)
        self.assertFalse(rec['done'])
        self.assertNotIn('outcome', rec)

    def test_secret_battle_timeout_is_normal_round_end_if_leave_clicked(self):
        ctx, task = Context('secret_realm'), self.task(secret_realm.SecretRealmTask)
        rec, step = task.make_chain_driver(ctx)
        rec.update(state=secret_realm.S_BATTLE, t_battle=0)
        task._do_battle(ctx, rec, {'battle_timeout_sec': 1}, {}, .85)
        self.assertEqual(rec['state'], secret_realm.S_LEAVE)
        task._match_scene = mock.Mock(return_value=(10, 20, .99))
        ctx.mouse.click.return_value = False  # 旧实现仍计数，不能声称输入接受/客户端确认。
        task._do_leave(ctx, rec, {}, {}, .85)
        self.assertEqual(rec['runs'], 1)
        self.assertEqual(task._activity_run_outcome(ctx, [rec], 'completed', 'runs'), 'completed')


if __name__ == '__main__':
    unittest.main()
