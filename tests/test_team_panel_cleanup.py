"""组队/退队交接：真实运行入口，合成画面和记录输入，不操作游戏。"""
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest import mock

import numpy as np

from mhxy.core import rotation, teaming
from mhxy.core.input_session import InputSession
from tests.test_dungeon_outcome import _Clock, _Context, _Window


class TeamPanelCleanupTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.clock = _Clock()
        self.stack.enter_context(mock.patch.object(rotation.time, "time", self.clock.time))
        self.stack.enter_context(mock.patch.object(rotation, "_sleep", self.clock.sleep))
        rng = np.random.default_rng(42)
        self.templates = {key: rng.integers(0, 256, (12, 24, 3), dtype=np.uint8)
                          for key in ("friend_panel", "team_create", "team_quit", "team_apply")}
        self.stack.enter_context(mock.patch.object(teaming.TeamFormation, "_load_templates",
                                                  return_value=self.templates))
        self.stack.enter_context(mock.patch.object(teaming.TeamFormation, "_diag_dump"))
        self.ctx = _Context([_Window(), _Window()])
        self.ctx.cfg["tasks"]["teaming"]["loop"]["form_timeout_sec"] = 30
        self.children = [self.ctx.make_child(w, "窗口") for w in self.ctx.windows]
        self.panels = ["team_apply", "friend_panel"]
        self.reject = False
        self.capture_ok = True
        self.inputs = []
        for i, child in enumerate(self.children):
            child.mouse.right_click.side_effect = lambda *a, i=i: self.close(i)
            child.send_hotkey.side_effect = lambda key, i=i: self.hotkey(i, key)
        self.stack.enter_context(mock.patch.object(teaming.win_mod, "grab", side_effect=self.grab))
        for i, window in enumerate(self.ctx.windows):
            window.activate = lambda i=i: self.activate(i)
        self.current = 0
        self.team = teaming.TeamFormation(self.ctx, list(zip(self.children, ("captain", "member"))),
                                         self.ctx.task_cfg("teaming"))
        self.team._sleep = self.clock.sleep

    def activate(self, index):
        self.current = index
        return True

    def grab(self, rect):
        if not self.capture_ok:
            return None
        scene = np.zeros((600, 800, 3), dtype=np.uint8)
        key = self.panels[self.current]
        if key:
            scene[80:92, 100:124] = self.templates[key]
        return scene

    def close(self, index):
        self.inputs.append((index, "close"))
        if self.reject:
            return False
        self.panels[index] = None
        return True

    def hotkey(self, index, key):
        if key == "close_panel":
            return self.close(index)
        self.inputs.append((index, key))
        return True

    def ready_to_close(self):
        # 重放握手已发出申请，下一轮分别关闭两个面板的现场。
        self.team.joined = 1
        for rec, state in zip(self.team.records, (teaming.C_CLOSE, teaming.M_CLOSE)):
            rec.update(state=state, t_state=self.clock.time(), panel_opened=True)

    def test_rejected_close_never_releases_formed_team(self):
        self.ready_to_close()
        self.reject = True
        ok, _ = self.team.run_until_formed()
        self.assertFalse(ok)
        self.assertNotEqual(self.team.formation_outcome, "completed")
        self.assertTrue(self.inputs)
        self.assertLessEqual(len(self.inputs), 6)

    def test_formed_team_is_released_only_after_both_panels_disappear(self):
        self.ready_to_close()
        self.assertEqual(self.team.run_until_formed(), (True, "formed"))
        self.assertEqual(self.panels, [None, None])

    def test_switching_windows_uses_new_capture_before_real_input_gate(self):
        self.ready_to_close()
        session = InputSession(self.ctx.cfg, self.ctx.stop, "dungeon")
        session._foreground = lambda window: True
        for index, child in enumerate(self.children):
            child.window._win = SimpleNamespace(_hWnd=index + 1)
            child.mouse = session.bind_mouse(child, child.mouse)
            def activate(index=index, window=child.window):
                self.activate(index)
                session.activated(window, True)
                return True
            child.window.activate = activate
        def capture(rect):
            token = session.capture_token()
            scene = self.grab(rect)
            session.observed(token, rect, scene is not None)
            return scene
        with session.activate(), mock.patch.object(teaming.win_mod, "grab", side_effect=capture):
            self.assertTrue(self.team.run_until_formed()[0])
        self.assertEqual(self.inputs, [(0, "close"), (1, "close")])

    def test_failed_leader_search_also_closes_friend_panel(self):
        self.team.records[0].update(state=teaming.C_CLOSE)
        self.team.records[1].update(state=teaming.M_FIND_LEADER, t_state=self.clock.time())
        self.assertFalse(self.team.run_until_formed()[0])
        self.assertIsNone(self.panels[1])

    def test_capture_failure_is_not_proof_of_closed_panel_and_sends_no_input(self):
        self.ready_to_close()
        self.capture_ok = False
        self.assertFalse(self.team.run_until_formed()[0])
        self.assertEqual(self.inputs, [])

    def test_capture_exception_does_not_prevent_other_windows_from_closing(self):
        for rec in self.team.records:
            rec["panel_opened"] = True
        self.ctx.cfg["tasks"]["teaming"]["loop"]["form_timeout_sec"] = 1
        def grab(rect):
            if self.current == 0:
                raise OSError("synthetic capture failure")
            return self.grab(rect)
        with mock.patch.object(teaming.win_mod, "grab", side_effect=grab):
            self.assertFalse(self.team.run_until_formed()[0])
        self.assertEqual(self.panels, ["team_apply", None])

    def test_activation_exception_in_cleanup_does_not_skip_other_windows(self):
        for rec in self.team.records:
            rec["panel_opened"] = True
        self.ctx.windows[0].activate = mock.Mock(side_effect=OSError("synthetic activation failure"))
        with self.assertRaises(OSError):
            self.team.run_until_formed()
        self.assertIsNone(self.panels[1])

    def test_stop_does_not_attempt_cleanup(self):
        self.ready_to_close()
        self.ctx.stop.set()
        self.assertEqual(self.team.run_until_formed(), (False, "stopped"))
        self.assertEqual(self.inputs, [])

    def test_formation_timeout_still_closes_opened_panels(self):
        self.ctx.cfg["tasks"]["teaming"]["loop"]["form_timeout_sec"] = 1
        for rec in self.team.records:
            rec["panel_opened"] = True
        # 两个窗口仍在等待阶段，模拟总时限先到。
        self.team.records[0]["state"] = teaming.C_ACCEPT
        self.assertFalse(self.team.run_until_formed()[0])
        self.assertEqual(self.panels, [None, None])

    def test_disband_requires_closed_team_panel_after_solo_confirmation(self):
        self.panels = ["team_create", "team_create"]
        self.reject = True
        self.ctx.cfg["tasks"]["teaming"]["loop"]["disband_timeout_sec"] = 40
        self.assertFalse(self.team.run_disband()[0])
        self.assertNotEqual(self.team.disband_outcome, "completed")

    def test_disband_recovers_remaining_friend_panel_and_closes_team_panel(self):
        self.panels = ["friend_panel", "friend_panel"]
        self.ctx.cfg["tasks"]["teaming"]["loop"]["disband_timeout_sec"] = 40
        previous = self.hotkey
        def hotkey(index, key):
            result = previous(index, key)
            if key == "open_team":
                self.panels[index] = "team_create"
            return result
        self.hotkey = hotkey
        self.assertTrue(self.team.run_disband()[0])
        self.assertEqual(self.panels, [None, None])

    def test_disband_timeout_closes_opened_panel_without_claiming_solo(self):
        self.team.tpl = {k: v for k, v in self.templates.items() if k != "friend_panel"}
        self.ctx.cfg["tasks"]["teaming"]["loop"]["disband_timeout_sec"] = 1
        previous = self.hotkey
        def hotkey(index, key):
            result = previous(index, key)
            if key == "open_team":
                self.panels[index] = "team_quit"
            return result
        self.hotkey = hotkey
        self.panels = [None, None]
        self.assertFalse(self.team.run_disband()[0])
        self.assertEqual(self.panels, [None, None])
