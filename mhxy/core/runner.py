# -*- coding: utf-8 -*-
"""旧任务线程适配器；登记、互斥、停止与 GUI 日志由 runtime 统一管理。"""
import copy
import queue
import threading

from .context import TaskContext
from .runtime import get_runtime


_TASK_OUTCOMES = {"completed", "stopped", "failed", "timeout", "partial"}


class TaskRunner:
    def __init__(self, task, cfg):
        self.task = task
        self.cfg = copy.deepcopy(cfg)
        self.log_queue = queue.Queue()
        self.stop_event = threading.Event()
        self.thread = None
        self.outcome = "idle"
        self.label = getattr(task, "title", None) or getattr(task, "name", "任务")
        self.runtime = get_runtime()
        self.runtime.register(self)

    def _log(self, msg, level="info"):
        self.log_queue.put((level, msg))
        self.runtime.publish(self, msg, level)

    def is_running(self):
        # preflight 和线程启动间隙也持有输入席位；请求停止不会提前释放。
        return self.outcome == "running" or (self.thread is not None and self.thread.is_alive())

    def start(self):
        return self.runtime.start(self)

    def _start_registered(self):
        from .input_session import InputSession
        session = InputSession(self.cfg, self.stop_event, getattr(self.task, "name", ""))
        ctx = TaskContext(self.cfg, log_fn=self._log, stop_event=self.stop_event,
                          input_session=session)
        # preflight 也在禁止真实输入的会话中执行。
        with session.activate(preflight=True):
            ok, problems = self.task.preflight(ctx)
        if not ok:
            self.outcome = "idle"
            return False, problems
        if self.stop_event.is_set():
            self.outcome = "stopped"
            return False, ["启动已取消"]

        def _wrap():
            try:
                with session.activate():
                    task_outcome = self.task.run(ctx)
            except Exception as exc:
                self.outcome = "failed"
                self._log(f"任务异常：{exc}", "error")
            else:
                if isinstance(task_outcome, str) and task_outcome in _TASK_OUTCOMES:
                    self.outcome = task_outcome
                else:
                    self.outcome = "stopped" if self.stop_event.is_set() else "completed"
            finally:
                self.runtime.finished(self)

        self.thread = threading.Thread(target=_wrap, daemon=True)
        try:
            self.thread.start()
        except Exception:
            self.outcome = "failed"
            raise
        return True, []

    def stop(self):
        self.stop_event.set()
