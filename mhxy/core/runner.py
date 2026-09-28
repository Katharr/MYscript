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
        # GUI 用它区分正常完成、请求停止和异常退出，不能只根据线程是否仍存活猜状态。
        self.outcome = "idle"

    def _log(self, msg, level="info"):
        self.log_queue.put((level, msg))

    def is_running(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self):
        """启动任务。返回 (ok, problems)。preflight 不通过则不启动。"""
        if self.is_running():
            return False, ["任务已在运行"]
        self.stop_event.clear()
        self.outcome = "running"
        ctx = TaskContext(self.cfg, log_fn=self._log, stop_event=self.stop_event)
        ok, problems = self.task.preflight(ctx)
        if not ok:
            self.outcome = "idle"
            return False, problems

        def _wrap():
            try:
                self.task.run(ctx)
            except Exception as e:  # 任务里任何异常都不该让线程静默死掉
                self.outcome = "failed"
                self._log(f"任务异常：{e}", "error")
            else:
                self.outcome = "stopped" if self.stop_event.is_set() else "completed"

        self.thread = threading.Thread(target=_wrap, daemon=True)
        self.thread.start()
        return True, []

    def stop(self):
        self.stop_event.set()
