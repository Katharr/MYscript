# -*- coding: utf-8 -*-
"""
任务运行上下文。把“窗口、鼠标、配置、日志、停止信号”打包传给任务，
任务只依赖这个 ctx，不直接碰 GUI，从而保证可在后台线程安全运行。
"""

import threading

from . import calibration
from . import window as win_mod
from . import accounts
from .window import GameWindow
from .input import Mouse


class TaskContext:
    def __init__(self, cfg, log_fn=None, stop_event=None, window=None, label=None,
                 input_session=None, mouse=None):
        self.cfg = cfg
        # 按 config 设定「只认游戏进程的窗口」，避免把终端/编辑器等同名标题窗口当成游戏号去点击。
        win_mod.set_game_process(cfg.get("window_process") or win_mod.DEFAULT_GAME_PROCESS_SPEC)
        # 游戏安装目录（读角色名用）；空=自动从窗口进程 exe 反推，见 core/accounts 模块头。
        accounts.set_game_dir(cfg.get("game_dir"))
        # 启动器 EXE 路径：游戏没开时靠它反推 LocalData 读名册（见 accounts.set_launcher_path）。
        accounts.set_launcher_path((cfg.get("account_launch") or {}).get("launcher_path"))
        # window 非空（多开派生子上下文）则绑定指定窗口；否则按标题建一个待 locate 的窗口。
        self.window = window or GameWindow(cfg.get("window_title", "梦幻西游"),
                                           cfg.get("window_offset", [0, 0]))
        self.input_session = input_session
        self._raw_mouse = mouse or Mouse(cfg.get("input_backend", "sendinput"),
                                         cfg.get("humanize", {}))
        self.mouse = (input_session.bind_mouse(self, self._raw_mouse)
                      if input_session is not None else self._raw_mouse)
        # 键盘动作（按键/组合键）也在同一个拟人化输入对象上，导航/复位靠它发快捷键。
        self.keyboard = self.mouse
        self.hotkeys = cfg.get("hotkeys", {})   # 键名映射，任务用 ctx.send_hotkey(动作名) 发
        self.label = label                      # 多开时的号标识（「角色名（等级）」，认不出才是「号N」），单开为 None
        self._log_fn = log_fn or (lambda msg, level="info": None)
        self.stop_event = stop_event or threading.Event()
        self.inventory_revision = 0  # 背包维护改变内容/位置后使扫描覆盖证据失效。

    # ---- 日志：任务调用 ctx.log(...)，GUI 通过 log_fn 收 ----
    def log(self, msg, level="info"):
        if self.label:                          # 多开：给日志加「[号N] 」前缀，三个号共用一个面板也能分清
            msg = f"[{self.label}] {msg}"
        self._log_fn(msg, level)

    # ---- 多开：派生绑定指定窗口的子上下文（共享鼠标/日志/停止/配置）----
    def make_child(self, window, label):
        """基于本上下文派生一个绑定指定窗口的子上下文：
        共享同一个鼠标(光标本就全局唯一)、日志出口、停止信号与配置，只是窗口与标签不同。"""
        return TaskContext(self.cfg, log_fn=self._log_fn,
                           stop_event=self.stop_event, window=window, label=label,
                           input_session=self.input_session, mouse=self._raw_mouse)

    # ---- 基础特性：按 targets 配置选出要操作的窗口列表 ----
    def select_windows(self):
        """单开→[选中的那个窗口]；多开→[选中的多个窗口]；找不到返回 []。"""
        return win_mod.resolve_targets(self.cfg.get("window_title", "梦幻西游"),
                                       self.cfg.get("window_offset", [0, 0]),
                                       self.cfg.get("targets", {}))

    def window_labels(self, wins):
        """按 wins 的顺序给出「角色名（等级）」显示名（认不出退回「号N」），与 select_windows 同序。

        用途：多开时把日志/界面里的「号1/号2」换成真实角色名，免得对不上是哪个号。
        原理见 core/accounts 模块头（读客户端 LocalData 下的纯文本 + 登录时间戳配对，零 OCR）。"""
        try:
            return accounts.cached_labels_for(wins)
        except Exception:
            return [accounts.fallback_label(i) for i in range(len(wins))]

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
        return calibration.effective_task_config(self.cfg, task_name)
