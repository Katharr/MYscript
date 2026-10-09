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
        obj.dry_run = False
        obj.ctx.mouse.click.return_value = True
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
        obj._cleanup = Mock(side_effect=lambda spec: events.append(("cleanup",)))
        hits = {"more_button": None, "stall_sell_button": (1, 2, .99),
                "stall_shelf_button": (3, 4, .99),
                "stall_notice_checkbox": (170, 200, .99),
                "stall_notice_confirm": (300, 400, .99)}
        obj._find_full_wait = lambda key, timeout: hits[key]
        obj.ctx.mouse.click.side_effect = lambda x, y: events.append(("click", x, y)) or True
        self.assertTrue(obj._do_action({"name": "item"}, "stall_sell", 10, 20, .99))
        self.assertEqual(events, [("click", 10, 20), ("click", 1, 2), ("click", 3, 4),
                                  ("click", 110, 200), ("click", 300, 400), ("cleanup",)])
        obj._cleanup.assert_called_once()

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


    def test_checkbox_rejection_never_finds_or_clicks_confirm(self):
        obj = self.make_organizer()
        obj._find_full_wait = Mock(return_value=(170, 200, .99))
        obj.ctx.mouse.click.return_value = False
        self.assertFalse(obj._dismiss_stall_notice())
        obj.ctx.mouse.click.assert_called_once_with(110, 200)
        obj._find_full_wait.assert_called_once_with("stall_notice_checkbox", 1.5)
        obj._sleep.assert_not_called()
        obj.ctx.log.assert_not_called()

    def test_confirm_rejection_is_failure_without_success_log(self):
        obj = self.make_organizer()
        obj._find_full_wait = Mock(side_effect=[(170, 200, .99), (300, 400, .99)])
        obj.ctx.mouse.click.side_effect = [True, False]
        self.assertFalse(obj._dismiss_stall_notice())
        self.assertEqual(obj.ctx.mouse.click.call_count, 2)
        obj._sleep.assert_called_once()
        obj.ctx.log.assert_called_once_with("摆摊上架提示：勾选「7天不再提示」。", level="hit")

    def test_notice_failure_or_exception_cleans_up_once(self):
        for notice_result in (False, RuntimeError("notice capture failed")):
            with self.subTest(result=notice_result):
                obj = self.make_organizer()
                obj._cleanup = Mock()
                obj._find_full_wait = Mock(side_effect=[None, (1, 2, .99), (3, 4, .99)])
                obj._dismiss_stall_notice = Mock()
                if isinstance(notice_result, Exception):
                    obj._dismiss_stall_notice.side_effect = notice_result
                    with self.assertRaises(RuntimeError):
                        obj._do_action({"name": "item"}, "stall_sell", 10, 20, .99)
                else:
                    obj._dismiss_stall_notice.return_value = notice_result
                    self.assertFalse(obj._do_action({"name": "item"}, "stall_sell", 10, 20, .99))
                obj._cleanup.assert_called_once()

    def test_non_stall_action_never_handles_stall_notice(self):
        obj = self.make_organizer()
        obj._cleanup = Mock()
        obj._dismiss_stall_notice = Mock()
        self.assertTrue(obj._do_action({"name": "item"}, "use", 10, 20, .99))
        obj._dismiss_stall_notice.assert_not_called()
        obj._cleanup.assert_called_once()

    def test_dry_run_or_stop_never_probes_or_inputs(self):
        for dry_run, stopped in ((True, False), (False, True)):
            with self.subTest(dry_run=dry_run, stopped=stopped):
                obj = self.make_organizer()
                obj.dry_run = dry_run
                obj.ctx.should_stop.return_value = stopped
                obj._find_full_wait = Mock()
                obj._cleanup = Mock()
                self.assertFalse(obj._dismiss_stall_notice())
                self.assertFalse(obj._do_action({"name": "item"}, "stall_sell", 10, 20, .99))
                obj._find_full_wait.assert_not_called()
                obj.ctx.mouse.click.assert_not_called()
                obj._cleanup.assert_not_called()

    def test_stop_during_notice_prevents_finally_cleanup(self):
        obj = self.make_organizer()
        obj._cleanup = Mock()
        obj._find_full_wait = Mock(side_effect=[None, (1, 2, .99), (3, 4, .99), (170, 200, .99)])
        def click(x, y):
            if x == 110:
                obj.ctx.should_stop.return_value = True
            return True
        obj.ctx.mouse.click.side_effect = click
        self.assertFalse(obj._do_action({"name": "item"}, "stall_sell", 10, 20, .99))
        self.assertEqual(obj.ctx.mouse.click.call_count, 4)
        obj._cleanup.assert_not_called()


if __name__ == "__main__":
    unittest.main()
