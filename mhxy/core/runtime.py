# -*- coding: utf-8 -*-
"""进程级运行入口；旧 TaskRunner.start 也必须经过这里。

登记先于 preflight，停止只请求取消，线程真正退出后才释放输入席位。
日志保留旧 runner 队列用于 CLI 兼容，GUI 使用此处的单一事件流。
"""
import queue
import threading
import weakref


class RuntimeService:
    def __init__(self):
        self._lock = threading.RLock()
        self._registered = weakref.WeakSet()
        self._active = None
        self._last = None
        self._restart = None
        self._logs = queue.Queue()

    def create_runner(self, task, cfg):
        from .runner import TaskRunner
        return TaskRunner(task, cfg)

    @staticmethod
    def is_running(runner):
        """页面持有运行句柄，线程生命周期的判断留在运行服务。"""
        return runner is not None and runner.is_running()

    @staticmethod
    def stop(runner):
        """请求指定运行停止；保留原停止边界，不提前释放输入席位。"""
        if runner is not None:
            runner.stop()

    def register(self, runner):
        with self._lock:
            self._registered.add(runner)

    def start(self, runner):
        with self._lock:
            self.register(runner)
            if self._active is not None and self._active.is_running():
                return False, ["已有任务运行或停止中，请等待结束后再启动"]
            self._active = runner
            runner.stop_event.clear()
            runner.outcome = "running"
        try:
            result = runner._start_registered()
        except Exception as exc:
            runner.outcome = "failed"
            runner._log(f"启动异常：{exc}", "error")
            result = False, [f"启动异常：{exc}"]
        with self._lock:
            if result[0]:
                self._last = runner
                self._restart = None
            elif self._active is runner:
                self._active = None
        return result

    def finished(self, runner):
        with self._lock:
            # 此回调仍在工作线程内；保留 _active，由 is_running 核验真实退出。
            # 下次 start 才覆盖已退出的席位，不能在 finally 中提前放行。
            self._last = runner
    def bind(self, runner, label, restart=None):
        """启动成功后补展示元数据，不承担运行登记。"""
        with self._lock:
            if runner not in self._registered:
                raise ValueError("Runner 未登记")
            runner.label = label or runner.label
            if runner is self._last and callable(restart):
                self._restart = restart

    def restart(self):
        with self._lock:
            if self.any_running():
                return False, ["任务仍在运行或停止中"]
            callback = self._restart
        if callback is None:
            return False, ["还没跑过任何模块，请先展开主界面开始"]
        callback()
        return True, []

    def any_running(self):
        with self._lock:
            return self._active is not None and self._active.is_running()

    def stop_all(self):
        with self._lock:
            if not self.any_running():
                return 0
            self._active.stop()
            return 1

    def state(self):
        with self._lock:
            runner = self._active if self.any_running() else self._last
            if runner is None:
                return "idle", "", ()
            if runner.is_running():
                phase = "stopping" if runner.stop_event.is_set() else "running"
                return phase, runner.label, (runner.label,)
            return runner.outcome, runner.label, ()

    def publish(self, runner, msg, level):
        self._logs.put((runner, level, msg))

    def drain_logs(self, max_events=None):
        """取出统一日志流中的事件。

        ``max_events`` 供图形界面按小批量消费，避免长时间运行时一次性
        把所有日志搬到主线程。省略该参数时保持原来的“取空队列”行为。
        ``None`` 表示不限制；小于等于零表示本次不消费任何事件。
        """
        if max_events is not None:
            try:
                max_events = int(max_events)
            except (TypeError, ValueError):
                max_events = None
            if max_events is not None and max_events <= 0:
                return []
        events = []
        while max_events is None or len(events) < max_events:
            try:
                runner, level, msg = self._logs.get_nowait()
            except queue.Empty:
                return events
            events.append((runner, level, msg, runner.label))
            # GUI 已消费统一日志后，旧兼容队列也必须释放，避免长任务双份累积。
            try:
                runner.log_queue.get_nowait()
            except queue.Empty:
                pass
        return events


_RUNTIME = RuntimeService()


def get_runtime():
    return _RUNTIME
