# -*- coding: utf-8 -*-
"""第二阶段运行入口纯逻辑门禁；不截图、不注入、不写真实配置。"""
import threading
import time
import unittest
from unittest import mock

from mhxy.core.input_session import InputSession
from mhxy.core.runtime import RuntimeService


class _Runner:
    def __init__(self, running=False):
        self.stop_event = threading.Event()
        self.outcome = "running" if running else "idle"
        self.label = "测试"
        self.log_queue = __import__("queue").Queue()

    def is_running(self):
        thread = getattr(self, "thread", None)
        return (self.outcome == "running"
                or (thread is not None and thread.is_alive()))

    def stop(self):
        self.stop_event.set()
        self.outcome = "stopped"


class RuntimeEntryTests(unittest.TestCase):
    def test_registration_is_exclusive_and_stop_releases_only_after_runner_stops(self):
        runtime = RuntimeService()
        first = _Runner(running=True)
        second = _Runner(running=False)
        runtime.register(first)
        runtime._active = first
        self.assertEqual(runtime.start(second), (False, ["已有任务运行或停止中，请等待结束后再启动"]))
        self.assertEqual(runtime.stop_all(), 1)
        first.outcome = "stopped"
        self.assertFalse(runtime.any_running())

    def test_thread_alive_keeps_runtime_exclusive_after_outcome_changes(self):
        runtime = RuntimeService()
        first = _Runner(running=True)
        first.outcome = "completed"
        first.thread = mock.Mock()
        first.thread.is_alive.return_value = True
        second = _Runner(running=False)
        runtime._active = first
        self.assertEqual(runtime.start(second), (False, ["已有任务运行或停止中，请等待结束后再启动"]))

    def test_log_events_have_one_runtime_stream_and_restart_waits_for_idle(self):
        runtime = RuntimeService()
        runner = _Runner(running=True)
        runtime.register(runner)
        runtime._active = runtime._last = runner
        runtime._restart = lambda: None
        runtime.publish(runner, "命中", "hit")
        self.assertEqual(runtime.drain_logs(), [(runner, "hit", "命中", "测试")])
        self.assertEqual(runtime.restart(), (False, ["任务仍在运行或停止中"]))
        runner.outcome = "stopped"
        self.assertEqual(runtime.restart(), (True, []))


class InputGateTests(unittest.TestCase):
    def test_dry_run_and_stop_never_allow_an_action(self):
        cfg = {"tasks": {"demo": {"dry_run": True}}}
        session = InputSession(cfg, threading.Event(), "demo")
        owner = mock.Mock()
        owner.window = mock.Mock()
        owner.window.rect.return_value = [0, 0, 100, 100]
        with session.activate():
            self.assertFalse(session.allowed(owner, "click", (10, 10), {}))
        session.stop_event.set()
        self.assertFalse(session.allowed(owner, "press_key", ("esc",), {}))

    def test_session_mouse_propagates_gate_without_calling_raw_backend(self):
        cfg = {"tasks": {"demo": {"dry_run": False}}}
        stop = threading.Event()
        session = InputSession(cfg, stop, "demo")
        owner = mock.Mock()
        owner.window = mock.Mock()
        owner.window.rect.return_value = [0, 0, 100, 100]
        raw = mock.Mock()
        raw.click.return_value = None
        session._foreground = mock.Mock(return_value=True)
        session.activated(owner.window, True)
        token = session.capture_token()
        session.observed(token, (0, 0, 100, 100), True)
        with session.activate():
            self.assertTrue(session.bind_mouse(owner, raw).click(10, 10))
        raw.click.assert_called_once_with(10, 10)
        raw.reset_mock()
        stop.set()
        with session.activate():
            self.assertFalse(session.bind_mouse(owner, raw).click(10, 10))
        raw.click.assert_not_called()

        cfg = {"tasks": {
            "escort": {"dry_run": True},
            "daily": {"dry_run": True},
            "organize_bag": {"dry_run": False},
        }}
        session = InputSession(cfg, threading.Event(), "escort")
        self.assertTrue(session.dry_run())
        with session.policy("organize_bag"):
            self.assertFalse(session.dry_run())
        with session.policy("daily"):
            self.assertFalse(session.dry_run())

    def test_failed_foreground_clears_observation(self):
        session = InputSession({"tasks": {"demo": {"dry_run": False}}},
                               threading.Event(), "demo")
        window = mock.Mock()
        window.rect.return_value = [0, 0, 100, 100]
        with mock.patch.object(session, "_foreground", return_value=False):
            session.activated(window, True)
        self.assertIsNone(session._window)
        self.assertIsNone(session._observation)


if __name__ == "__main__":
    unittest.main()
