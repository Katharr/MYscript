# -*- coding: utf-8 -*-
"""真实整理/滚动循环配合虚拟背包，验证局部补查、预算与无进展保护；不操作游戏。"""
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest import mock

import numpy as np

from mhxy.core import inventory


class InventoryLocalRecheckTests(unittest.TestCase):
    def setUp(self):
        self.page = 1
        self.bottom = 3
        self.items = {"one": 1, "two": 1, "hidden": None}
        self.events = []
        self.revision = 0
        self.now = 0.0
        self.stop = False
        self.on_action = None
        self.ctx = SimpleNamespace(
            cfg={}, input_session=None, mouse=mock.Mock(), window=mock.Mock(),
            log=mock.Mock(), send_hotkey=mock.Mock(return_value=True),
            should_stop=lambda: self.stop)
        self.ctx.window.rect.return_value = (0, 0, 100, 80)
        self.ctx.mouse.scroll.side_effect = self.scroll
        self.ob = {"items": [{"name": name, "template": name, "action": "shop_sell"}
                             for name in self.items],
                   "loop": {"scroll_reset_top": False, "passes": 5}, "templates": {}}
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.object(inventory.vision, "load_template", side_effect=lambda p: p))
        self.stack.enter_context(mock.patch.object(inventory.vision, "match", side_effect=self.match))
        self.stack.enter_context(mock.patch.object(inventory.win_mod, "grab", side_effect=self.grab))
        self.stack.enter_context(mock.patch.object(inventory.time, "monotonic", side_effect=lambda: self.now))
        self.organizer = inventory.InventoryOrganizer(self.ctx, self.ob, dry_run=False)
        self.organizer._sleep = mock.Mock()
        self.organizer._do_action = mock.Mock(side_effect=self.action)
        self.organizer._click_sort_button = mock.Mock()

    def grab(self, rect):
        value = (self.page * 30 + self.revision * 5) % 230
        frame = np.full((8, 10, 3), value, dtype=np.uint8)
        frame[::2, ::2] += 4
        return frame

    def match(self, scene, template, threshold):
        if template in self.items and self.items[template] == self.page:
            return (10, 10 + list(self.items).index(template), .99)
        return None

    def scroll(self, direction, *args, **kwargs):
        self.events.append(("scroll", direction))
        self.page = max(0, min(self.bottom, self.page + (1 if direction < 0 else -1)))
        return True

    def action(self, item, *args):
        name = item["template"]
        self.events.append(("action", name))
        if self.on_action:
            return self.on_action(name)
        del self.items[name]
        self.revision += 1
        if name == "one" and "hidden" in self.items:
            self.items["hidden"] = 0  # 出售后漏到当前视野上方。
        return True

    def test_finish_current_screen_then_recheck_once_and_continue_down(self):
        self.assertEqual(self.organizer.organize(), (3, "done"))
        self.assertEqual(self.events[:4], [("action", "one"), ("action", "two"),
                                          ("scroll", 1), ("action", "hidden")])
        self.assertEqual([e for e in self.events if e[0] == "scroll"],
                         [("scroll", 1)] + [("scroll", -3)] * 4)
        self.assertFalse(self.organizer.evidence["scan_limited"])
        self.organizer._click_sort_button.assert_called_once()
        # 即使旧配置指定多遍，也不能追加整包复查。
        self.assertEqual(self.ctx.send_hotkey.call_args_list,
                         [mock.call("open_bag"), mock.call("close_panel")])

    def test_no_targets_or_only_failed_actions_do_not_trigger_recheck(self):
        for failed in (False, True):
            with self.subTest(failed=failed):
                self.page = 1
                self.events.clear()
                self.items = {"one": 1} if failed else {}
                self.organizer._do_action.side_effect = lambda *_: False
                self.assertEqual(self.organizer.organize(), (0, "done"))
                self.assertTrue(all(direction < 0 for _, direction in self.events))
                self.assertEqual(self.organizer.evidence["action_failures"], int(failed))

    def test_recheck_with_no_target_does_not_repeat_or_start_full_second_scan(self):
        self.items = {"two": 1}
        self.assertEqual(self.organizer.organize(), (1, "done"))
        self.assertEqual(self.events.count(("scroll", 1)), 1)
        self.assertEqual(self.events.count(("action", "two")), 1)

    def test_more_than_thirty_actions_still_get_full_scroll_budget(self):
        self.items = {f"item{i}": 1 for i in range(40)}
        self.ob["items"] = [{"template": name, "action": "use"} for name in self.items]
        self.ob["loop"]["scroll_max_tries"] = 4
        self.organizer._item_tpls = [(item, item["template"]) for item in self.ob["items"]]
        self.assertEqual(self.organizer.organize(), (40, "done"))
        self.assertFalse(self.organizer.evidence["scan_limited"])
        self.assertEqual(self.events.count(("scroll", 1)), 1)
        self.assertEqual(self.page, self.bottom)

    def test_unchanged_item_stops_without_repeated_actions_or_sort(self):
        self.items = {"one": 1}
        self.on_action = lambda _: True  # 按钮序列完成，但物品和画面并没有变。
        self.assertEqual(self.organizer.organize(), (3, "done"))
        self.assertEqual(self.organizer._do_action.call_count, 3)
        self.assertTrue(self.organizer.evidence["scan_limited"])
        self.assertEqual(self.ctx.mouse.scroll.call_count, 0)
        self.organizer._click_sort_button.assert_not_called()
        self.assertTrue(any("整理背包未完成" in c.args[0] for c in self.ctx.log.call_args_list))
        self.assertEqual(self.ctx.send_hotkey.call_args_list[-1], mock.call("close_panel"))

    def test_small_stack_quantity_changes_are_progress_even_below_scroll_threshold(self):
        self.items = {"one": 1}
        base = self.grab(None)
        def stack_frame(rect):
            frame = base.copy()
            frame[0, 0, 0] += self.revision
            return frame
        def use_stack(name):
            self.revision += 1
            if self.revision == 5:
                del self.items[name]
            return True
        self.on_action = use_stack
        with mock.patch.object(inventory.win_mod, "grab", side_effect=stack_frame):
            self.assertEqual(self.organizer.organize(), (5, "done"))
        self.assertFalse(self.organizer.evidence["scan_limited"])

    def test_duration_limit_stops_before_next_action_and_keeps_remaining_items(self):
        self.ob["loop"]["organize_timeout_sec"] = 1
        def slow_action(name):
            del self.items[name]
            self.now += 2
            return True
        self.on_action = slow_action
        self.assertEqual(self.organizer.organize(), (1, "done"))
        self.assertIn("two", self.items)
        self.assertTrue(self.organizer.evidence["scan_limited"])
        self.organizer._click_sort_button.assert_not_called()
        self.ctx.mouse.scroll.assert_not_called()

    def test_scroll_limit_still_stops_and_reports_incomplete(self):
        self.items = {}
        self.bottom = 100
        self.ob["loop"]["scroll_max_tries"] = 2
        self.assertEqual(self.organizer.organize(), (0, "done"))
        self.assertTrue(self.organizer.evidence["scan_limited"])
        self.assertLess(self.page, self.bottom)
        self.organizer._click_sort_button.assert_not_called()

    def test_recheck_scroll_denial_closes_bag_and_propagates(self):
        self.items = {"two": 1}
        self.ctx.mouse.scroll.side_effect = lambda *_args, **_kwargs: False
        with self.assertRaisesRegex(inventory.InventoryRecoveryError, "局部回看"):
            self.organizer.organize()
        self.ctx.send_hotkey.assert_has_calls([mock.call("open_bag"), mock.call("close_panel")])
        self.organizer._click_sort_button.assert_not_called()

    def test_stop_after_action_does_not_recheck_or_close_bag(self):
        def stop_after_action(name):
            self.stop = True
            return True
        self.on_action = stop_after_action
        self.assertEqual(self.organizer.organize()[1], "stopped")
        self.ctx.mouse.scroll.assert_not_called()
        self.ctx.send_hotkey.assert_called_once_with("open_bag")

    def test_dry_run_never_scrolls_or_handles_items(self):
        self.organizer.dry_run = True
        self.assertEqual(self.organizer.organize(), (1, "done"))
        self.ctx.mouse.scroll.assert_not_called()
        self.organizer._do_action.assert_not_called()
        self.ctx.send_hotkey.assert_not_called()


if __name__ == "__main__":
    unittest.main()
