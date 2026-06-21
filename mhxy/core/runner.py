# -*- coding: utf-8 -*-
"""
任务运行器：在后台线程里跑一个 Task，把日志通过线程安全队列交给 GUI。
GUI 只需 start()/stop()/poll 队列，完全不必关心线程细节。
"""

import queue
import threading

from .context import TaskContext


class TaskRunner:
    def __init__(self, task, cfg):
        self.task = task
        self.cfg = cfg
        self.log_queue = queue.Queue()
        self.stop_event = threading.Event()
        self.thread = None

    def _log(self, msg, level="info"):
        self.log_queue.put((level, msg))

    def is_running(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self):
        """启动任务。返回 (ok, problems)。preflight 不通过则不启动。"""
        if self.is_running():
            return False, ["任务已在运行"]
        self.stop_event.clear()
        ctx = TaskContext(self.cfg, log_fn=self._log, stop_event=self.stop_event)
        ok, problems = self.task.preflight(ctx)
        if not ok:
            return False, problems

        def _wrap():
            try:
                self.task.run(ctx)
            except Exception as e:  # 任务里任何异常都不该让线程静默死掉
                self._log(f"任务异常：{e}", "error")

        self.thread = threading.Thread(target=_wrap, daemon=True)
        self.thread.start()
        return True, []

    def stop(self):
        self.stop_event.set()
