# -*- coding: utf-8 -*-
import threading
import unittest
from dataclasses import replace

from mhxy.core.runner import TaskRunner
from mhxy.gui.app import (App, AppState, GAME_ABSENT, GAME_READY, TASK_COMPLETED,
                          TASK_FAILED, TASK_RUNNING, TASK_STOPPED, TASK_STOPPING,
                          UI_COMPACT, UI_FLOATING, UI_FULL)


class _Runner:
    def __init__(self, running, outcome="completed", stopping=False):
        self.running = running
        self.outcome = outcome
        self.stop_event = threading.Event()
        if stopping:
            self.stop_event.set()

    def is_running(self):
        return self.running


class _TkLike:
    """模拟 tk.Tk：自带 state(newstate=None) 方法。

    CTk 的 mainloop/_windows_set_titlebar_color 会调 `self.state(...)` 复位窗口显示状态，
    故 App 的状态快照必须叫 ui_state —— 叫 state 会把这个方法覆盖掉（真机上启动即崩）。"""

    def __init__(self):
        self._tk_state = "normal"

    def state(self, new_state=None):
        if new_state is not None:
            self._tk_state = new_state
        return self._tk_state


class _StateApp(_TkLike):
    def __init__(self):
        super().__init__()
        self.ui_state = AppState(ui_mode=UI_FULL)
        self._started = []
        self.quick_panel = None
        self.float_log = None
        self._task_restart = None
        self.collapse_calls = []

    def collapse_to_float(self, auto=False):
        self.collapse_calls.append(auto)
        self._set_state(ui_mode=UI_FLOATING)

    def _sync_task_state(self):
        return App._sync_task_state(self)

    @staticmethod
    def _runner_running(runner):
        return runner.is_running()

    def _set_state(self, **changes):
        self.ui_state = replace(self.ui_state, **changes)


class _Task:
    def __init__(self, mode):
        self.mode = mode

    def preflight(self, ctx):
        return True, []

    def run(self, ctx):
        if self.mode == "failed":
            raise RuntimeError("boom")
        if self.mode == "stopped":
            ctx.stop()


class AppStateTests(unittest.TestCase):
    def test_started_task_enters_running_floating_state(self):
        app = _StateApp()
        runner = _Runner(running=True)
        restart = lambda: None

        App.on_task_started(app, "秒装备", restart, runner)

        self.assertEqual(app.ui_state.ui_mode, UI_FLOATING)
        self.assertEqual(app.ui_state.task_state, TASK_RUNNING)
        self.assertEqual(app.ui_state.active_labels, ("秒装备",))
        self.assertIs(app._task_restart, restart)
        self.assertEqual(app.collapse_calls, [True])

    def test_state_attribute_never_shadows_tk_state_method(self):
        """回归：状态快照叫 ui_state，Tk 的 state() 方法必须仍然可调用。

        叫 self.state 时它覆盖 tk.Tk.state()，CTk 启动进 mainloop 会
        `self.state(self._state_before_windows_set_titlebar_color)` → TypeError: 'AppState' object is not callable。"""
        app = _StateApp()
        App.on_task_started(app, "秒装备", lambda: None, _Runner(running=True))

        self.assertEqual(app.state("normal"), "normal")   # 仍是 Tk 的 wm state，没被数据对象顶掉
        self.assertEqual(app.ui_state.task_state, TASK_RUNNING)

    def test_quick_start_stays_compact_while_running(self):
        app = _StateApp()
        app.ui_state = replace(app.ui_state, ui_mode=UI_COMPACT)
        runner = _Runner(running=True)

        App.on_quick_start_started(app, runner)

        self.assertEqual(app.ui_state.ui_mode, UI_COMPACT)
        self.assertEqual(app.ui_state.task_state, TASK_RUNNING)
        self.assertEqual(app.ui_state.active_labels, ("启动登录",))
        self.assertEqual(app.collapse_calls, [])

    def test_sync_reports_running_then_stopping_then_stopped(self):
        app = _StateApp()
        runner = _Runner(running=True, stopping=False)
        app._started = [(runner, "秒装备")]

        App._sync_task_state(app)
        self.assertEqual(app.ui_state.task_state, TASK_RUNNING)
        self.assertEqual(app.ui_state.active_labels, ("秒装备",))

        runner.stop_event.set()
        App._sync_task_state(app)
        self.assertEqual(app.ui_state.task_state, TASK_STOPPING)

        runner.running = False
        runner.outcome = "stopped"
        App._sync_task_state(app)
        self.assertEqual(app.ui_state.task_state, TASK_STOPPED)
        self.assertEqual(app.ui_state.task_label, "秒装备")
        self.assertEqual(app.ui_state.active_labels, ())

    def test_sync_preserves_completed_or_failed_outcome(self):
        for outcome, expected in (("completed", TASK_COMPLETED), ("failed", TASK_FAILED)):
            with self.subTest(outcome=outcome):
                app = _StateApp()
                app._started = [(_Runner(running=False, outcome=outcome), "运镖")]
                App._sync_task_state(app)
                self.assertEqual(app.ui_state.task_state, expected)
                self.assertEqual(app.ui_state.task_label, "运镖")

    def test_state_constants_keep_ui_and_game_independent(self):
        state = AppState(ui_mode=UI_COMPACT, game_state=GAME_ABSENT)
        state = replace(state, ui_mode=UI_FLOATING, game_state=GAME_READY,
                        task_state=TASK_RUNNING, task_label="宝图")
        self.assertEqual(state.ui_mode, UI_FLOATING)
        self.assertEqual(state.game_state, GAME_READY)
        self.assertEqual(state.task_state, TASK_RUNNING)


class TaskRunnerOutcomeTests(unittest.TestCase):
    def _run(self, mode):
        runner = TaskRunner(_Task(mode), {})
        ok, problems = runner.start()
        self.assertTrue(ok, problems)
        runner.thread.join(timeout=2)
        self.assertFalse(runner.is_running())
        return runner

    def test_runner_records_completed_outcome(self):
        self.assertEqual(self._run("completed").outcome, TASK_COMPLETED)

    def test_runner_records_stopped_outcome(self):
        self.assertEqual(self._run("stopped").outcome, TASK_STOPPED)

    def test_runner_records_failed_outcome(self):
        runner = self._run("failed")
        self.assertEqual(runner.outcome, TASK_FAILED)
        self.assertEqual(runner.log_queue.get_nowait()[0], "error")


if __name__ == "__main__":
    unittest.main()
