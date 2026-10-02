# -*- coding: utf-8 -*-
"""秒装备行为测试：不接触游戏、不发真实输入、不写真实配置。"""
import copy
import unittest
from unittest.mock import Mock, patch

from mhxy.core import config
from mhxy.tasks.sniper import SniperTask

HIT = (20, 30, 0.95)


class SniperTests(unittest.TestCase):
    def setUp(self):
        self.task = SniperTask()
        self.tc = copy.deepcopy(config.DEFAULT_CONFIG['tasks']['sniper'])
        self.tc['dry_run'] = False
        self.tc['regions']['purchase'] = [0, 0, 1000, 800]
        self.tc['templates'] = {'sniper_product': 'product.png', 'sniper_buy': 'buy.png',
                                'sniper_buy_confirm': 'buy_confirm.png', 'sniper_success': 'success.png'}
        self.ctx = Mock()
        self.ctx.cfg = {'humanize': {}}
        self.ctx.window.region_center_screen.return_value = (10, 10)
        self.ctx.should_stop.return_value = False
        self.ctx.send_hotkey.return_value = True
        self.rec = {'ctx': self.ctx, 'label': '测试窗口', 'state': '识别中', 'count': 0,
                    'done': False, 'ready_at': 0, 'clear_deadline': 0, 'dry_logged': False}
        self.sleep = patch.object(self.task, '_interruptible_sleep').start()
        self.addCleanup(patch.stopall)
        self.task._jitter = lambda seconds, ctx: seconds

    def matches(self, *hits):
        self.task._match = Mock(side_effect=[(h, (100, 200, 300, 400)) for h in hits])

    def step(self):
        self.task._step(self.rec, self.tc, 'product', 'buy', 'buy_confirm', 'success')

    def test_missing_product_closes_then_opens_shop_without_buying(self):
        self.matches(None, None)
        self.step()
        self.assertEqual(self.rec['state'], '等待关闭')
        self.ctx.send_hotkey.assert_called_once_with('open_shop')
        self.ctx.mouse.click.assert_not_called()
        self.rec['ready_at'] = 0
        self.step()
        self.assertEqual(self.rec['state'], '等待商品页')
        self.assertEqual(self.ctx.send_hotkey.call_count, 2)
        self.rec['ready_at'] = 0
        self.step()
        self.assertEqual(self.rec['state'], '识别中')
        self.assertEqual(self.rec['count'], 0)

    def test_success_counts_once_and_stops_at_target(self):
        self.matches(None, HIT, HIT, HIT, HIT)
        self.step()
        self.assertEqual(self.rec['count'], 1)
        self.assertTrue(self.rec['done'])
        self.assertEqual(self.rec['state'], '已完成')
        self.assertEqual(self.ctx.mouse.click.call_count, 3)
        self.ctx.mouse.click.assert_any_call(120, 230, speed=3.0)
        self.ctx.mouse.click.assert_any_call(120, 230, speed=3.0)
        self.step()
        self.assertEqual(self.rec['count'], 1)
        self.assertEqual(self.ctx.mouse.click.call_count, 3)
        self.ctx.send_hotkey.assert_not_called()

    def test_first_purchase_button_must_match_immediately(self):
        self.matches(None, HIT, None)
        self.step()
        self.assertEqual(self.rec['state'], '第一次购买按钮未匹配')
        self.assertEqual(self.ctx.mouse.click.call_count, 1)
        self.ctx.stop.assert_called_once()

    def test_second_purchase_button_must_match_immediately(self):
        self.matches(None, HIT, HIT, None)
        self.step()
        self.assertEqual(self.rec['state'], '第二次购买按钮未匹配')
        self.assertEqual(self.ctx.mouse.click.call_count, 2)
        self.ctx.stop.assert_called_once()

    def test_old_success_never_counts_and_blocks_next_purchase(self):
        self.tc['target_count'] = 2
        self.matches(None, HIT, HIT, HIT, HIT, HIT, HIT)
        self.step()
        self.assertEqual(self.rec['count'], 1)
        self.assertFalse(self.rec['done'])
        self.step()
        self.step()
        self.assertEqual(self.rec['count'], 1)
        self.assertEqual(self.ctx.mouse.click.call_count, 3)
        self.ctx.stop.assert_not_called()

    def test_two_purchases_require_old_toast_to_disappear(self):
        self.tc['target_count'] = 2
        self.matches(None, HIT, HIT, HIT, HIT, None, HIT, HIT, HIT, HIT)
        self.step()
        self.step()
        self.assertEqual(self.rec['count'], 2)
        self.assertTrue(self.rec['done'])
        self.assertEqual(self.ctx.mouse.click.call_count, 6)

    def test_result_timeout_stops_without_counting_or_retrying(self):
        self.tc['loop']['purchase_timeout_sec'] = 0
        self.matches(None, HIT, HIT, HIT, None)
        self.step()
        self.ctx.stop.assert_called_once()
        self.assertEqual(self.rec['state'], '结果未确认')
        self.assertEqual(self.rec['count'], 0)
        self.ctx.send_hotkey.assert_not_called()

    def test_dry_run_never_inputs_or_counts(self):
        self.tc['dry_run'] = True
        self.matches(None, HIT)
        self.step()
        self.assertEqual(self.rec['count'], 0)
        self.ctx.mouse.click.assert_not_called()
        self.ctx.send_hotkey.assert_not_called()

    def test_stop_after_select_prevents_buy(self):
        self.matches(None, HIT)
        self.ctx.should_stop.side_effect = [False, True]
        self.step()
        self.assertEqual(self.ctx.mouse.click.call_count, 1)
        self.assertEqual(self.rec['count'], 0)

    def test_windows_have_independent_counts(self):
        self.tc['target_count'] = 2
        other = dict(self.rec)
        self.matches(None, HIT, HIT, HIT, HIT)
        self.step()
        self.assertEqual(other['count'], 0)
        self.task._publish([self.rec, other], 2)
        self.assertEqual([r[1] for r in self.task.progress], [1, 0])

    def test_screenshot_failure_is_not_treated_as_no_stock(self):
        self.ctx.detection_rect.return_value = (0, 0, 100, 100)
        with patch('mhxy.tasks.sniper.win_mod.grab', return_value=None):
            with self.assertRaisesRegex(RuntimeError, '截图失败'):
                self.step()
        self.ctx.send_hotkey.assert_not_called()
        self.ctx.mouse.click.assert_not_called()

    def test_preflight_requires_templates_button_and_positive_quantity(self):
        self.ctx.task_cfg.return_value = self.tc
        self.ctx.hotkeys = {'open_shop': ['alt', 'a']}
        self.ctx.select_windows.return_value = [Mock()]
        with patch('mhxy.tasks.sniper.vision.load_template', return_value=object()):
            self.assertEqual(self.task.preflight(self.ctx), (True, []))
            for invalid in (0, -1, True, 1.5, '2'):
                self.tc['target_count'] = invalid
                self.assertFalse(self.task.preflight(self.ctx)[0])
        self.tc['target_count'] = 1
        self.tc['regions']['purchase'] = None
        with patch('mhxy.tasks.sniper.vision.load_template', return_value=None):
            ok, problems = self.task.preflight(self.ctx)
        self.assertFalse(ok)
        self.assertEqual(len(problems), 4)

    def test_run_completes_each_window_and_preserves_foreground_during_result(self):
        self.tc['target_count'] = 1
        self.ctx.task_cfg.return_value = self.tc
        self.ctx.cfg['targets'] = {'multi': True}
        self.ctx.hotkeys = {'open_shop': ['alt', 'a']}
        self.ctx.label = '窗口A'
        other = Mock()
        other.cfg = self.ctx.cfg
        other.hotkeys = self.ctx.hotkeys
        other.label = '窗口B'
        other.should_stop.return_value = False
        other.window.rect.return_value = (0, 0, 100, 100)
        other.window.activate.return_value = True
        other.window.region_center_screen.return_value = (10, 10)
        self.ctx.window.rect.return_value = (0, 0, 100, 100)
        self.ctx.window.activate.return_value = True
        self.matches(None, HIT, HIT, HIT, HIT, None, HIT, HIT, HIT, HIT)
        with patch.object(self.task, '_resolve_contexts', return_value=[self.ctx, other]), \
             patch('mhxy.tasks.sniper.vision.load_template', return_value=object()), \
             patch('mhxy.core.rotation._sleep'):
            self.task.run(self.ctx)
        self.assertEqual([row[1:] for row in self.task.progress],
                         [(1, 1, '已完成'), (1, 1, '已完成')])
        self.assertEqual(self.ctx.mouse.click.call_count, 3)
        self.assertEqual(other.mouse.click.call_count, 3)
        self.assertEqual(self.ctx.window.activate.call_count, 1)
        self.assertEqual(other.window.activate.call_count, 1)
        self.ctx.maybe_auto_organize.assert_not_called()
        other.maybe_auto_organize.assert_not_called()

    def test_foreground_failure_sends_no_inputs(self):
        from mhxy.core.rotation import RotationConfig, run_rotation
        other_ctx = Mock()
        other_ctx.window.rect.return_value = (0, 0, 100, 100)
        other_ctx.window.activate.return_value = False
        other_ctx.should_stop.return_value = False
        rec = {'ctx': other_ctx, 'state': '识别中', 'done': False}
        step = Mock()
        with patch('mhxy.core.rotation._sleep'):
            run_rotation(RotationConfig([rec], step, Mock(side_effect=[False, False, True, True]), Mock()))
        step.assert_not_called()
        other_ctx.mouse.click.assert_not_called()
        other_ctx.send_hotkey.assert_not_called()

    def test_legacy_migration_preserves_product_and_other_tasks(self):
        original = copy.deepcopy(config.DEFAULT_CONFIG)
        original['tasks']['sniper'].update({'dry_run': False, 'watchlist': [
            {'name': '旧商品', 'template': 'old.png'}]})
        original['tasks']['sniper']['regions']['category_button'] = [1, 2, 3, 4]
        original['tasks']['sniper']['regions']['confirm_button'] = [1, 2, 3, 4]
        untouched = copy.deepcopy(original['tasks']['escort'])
        migrated = config._normalize_sniper(original)
        tc = migrated['tasks']['sniper']
        self.assertTrue(tc['dry_run'])
        self.assertEqual(tc['templates']['sniper_product'], 'old.png')
        self.assertNotIn('watchlist', tc)
        self.assertNotIn('category_button', tc['regions'])
        self.assertNotIn('confirm_button', tc['regions'])
        self.assertEqual(migrated['tasks']['escort'], untouched)


if __name__ == '__main__':
    unittest.main()
