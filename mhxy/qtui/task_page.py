"""所有 Qt 任务页共享的运行框架。

具体任务页只需提供 ``TASK_NAME``、标题以及可选的参数控件。这里负责配置
读取、runner 创建、后台 preflight/start、停止请求、状态显示和小批量日志
消费；任务本身仍由 ``mhxy.core`` 和 ``mhxy.tasks`` 实现。
"""

import copy
import threading

from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..core import config as cfg_mod
from ..core.runtime import get_runtime
from . import theme as T
from .log_panel import LogPanel
from .widgets import FormLayout as QFormLayout


class _TaskBridge(QWidget):
    """仅承载 worker 到主线程的启动结果信号。"""

    start_finished = Signal(bool, object)


class TaskPage(QWidget):
    """任务页的轻量公共 UI 和 runner 生命周期。

    参数都是可选的，方便后续页面通过类属性复用：

    ``TaskPage(cfg, "escort", EscortTask, runtime)``
    ``class EscortPage(TaskPage): TASK_NAME = "escort"``

    ``task_factory`` 可以是任务类、返回任务实例的 callable，或直接传入任务
    实例。测试也可以传 ``runner_factory`` 直接提供假 runner。
    """

    TASK_NAME = ""
    TITLE = "任务"
    LOG_SOURCE = ""
    START_TEXT = "开始"
    LOG_BATCH_SIZE = 64
    POLL_INTERVAL_MS = 100

    STATUS_NAMES = {
        "idle": "待机",
        "running": "运行",
        "stopping": "停止中",
        "completed": "已完成",
        "stopped": "待机",
        "failed": "失败",
        "timeout": "超时",
        "partial": "部分完成",
    }
    status_changed = Signal(str)
    start_requested = Signal()
    stop_requested = Signal()
    window_selection_requested = Signal()

    def __init__(self, cfg=None, task_name=None, task_factory=None, runtime=None,
                 parent=None, *, task_cls=None, runner_factory=None,
                 log_batch_size=None, poll_interval_ms=None, config=None):
        # Allow the usual QWidget shorthand TaskPage(parent).
        if isinstance(cfg, QWidget) and parent is None:
            parent, cfg = cfg, None
        if isinstance(cfg, str):
            task_name, cfg = cfg, None
        if isinstance(task_name, QWidget) and parent is None:
            parent, task_name = task_name, None
        super().__init__(parent)
        self.setObjectName("taskPage")
        T.set_role(self, "page")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.task_name = task_name or self.TASK_NAME
        self.task_factory = task_factory or task_cls
        self.runner_factory = runner_factory
        self.runtime = runtime or get_runtime()
        if cfg is None and config is not None:
            cfg = config
        self._cfg_supplied = isinstance(cfg, dict)
        self.cfg = copy.deepcopy(cfg) if self._cfg_supplied else cfg_mod.load_config()
        self.runner = None
        self._start_pending = False
        self._stop_requested = False
        self._terminal_rendered = False
        self._visible = False
        self._closed = False
        self.log_batch_size = max(1, int(log_batch_size or self.LOG_BATCH_SIZE))

        self.bridge = _TaskBridge()
        self.bridge.start_finished.connect(self._start_result, Qt.ConnectionType.QueuedConnection)
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(int(poll_interval_ms or self.POLL_INTERVAL_MS))
        self.poll_timer.timeout.connect(self._pump)

        self._build_ui()
        self.refresh_config()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(T.SPACE_LG, T.SPACE_LG, T.SPACE_LG, T.SPACE_LG)
        layout.setSpacing(T.SPACE_MD)

        header = QHBoxLayout()
        self.title_label = QLabel(self.page_title, self)
        self.title_label.setProperty("role", "heading")
        header.addWidget(self.title_label, 1)
        self.mode_label = QLabel("演练", self)
        self.mode_label.setProperty("role", "muted")
        header.addWidget(self.mode_label)
        self.status_label = QLabel("待机", self)
        self.status_label.setProperty("role", "muted")
        header.addWidget(self.status_label)
        layout.addLayout(header)

        controls = QHBoxLayout()
        self.start_button = QPushButton(self.START_TEXT, self)
        T.set_role(self.start_button, "primary")
        self.start_button.clicked.connect(self.toggle_run)
        controls.addWidget(self.start_button)
        self.stop_button = QPushButton("停止", self)
        T.set_role(self.stop_button, "danger")
        self.stop_button.clicked.connect(self.stop)
        controls.addWidget(self.stop_button)
        self.window_button = QPushButton("选择窗口", self)
        self.window_button.clicked.connect(self.window_selection_requested.emit)
        controls.addWidget(self.window_button)
        self.window_status = QLabel("窗口配置由任务页提供", self)
        self.window_status.setProperty("role", "muted")
        self.window_status.setWordWrap(True)
        controls.addWidget(self.window_status, 1)
        layout.addLayout(controls)

        self.body_scroll = QScrollArea(self)
        self.body_scroll.setWidgetResizable(True)
        self.body_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.body = QWidget(self.body_scroll)
        self.body.setObjectName("taskBody")
        body_layout = QVBoxLayout(self.body)
        body_layout.setContentsMargins(0, 0, 4, 0)
        body_layout.setSpacing(T.SPACE_MD)
        body_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.form = QFormLayout()
        body_layout.addLayout(self.form)
        self.content_layout = QVBoxLayout()
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(T.SPACE_MD)
        body_layout.addLayout(self.content_layout, 1)
        self.body_scroll.setWidget(self.body)
        layout.addWidget(self.body_scroll, 1)

        self.log_panel = LogPanel(self, collapsed=True)
        layout.addWidget(self.log_panel)
        self.run_button = self.start_button
        self.select_windows_button = self.window_button
        self.log = self.log_panel
        self.stop_button.setEnabled(False)

    @property
    def page_title(self):
        if self.TITLE != "任务":
            return self.TITLE
        factory = self.task_factory
        if factory is not None:
            title = getattr(factory, "title", None)
            if title:
                return str(title)
        return self.task_name or self.TITLE

    def refresh_config(self):
        """重新读取配置并刷新模式/窗口摘要；可由具体页面扩展。"""
        if not self._cfg_supplied:
            self.cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(self.cfg, self.task_name) if self.task_name else {}
        self.mode_label.setText("实战" if not tc.get("dry_run", True) else "演练")
        targets = self.cfg.get("targets") or {}
        if targets.get("multi"):
            indices = targets.get("multi_indices") or []
            count = targets.get("max_windows") or len(indices)
            self.window_status.setText(f"已选择 {count or '多个'} 个窗口")
        else:
            self.window_status.setText("已选择 1 个窗口" if targets else "未选择窗口")
        return self.cfg

    def add_control(self, label, widget):
        """供具体页把参数控件放进共享表单。"""
        self.form.addRow(label, widget)

    def _make_runner(self):
        if self.runner_factory is not None:
            try:
                return self.runner_factory(self.cfg)
            except TypeError:
                return self.runner_factory()
        factory = self.task_factory
        if factory is None:
            from ..tasks import get_task
            factory = get_task(self.task_name)
        if factory is None:
            raise ValueError(f"未注册任务：{self.task_name}")
        task = factory() if isinstance(factory, type) else (factory() if callable(factory) else factory)
        runner = self.runtime.create_runner(task, self.cfg)
        # TaskRunner defaults to the process-wide service for legacy callers;
        # an injected service belongs to this page and must receive its logs.
        if getattr(runner, "runtime", None) is not self.runtime:
            runner.runtime = self.runtime
            register = getattr(self.runtime, "register", None)
            if callable(register):
                register(runner)
        return runner

    def _set_status(self, status):
        T.set_tone(self.status_label, {"failed": "error", "timeout": "error", "partial": "warning",
                                     "stopping": "warning", "running": "success", "completed": "success"}.get(status, ""))
        status = self.STATUS_NAMES.get(status, str(status))
        if self.status_label.text() != status:
            self.status_label.setText(status)
            self.status_changed.emit(status)

    def _runtime_running(self):
        try:
            return bool(self.runner and self.runtime.is_running(self.runner))
        except Exception:
            return bool(self.runner and getattr(self.runner, "is_running", lambda: False)())

    def _status_key(self):
        if self.runner is None:
            return "idle"
        if self._start_pending or self._runtime_running():
            return "stopping" if getattr(self.runner, "stop_event", None) is not None and self.runner.stop_event.is_set() else "running"
        return getattr(self.runner, "outcome", "idle")

    def toggle_run(self):
        if self._start_pending or self._runtime_running():
            self.stop()
            return
        if getattr(self.window(), "_window_action", ""):
            self.log_panel.append("请等待窗口操作完成。", "warn", self.LOG_SOURCE)
            return
        if getattr(self, "_calibration_dialog", None) is not None:
            self.log_panel.append("请先关闭标定窗口。", "warn", self.LOG_SOURCE)
            return
        self.refresh_config()
        try:
            runner = self._make_runner()
        except Exception as exc:
            self.log_panel.append(f"无法启动：{exc}", "error", self.LOG_SOURCE)
            self._set_status("failed")
            return
        self.runner = runner
        self._start_pending = True
        self._stop_requested = False
        cancel = self._cancel_event = threading.Event()
        self._terminal_rendered = False
        self.stop_button.setEnabled(True)
        self.start_button.setEnabled(False)
        self._set_status("running")
        self.start_requested.emit()
        runtime, bridge = self.runtime, self.bridge

        def work():
            if cancel.is_set():
                ok, problems = False, ["启动已取消"]
            else:
                try:
                    ok, problems = runtime.start(runner)
                except Exception as exc:  # keep worker exceptions out of Qt
                    ok, problems = False, [str(exc)]
                if cancel.is_set():
                    try:
                        runtime.stop(runner)
                    except Exception:
                        pass
            if isinstance(problems, str):
                problems = [problems]
            try:
                bridge.start_finished.emit(bool(ok), list(problems or []))
            except RuntimeError:
                pass

        threading.Thread(target=work, name="mhxy-task-start", daemon=True).start()

    # Stable names for concrete pages and lightweight tests.  The button uses
    # ``toggle_run`` so a second click keeps the old start/stop interaction.
    start = toggle_run

    @Slot(bool, object)
    def _start_result(self, ok, problems):
        if self._closed:
            return
        self._start_pending = False
        if not ok:
            # 取消 preflight 是一次正常的停止流程，不应被显示成失败。
            if self._stop_requested:
                self._terminal_rendered = True
                self._set_status("stopped")
                self.stop_button.setEnabled(False)
                self.start_button.setEnabled(True)
                return
            for problem in problems or ["启动检查失败"]:
                self.log_panel.append("无法启动：" + str(problem), "error", self.LOG_SOURCE)
            self._terminal_rendered = True
            self._set_status("failed")
            self.stop_button.setEnabled(False)
            self.start_button.setEnabled(True)
            return
        if self._stop_requested:
            self._set_status("stopping")
        else:
            self._set_status(self._status_key())
        running = self._runtime_running()
        self.start_button.setEnabled(not running)
        self.stop_button.setEnabled(running)

    def stop(self):
        if self.runner is None or (not self._start_pending and not self._runtime_running()):
            self._set_status(self._status_key())
            return
        self._stop_requested = True
        cancel = getattr(self, "_cancel_event", None)
        if cancel is not None:
            cancel.set()
        try:
            self.runtime.stop(self.runner)
        except Exception:
            stop = getattr(self.runner, "stop", None)
            if callable(stop):
                stop()
        self.stop_requested.emit()
        self._set_status("stopping")
        self.stop_button.setEnabled(False)

    stop_task = stop

    def clear_log(self):
        self.log_panel.clear()

    def pump(self):
        """兼容旧页面的周期刷新入口；实际调度由 Qt timer 完成。"""
        self._pump()

    def _pump(self):
        if self._closed or not self._visible:
            return
        try:
            events = self.runtime.drain_logs(self.log_batch_size)
        except TypeError:  # compatibility with a minimal fake runtime
            events = self.runtime.drain_logs()
        for event in events:
            if self.runner is None or not event or event[0] is self.runner:
                self.log_panel.append_event(event)
        if self.runner is not None:
            if self._start_pending or self._runtime_running():
                self._set_status(self._status_key())
            elif not self._terminal_rendered:
                self._terminal_rendered = True
                self._set_status(self._status_key())
                self.start_button.setEnabled(True)
                self.stop_button.setEnabled(False)

    def showEvent(self, event):
        self._visible = True
        if not self.poll_timer.isActive():
            self.poll_timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self._visible = False
        self.poll_timer.stop()
        super().hideEvent(event)

    def closeEvent(self, event):
        self._closed = True
        self.poll_timer.stop()
        if self._start_pending or self._runtime_running():
            self.stop()
        super().closeEvent(event)
