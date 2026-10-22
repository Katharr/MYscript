# -*- coding: utf-8 -*-
"""真实整理/扫描/动作编排，虚拟物品和按钮；不向游戏输入。"""
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest import mock

import numpy as np

from mhxy.core import config, inventory
from mhxy.tasks.organize_bag import OrganizeBagTask


class StallFullTests(unittest.TestCase):
    def setUp(self):
        self.items = ["stall_one", "use_item", "shop_item", "stall_two"]
        self.events = []
        self.attempts = 0
        self.full_attempts = {1, 2}
        self.full_popup = False
        self.current_stall = None
        self.ctx = SimpleNamespace(cfg={"humanize": {}}, input_session=None,
            mouse=mock.Mock(), window=mock.Mock(), log=mock.Mock(),
            send_hotkey=mock.Mock(return_value=True), should_stop=mock.Mock(return_value=False))
        self.ctx.window.rect.return_value = (0, 0, 800, 600)
        self.ctx.window.region_to_screen_rect.return_value = (0, 0, 200, 200)
        self.ctx.mouse.click.side_effect = self.click
        self.ctx.mouse.double_click.side_effect = self.use
        self.ctx.mouse.right_click.side_effect = self.close
        self.ctx.mouse.scroll.return_value = True
        self.positions = {"stall_one": (10, 10), "use_item": (20, 20),
                          "shop_item": (30, 30), "stall_two": (40, 40),
                          "stall_sell_button": (110, 110), "stall_shelf_button": (120, 120),
                          "shop_sell_button": (130, 130), "sell_full_button": (140, 140),
                          "sell_confirm_button": (150, 150), "sort_button": (160, 160)}
        self.ob = {"items": [{"name": key, "template": key,
            "action": "stall_sell" if key.startswith("stall_") else
                      "shop_sell" if key.startswith("shop_") else "use"} for key in self.items],
            "regions": {"bag_list": (0, 0, 200, 200)},
            "loop": {"passes": 3, "step_find_timeout_sec": 0, "optional_find_timeout_sec": 0},
            "templates": {key: key for key in self.positions if key.endswith("button")}}
        self.ob["templates"]["stall_full_notice"] = "stall_full_notice"
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.object(inventory.vision, "load_template", side_effect=lambda path: path))
        self.stack.enter_context(mock.patch.object(inventory.vision, "match", side_effect=self.match))
        self.stack.enter_context(mock.patch.object(inventory.win_mod, "grab", side_effect=self.grab))
        self.organizer = inventory.InventoryOrganizer(self.ctx, self.ob, dry_run=False)
        self.organizer._sleep = mock.Mock()

    def grab(self, rect):
        frame = np.full((8, 10, 3), len(self.items) * 10, dtype=np.uint8)
        frame[::2, ::2] += 4
        return frame

    def match(self, frame, template, threshold):
        if template == "stall_full_notice":
            return (200, 200, .99) if self.full_popup else None
        if template in self.positions:
            if template in ("stall_one", "stall_two", "use_item", "shop_item") and template not in self.items:
                return None
            if template in ("shop_sell_button", "sell_full_button", "sell_confirm_button") and "shop_item" not in self.items:
                return None
            return (*self.positions[template], .99)
        return None

    def click(self, x, y):
        key = next(key for key, pos in self.positions.items() if pos == (x, y))
        self.events.append(key)
        if key in ("stall_one", "stall_two"):
            self.attempts += 1
            self.current_stall = key
        elif key == "stall_sell_button":
            self.full_popup = self.attempts in self.full_attempts
        elif key == "stall_shelf_button":
            self.items.remove(self.current_stall)
        elif key == "sell_confirm_button":
            self.items.remove("shop_item")
        return True

    def use(self, x, y):
        self.events.append("use_item")
        self.items.remove("use_item")
        return True

    def close(self, x, y):
        self.events.append("close_stall")
        self.full_popup = False
        return True

    def test_full_stall_is_closed_other_items_finish_then_exactly_one_retry(self):
        self.assertEqual(self.organizer.organize(), (2, "done"))
        self.assertEqual(self.attempts, 2)
        self.assertEqual(self.items, ["stall_one", "stall_two"])
        first, last = [i for i, event in enumerate(self.events) if event == "stall_one"]
        self.assertLess(first, self.events.index("close_stall"))
        self.assertLess(self.events.index("close_stall"), self.events.index("use_item"))
        self.assertLess(self.events.index("use_item"), last)
        self.assertLess(self.events.index("sell_confirm_button"), last)
        self.assertLess(last, self.events.index("sort_button"))
        self.assertEqual(self.events.count("close_stall"), 2)
        self.assertTrue(self.organizer.evidence["stall_full"])
        self.assertEqual(self.organizer.evidence["action_failures"], 0)
        self.assertEqual(self.ctx.send_hotkey.call_args_list, [mock.call("open_bag"), mock.call("close_panel")])

    def test_retry_recovered_capacity_sells_once_and_ends_without_third_attempt(self):
        self.full_attempts = {1}
        self.assertEqual(self.organizer.organize(), (3, "done"))
        self.assertEqual(self.attempts, 2)
        self.assertEqual(self.items, ["stall_two"])
        self.assertFalse(self.organizer.evidence["stall_full"])

    def test_full_notice_while_waiting_for_button_stops_before_any_shelf_click(self):
        self.full_popup = True
        self.assertFalse(self.organizer._do_action({"name": "one"}, "stall_sell", 10, 10, .99))
        self.assertNotIn("stall_sell_button", self.events)
        self.assertNotIn("stall_shelf_button", self.events)
        self.assertEqual(self.events[-1], "close_stall")
        self.assertTrue(self.organizer._stall_blocked)

    def test_full_after_shelf_is_not_counted_as_sale(self):
        original_click = self.click
        def full_after_shelf(x, y):
            if (x, y) == (120, 120):
                self.events.append("stall_shelf_button")
                self.full_popup = True
                return True
            return original_click(x, y)
        self.full_attempts = set()
        self.ctx.mouse.click.side_effect = full_after_shelf
        self.assertFalse(self.organizer._do_action({"name": "one"}, "stall_sell", 10, 10, .99))
        self.assertIn("stall_one", self.items)
        self.assertTrue(self.organizer.evidence["stall_full"])
        self.assertEqual(self.events[-1], "close_stall")

    def test_stop_after_other_items_prevents_retry_and_final_input(self):
        original_use = self.use
        def stop_after_use(*args):
            result = original_use(*args)
            self.ctx.should_stop.return_value = True
            return result
        self.ctx.mouse.double_click.side_effect = stop_after_use
        handled, reason = self.organizer.organize()
        self.assertEqual(reason, "stopped")
        self.assertEqual(self.attempts, 1)
        self.ctx.send_hotkey.assert_called_once_with("open_bag")

    def test_dry_run_does_not_detect_full_or_open_stall(self):
        self.organizer.dry_run = True
        self.organizer.organize()
        self.assertEqual(self.events, [])
        self.ctx.send_hotkey.assert_not_called()
        self.assertFalse(self.organizer.evidence["stall_full"])

    def test_without_full_notice_old_config_keeps_normal_selling(self):
        self.full_attempts = set()
        self.organizer._btn['stall_full_notice'] = None
        self.assertEqual(self.organizer.organize(), (4, "done"))
        self.assertEqual(self.attempts, 2)
        self.assertEqual(self.items, [])
        self.assertFalse(self.organizer.evidence['stall_full'])
        self.assertFalse(any('最后再尝试' in c.args[0] for c in self.ctx.log.call_args_list))

    def test_failed_stall_cleanup_does_not_continue_or_retry(self):
        self.ctx.mouse.right_click.side_effect = None
        self.ctx.mouse.right_click.return_value = False
        with self.assertRaises(inventory.InventoryRecoveryError):
            self.organizer.organize()
        self.assertEqual(self.attempts, 1)
        self.assertNotIn('use_item', self.events)
        self.assertNotIn('sell_confirm_button', self.events)
        self.assertNotIn('sort_button', self.events)

    def test_full_status_is_reset_for_the_next_inventory_run(self):
        self.organizer.organize()
        self.assertTrue(self.organizer.evidence['stall_full'])
        self.full_attempts = set()
        self.assertEqual(self.organizer.organize(), (2, 'done'))
        self.assertEqual(self.items, [])
        self.assertFalse(self.organizer.evidence['stall_full'])

    def test_calibration_and_default_template_are_registered(self):
        entries = dict((key, label) for key, label, *_ in OrganizeBagTask.CALIBRATION["templates"])
        self.assertEqual(entries["stall_full_notice"], "「摊位已满」提示")
        self.assertIn("stall_full_notice", config.DEFAULT_CONFIG["tasks"]["organize_bag"]["templates"])


if __name__ == '__main__':
    unittest.main()
