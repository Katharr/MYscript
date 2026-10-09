# -*- coding: utf-8 -*-
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from mhxy.core.inventory import InventoryOrganizer


class StallNoticeTests(unittest.TestCase):
    def make_organizer(self, notice=True):
        obj = InventoryOrganizer.__new__(InventoryOrganizer)
        obj.ctx = SimpleNamespace(mouse=Mock(), should_stop=Mock(return_value=False), log=Mock())
        obj.loop = {}
        obj.cfg = {}
        obj._btn = {
            "stall_notice_checkbox": SimpleNamespace(shape=(20, 140)) if notice else None,
            "stall_notice_confirm": object(),
        }
        obj._sleep = Mock()
        obj._jitter = lambda s: s
        return obj

    def test_notice_is_handled_after_shelf_before_cleanup(self):
        obj = self.make_organizer()
        events = []
        obj._click = lambda how, x, y: events.append(("step", x))
        obj._cleanup = lambda spec: events.append(("cleanup",))
        hits = {"more_button": None, "stall_sell_button": (1, 2, .99),
                "stall_shelf_button": (3, 4, .99),
                "stall_notice_checkbox": (170, 200, .99),
                "stall_notice_confirm": (300, 400, .99)}
        obj._find_full_wait = lambda key, timeout: hits[key]
        obj.ctx.mouse.click.side_effect = lambda x, y: events.append(("notice", x, y))
        self.assertTrue(obj._do_action({"name": "item"}, "stall_sell", 10, 20, .99))
        self.assertEqual(events, [("step", 10), ("step", 1), ("step", 3),
                                  ("notice", 110, 200), ("notice", 300, 400), ("cleanup",)])

    def test_absent_notice_does_not_click_confirm(self):
        obj = self.make_organizer()
        obj._find_full_wait = Mock(return_value=None)
        self.assertTrue(obj._dismiss_stall_notice())
        obj.ctx.mouse.click.assert_not_called()
        obj._find_full_wait.assert_called_once_with("stall_notice_checkbox", 1.5)

    def test_old_config_skips_notice(self):
        obj = self.make_organizer(notice=False)
        obj._find_full_wait = Mock()
        self.assertTrue(obj._dismiss_stall_notice())
        obj._find_full_wait.assert_not_called()

    def test_missing_confirm_does_not_toggle_checkbox(self):
        obj = self.make_organizer()
        obj._btn["stall_notice_confirm"] = None
        obj._find_full_wait = Mock(return_value=(170, 200, .99))
        self.assertFalse(obj._dismiss_stall_notice())
        obj.ctx.mouse.click.assert_not_called()

    def test_confirm_timeout_is_failure(self):
        obj = self.make_organizer()
        obj._find_full_wait = Mock(side_effect=[(170, 200, .99), None])
        self.assertFalse(obj._dismiss_stall_notice())
        obj.ctx.mouse.click.assert_called_once_with(110, 200)

    def test_stop_after_checkbox_prevents_confirm(self):
        obj = self.make_organizer()
        obj._find_full_wait = Mock(side_effect=[(170, 200, .99), (300, 400, .99)])
        obj.ctx.mouse.click.side_effect = lambda *args: obj.ctx.should_stop.configure_mock(return_value=True)
        self.assertFalse(obj._dismiss_stall_notice())
        obj.ctx.mouse.click.assert_called_once_with(110, 200)


if __name__ == "__main__":
    unittest.main()
