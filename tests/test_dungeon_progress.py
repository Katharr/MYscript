"""队长副本运行回放：合成模板、截图与输入，无游戏操作。"""
import unittest
from unittest import mock

import numpy as np

from mhxy.tasks.taohaiqu import TaohaiquTask, _FLAG_KEYS
from tests.test_dungeon_outcome import _Clock, _Context, _Window


class DungeonProgressTests(unittest.TestCase):
    def setUp(self):
        self.clock = _Clock()
        self.ctx = _Context([_Window()])
        self.ctx.cfg["tasks"]["taohaiqu"] = {
            "dry_run": False, "skip_team": True, "auto_disband": False,
            "loop": {"match_threshold": .85, "entry_skip_sec": 15,
                     "step_timeout_sec": 10, "battle_timeout_sec": 15}, "regions": {}}
        self.ctx.make_child = lambda *args: self.ctx
        rng = np.random.default_rng(71)
        self.flags = {k: rng.integers(0, 256, (10, 18, 3), dtype=np.uint8) for k in _FLAG_KEYS}
        self.key = "thq_select"
        self.round = 0
        self.skip_clicks = 0
        self.lose_first_skip = True
        self.reject_all = False
        self.stop_after_skip = False
        self.ignore_clock = False
        self.task = TaohaiquTask()
        # 回放从已经选中活动、到达 NPC 开始，省去无关的活动列表滚动。
        self.task._open_and_join = mock.Mock(return_value=True)
        self.task._click_enter = mock.Mock(side_effect=lambda *a: setattr(self, "key", "thq_skip") or True)
        self.task._load_flags = mock.Mock(return_value=self.flags)
        self.task._is_admin = mock.Mock(return_value=True)
        self.task._interruptible_sleep = lambda *args: self.clock.sleep()
        self.ctx.mouse.click.side_effect = self.click
        self.ctx.send_hotkey.side_effect = self.hotkey

    def grab(self, rect):
        scene = np.zeros((600, 800, 3), dtype=np.uint8)
        if self.key:
            scene[100:110, 200:218] = self.flags[self.key]
        return scene

    def hotkey(self, key):
        if key == "open_task" and self.key is None:
            self.key = "thq_task"
        return True

    def click(self, *args):
        if self.reject_all:
            return False
        if self.key == "thq_select":
            self.key = "thq_enter"
        elif self.key == "thq_skip":
            self.skip_clicks += 1
            if self.stop_after_skip:
                self.ctx.stop.set()
            if not self.lose_first_skip or self.skip_clicks > 1:
                self.key = None
                self.round += 1
        elif self.key == "thq_task":
            self.key = "thq_teleport"
        elif self.key == "thq_teleport":
            self.key = "thq_opt%d" % self.round
        elif self.key and self.key.startswith("thq_opt"):
            self.key = "thq_skip"
        elif self.key == "thq_clock":
            if not self.ignore_clock:
                self.key = None
        elif self.key is None and self.round == 4:
            self.key = "thq_clock"
        return True

    def run_replay(self):
        with mock.patch("mhxy.tasks.taohaiqu.time.time", self.clock.time), \
                mock.patch("mhxy.tasks.taohaiqu.win_mod.grab", side_effect=self.grab):
            return self.task.run(self.ctx)

    def test_missed_skip_is_retried_and_entire_dungeon_finishes(self):
        self.assertEqual(self.run_replay(), "completed")
        self.assertEqual(self.round, 4)
        self.assertEqual(self.skip_clicks, 5)

    def test_unresponsive_clock_is_not_reported_as_completed(self):
        self.ignore_clock = True
        self.assertEqual(self.run_replay(), "partial")

    def test_skip_that_never_responds_is_bounded(self):
        original = self.click
        def click(*args):
            if self.key == "thq_skip":
                self.skip_clicks += 1
                return True
            return original(*args)
        self.ctx.mouse.click.side_effect = click
        self.assertEqual(self.run_replay(), "partial")
        self.assertEqual(self.skip_clicks, 3)
        self.ctx.send_hotkey.assert_not_called()

    def test_stop_after_skip_does_not_open_task_or_retry(self):
        self.stop_after_skip = True
        self.assertEqual(self.run_replay(), "stopped")
        self.assertEqual(self.skip_clicks, 1)
        self.ctx.send_hotkey.assert_not_called()

    def test_rejected_input_cannot_complete_dungeon(self):
        self.reject_all = True
        self.assertEqual(self.run_replay(), "timeout")
        self.assertEqual(self.round, 0)

    def test_stale_task_list_is_not_proof_that_battle_has_finished(self):
        self.task._click_enter.side_effect = lambda *args: setattr(self, "key", "thq_task") or True
        self.assertEqual(self.run_replay(), "partial")
        self.assertEqual(self.skip_clicks, 0)
        self.assertEqual(self.round, 0)

    def test_missed_task_selection_is_retried_when_teleport_not_yet_available(self):
        original = self.click
        missed = []
        def click(*args):
            if self.key == "thq_task" and not missed:
                missed.append(True)
                return True
            return original(*args)
        self.ctx.mouse.click.side_effect = click
        self.assertEqual(self.run_replay(), "completed")
        self.assertEqual(missed, [True])

    def test_missed_teleport_and_dialog_are_retried(self):
        original = self.click
        missed = set()
        def click(*args):
            if self.key in ("thq_teleport", "thq_opt1") and self.key not in missed:
                missed.add(self.key)
                return True
            return original(*args)
        self.ctx.mouse.click.side_effect = click
        self.assertEqual(self.run_replay(), "completed")
        self.assertEqual(missed, {"thq_teleport", "thq_opt1"})

    def test_slow_task_window_is_not_toggled_closed_while_loading(self):
        original = self.grab
        original_hotkey = self.hotkey
        opened = []
        def hotkey(key):
            if key == "open_task":
                opened.append(self.round)
                return True
            return original_hotkey(key)
        pending_frames = []
        def grab(rect):
            if self.key is None and self.round in opened and self.round < 4:
                pending_frames.append(self.round)
                if pending_frames.count(self.round) >= 4:
                    self.key = "thq_task"
            return original(rect)
        self.ctx.send_hotkey.side_effect = hotkey
        self.grab = grab
        self.assertEqual(self.run_replay(), "completed")
        self.assertEqual(opened, [1, 2, 3])

    def test_existing_teleport_button_does_not_skip_conservative_task_reselection(self):
        original_grab, original_click = self.grab, self.click
        missed, wrong_teleport = [], []
        def grab(rect):
            scene = original_grab(rect)
            if self.key == "thq_task":
                scene[100:110, 400:418] = self.flags["thq_teleport"]
            return scene
        def click(x, y):
            if self.key == "thq_task":
                if x > 350:
                    wrong_teleport.append(True)
                    self.ctx.stop.set()
                    return True
                if not missed:
                    missed.append(True)
                    return True
            return original_click(x, y)
        self.grab = grab
        self.ctx.mouse.click.side_effect = click
        self.assertEqual(self.run_replay(), "completed")
        self.assertEqual(wrong_teleport, [])
