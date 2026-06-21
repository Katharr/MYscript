# -*- coding: utf-8 -*-
"""
任务运行上下文。把“窗口、鼠标、配置、日志、停止信号”打包传给任务，
任务只依赖这个 ctx，不直接碰 GUI，从而保证可在后台线程安全运行。
"""

import threading

from . import config as cfg_mod
from .window import GameWindow
from .input import Mouse


class TaskContext:
    def __init__(self, cfg, log_fn=None, stop_event=None):
        self.cfg = cfg
        self.window = GameWindow(cfg.get("window_title", "梦幻西游"),
                                 cfg.get("window_offset", [0, 0]))
        self.mouse = Mouse(cfg.get("input_backend", "sendinput"),
                           cfg.get("humanize", {}))
        self._log_fn = log_fn or (lambda msg, level="info": None)
        self.stop_event = stop_event or threading.Event()

    # ---- 日志：任务调用 ctx.log(...)，GUI 通过 log_fn 收 ----
    def log(self, msg, level="info"):
        self._log_fn(msg, level)

    # ---- 停止控制 ----
    def should_stop(self):
        return self.stop_event.is_set()

    def stop(self):
        self.stop_event.set()

    # ---- 便捷：取本任务的配置块 ----
    def task_cfg(self, task_name):
        return cfg_mod.task_config(self.cfg, task_name)
