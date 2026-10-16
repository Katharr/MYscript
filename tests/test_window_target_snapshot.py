"""固定目标及逐窗状态上报的核心回归，不依赖 Qt 或真实游戏。"""

import copy
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import patch

from mhxy.core import config as cfg_mod, window as win_mod
from mhxy.core.input_session import InputSession
from mhxy.core.rotation import RotationConfig, _drive_until_yield
from mhxy.tasks.escort import EscortTask
from mhxy.tasks.treasure_map import TreasureMapTask


def fake_window(hwnd):
    return SimpleNamespace(_win=SimpleNamespace(_hWnd=hwnd), rect=lambda: [0, 0, 100, 100])


class WindowSnapshotTests(unittest.TestCase):
    def test_explicit_handles_keep_snapshot_order_and_exclude_new_window(self):
        wins = [fake_window(h) for h in (3, 2, 1)]
        with patch.object(win_mod, "locate_all", return_value=wins):
            selected = win_mod.resolve_targets("test", [0, 0], {"window_handles": [1, 2]})
        self.assertEqual([w._win._hWnd for w in selected], [1, 2])

    def test_missing_snapshot_member_never_compresses_captain_index_or_falls_back(self):
        with patch.object(win_mod, "locate_all", return_value=[fake_window(2), fake_window(3)]):
            self.assertEqual(win_mod.resolve_targets("test", [0, 0], {"window_handles": [1, 2]}), [])

    def test_explicit_empty_is_empty_while_legacy_empty_multi_still_means_all(self):
        wins = [fake_window(1), fake_window(2)]
        with patch.object(win_mod, "locate_all", return_value=wins):
            self.assertEqual(win_mod.resolve_targets("test", [0, 0], {"selected_hwnds": []}), [])
            self.assertEqual(win_mod.resolve_targets("test", [0, 0], {"multi": True, "multi_indices": []}), wins)

    def test_failed_activation_and_preflight_do_not_publish_activity(self):
        events = []
        session = InputSession({}, threading.Event(), "escort", activity_fn=lambda *a: events.append(a))
        win = fake_window(1)
        with patch.object(session, "_foreground", return_value=True), session.activate(preflight=True):
            session.activated(win, True)
        self.assertEqual(events, [])
        with patch.object(session, "_foreground", return_value=False), session.activate():
            session.activated(win, False)
        self.assertEqual(events, [])
        with patch.object(session, "_foreground", return_value=True), session.activate():
            session.activated(win, True)
        self.assertEqual(events, [(1, "运镖中…", "success")])

    def test_chain_drivers_report_their_own_business_for_each_bound_window(self):
        cfg = copy.deepcopy(cfg_mod.DEFAULT_CONFIG)
        events = []
        session = InputSession(cfg, threading.Event(), "daily", activity_fn=lambda *a: events.append(a))
        for hwnd, task_cls in ((1, EscortTask), (2, TreasureMapTask)):
            ctx = SimpleNamespace(window=fake_window(hwnd), input_session=session, cfg=cfg,
                                  task_cfg=lambda name: cfg_mod.task_config(cfg, name))
            task = task_cls()
            with patch.object(task, "_load_flags", return_value={}), patch.object(task, "_step_once") as step:
                rec, drive = task.make_chain_driver(ctx)
                with session.activate():
                    drive()
                step.assert_called_once()
        self.assertEqual(events, [(1, "运镖中…", "success"), (2, "挖宝中…", "success")])

    def test_rotation_completion_reports_only_finished_role(self):
        events = []
        session = InputSession({}, threading.Event(), "escort", activity_fn=lambda *a: events.append(a))
        ctx = SimpleNamespace(window=fake_window(1), input_session=session)
        rec = dict(ctx=ctx, state="work", done=False)
        rotation = RotationConfig([rec], lambda r: r.update(done=True, outcome="completed"),
                                  lambda: False, lambda *a, **kw: None)
        with session.activate():
            _drive_until_yield(rotation, rec)
        self.assertEqual(events, [(1, "已完成", "")])


if __name__ == "__main__":
    unittest.main()
