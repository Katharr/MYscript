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
        # 键盘动作（按键/组合键）也在同一个拟人化输入对象上，导航/复位靠它发快捷键。
        self.keyboard = self.mouse
        self.hotkeys = cfg.get("hotkeys", {})   # 键名映射，任务用 ctx.send_hotkey(动作名) 发
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

    # ---- 便捷：按动作名发配置里的快捷键（如 send_hotkey("open_bag")）----
    def send_hotkey(self, action):
        """查 cfg.hotkeys[action] 得到键名列表（如 ["alt","e"]）并发出。
        返回是否成功（动作未配置或键名为空则返回 False，调用方可降级为点坐标）。"""
        keys = self.hotkeys.get(action)
        if not keys:
            return False
        return self.keyboard.send_hotkey(keys)

    # ---- 便捷：取本任务的配置块 ----
    def task_cfg(self, task_name):
        return cfg_mod.task_config(self.cfg, task_name)
