# -*- coding: utf-8 -*-
"""
任务运行上下文。把“窗口、鼠标、配置、日志、停止信号”打包传给任务，
任务只依赖这个 ctx，不直接碰 GUI，从而保证可在后台线程安全运行。
"""

import time
import threading

from . import config as cfg_mod
from . import window as win_mod
from . import vision
from .window import GameWindow
from .input import Mouse


class TaskContext:
    def __init__(self, cfg, log_fn=None, stop_event=None, window=None, label=None):
        self.cfg = cfg
        # 按 config 设定「只认游戏进程的窗口」，避免把终端/编辑器等同名标题窗口当成游戏号去点击。
        win_mod.set_game_process(cfg.get("window_process", "MyGame_x64r.exe"))
        # window 非空（多开派生子上下文）则绑定指定窗口；否则按标题建一个待 locate 的窗口。
        self.window = window or GameWindow(cfg.get("window_title", "梦幻西游"),
                                           cfg.get("window_offset", [0, 0]))
        self.mouse = Mouse(cfg.get("input_backend", "sendinput"),
                           cfg.get("humanize", {}))
        # 键盘动作（按键/组合键）也在同一个拟人化输入对象上，导航/复位靠它发快捷键。
        self.keyboard = self.mouse
        self.hotkeys = cfg.get("hotkeys", {})   # 键名映射，任务用 ctx.send_hotkey(动作名) 发
        self.label = label                      # 多开时标识「号1/号2…」，单开为 None
        self._log_fn = log_fn or (lambda msg, level="info": None)
        self.stop_event = stop_event or threading.Event()
        self._last_bag_check = 0.0              # 「自动整理背包」节流：上次检测背包满的时刻（每号独立）

    # ---- 日志：任务调用 ctx.log(...)，GUI 通过 log_fn 收 ----
    def log(self, msg, level="info"):
        if self.label:                          # 多开：给日志加「[号N] 」前缀，三个号共用一个面板也能分清
            msg = f"[{self.label}] {msg}"
        self._log_fn(msg, level)

    # ---- 多开：派生绑定指定窗口的子上下文（共享鼠标/日志/停止/配置）----
    def make_child(self, window, label):
        """基于本上下文派生一个绑定指定窗口的子上下文：
        共享同一个鼠标(光标本就全局唯一)、日志出口、停止信号与配置，只是窗口与标签不同。"""
        child = TaskContext(self.cfg, log_fn=self._log_fn,
                            stop_event=self.stop_event, window=window, label=label)
        child.mouse = self.mouse                # 共用同一只鼠标
        child.keyboard = child.mouse
        return child

    # ---- 基础特性：按 targets 配置选出要操作的窗口列表 ----
    def select_windows(self):
        """单开→[选中的那个窗口]；多开→[选中的多个窗口]；找不到返回 []。"""
        return win_mod.resolve_targets(self.cfg.get("window_title", "梦幻西游"),
                                       self.cfg.get("window_offset", [0, 0]),
                                       self.cfg.get("targets", {}))

    # ---- 基础特性：取「大检测区」的屏幕矩形（整窗检测核心）----
    def detection_rect(self, region):
        """region 为空 → 用整个窗口当检测区（返回窗口屏幕矩形）；
        否则按窗口内 [x,y,w,h] 换算成屏幕绝对矩形。窗口未定位返回 None。"""
        if region:
            return self.window.region_to_screen_rect(region)
        return self.window.rect()

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

    # ---- 自动整理背包：任何任务流程在轮转节拍调用，检测到背包满则就地整理一遍 ----
    def maybe_auto_organize(self):
        """若开了 tasks.organize_bag.auto_organize，按节流间隔检测一次「背包满图标」，
        命中就在【当前前台号】上整理背包一遍（dry_run 跟随 organize_bag 自身配置）。
        由 core/rotation 在每个号切前台后调用，故覆盖所有多开轮转任务（运镖/宝图/秘境/副本）。
        未开开关 / 未标定满图标 / 没满 → 静默返回，不打扰任务流程。"""
        ob = self.task_cfg("organize_bag")
        if not ob.get("auto_organize"):
            return
        loop = ob.get("loop", {}) or {}
        interval = loop.get("auto_check_interval_sec", 20)
        now = time.time()
        if now - self._last_bag_check < interval:
            return
        self._last_bag_check = now
        tpl_path = (ob.get("templates", {}) or {}).get("bag_full_icon")
        tpl = vision.load_template(tpl_path) if tpl_path else None
        if tpl is None:
            return                       # 没标定「背包满图标」→ 无从判断，静默跳过
        rect = self.window.rect()
        if rect is None:
            return
        scene = win_mod.grab(rect)
        if scene is None:
            return
        if vision.match(scene, tpl, loop.get("match_threshold", 0.85)) is None:
            return                       # 背包没满
        self.log("检测到背包已满 → 自动整理背包。", level="hit")
        try:
            self.window.activate()       # 整理要点击，先确保在前台（单开 rotation 不会 activate）
        except Exception:
            pass
        from .inventory import InventoryOrganizer   # 延迟导入，避免 core 内循环依赖
        try:
            InventoryOrganizer(self, ob, dry_run=ob.get("dry_run", True)).organize()
        except Exception as e:
            self.log(f"自动整理背包异常：{e}", level="error")
        self._last_bag_check = time.time()   # 整理耗时较久，以结束时刻重新计节流
