# -*- coding: utf-8 -*-
"""旧任务的输入会话适配：前台、观察、停止与演练在原子动作入口统一检查。

Runner 串行持有整个会话；线程局部窗口/截图证据不影响 GUI 的标定截图。
run 与 daily 子 driver 显式切换策略，自动整理使用自己的配置命名空间。
观察在切窗、激活失败、位置/尺寸变化、截图失败后失效；不跨窗口复用。
"""
import threading
import time
from contextlib import contextmanager

from .config import task_config

_LOCAL = threading.local()
_ACTIONS = frozenset(("human_move", "click", "right_click", "double_click",
                      "scroll", "press_key", "send_hotkey"))


def current_session():
    return getattr(_LOCAL, "session", None)


class InputSession:
    def __init__(self, cfg, stop_event, task_name, activity_fn=None):
        self.cfg = cfg
        self.stop_event = stop_event
        self.task_name = task_name
        self._policy = task_name
        self._policies = []
        self._preflight = False
        self._window = None
        self._activation_rect = None
        self._observation = None
        self._action_lock = threading.RLock()
        self.activity_fn = activity_fn

    def report_activity(self, window, text=None, tone="success"):
        hwnd = self._identity(window)
        if self.activity_fn is not None and hwnd and not self._preflight:
            if text is None:
                from .task_activity import activity_text
                text = activity_text(self._policy)
            self.activity_fn(hwnd, text, tone)

    @contextmanager
    def activate(self, preflight=False):
        previous = current_session()
        previous_preflight = self._preflight
        _LOCAL.session = self
        self._preflight = preflight
        try:
            yield self
        finally:
            self.invalidate()
            self._preflight = previous_preflight
            _LOCAL.session = previous

    @contextmanager
    def policy(self, task_name):
        previous = self._policy
        self._policy = task_name
        self._policies.append(task_name)
        try:
            yield
        finally:
            self._policies.pop()
            self._policy = previous

    def invalidate(self):
        self._observation = None

    @staticmethod
    def _rect(window):
        rect = window.rect() if window is not None else None
        return tuple(rect) if rect and rect[2] > 0 and rect[3] > 0 else None

    @staticmethod
    def _foreground(window):
        # 使用 HWND 严格核验；没有句柄的旧 activate 尽力返回不能作为输入许可。
        from . import window as win_mod
        raw = getattr(window, "_win", None)
        hwnd = getattr(raw, "_hWnd", None)
        return bool(hwnd and win_mod._user32.IsWindow(hwnd)
                    and not win_mod._user32.IsIconic(hwnd)
                    and int(win_mod._user32.GetForegroundWindow() or 0) == int(hwnd))

    @staticmethod
    def _identity(window):
        raw = getattr(window, "_win", None)
        return getattr(raw, "_hWnd", None)

    def activated(self, window, ok):
        previous = self._observation
        self.invalidate()
        self._window = window if ok and self._foreground(window) else None
        if self._window is not None:
            self.report_activity(window)
        self._activation_rect = self._rect(self._window)
        identity = self._identity(self._window)
        if (previous is not None and previous[0] is self._window
                and previous[1] == self._activation_rect and previous[2] == identity):
            self._observation = previous

    def capture_token(self):
        window = self._window
        rect = self._rect(window)
        identity = self._identity(window)
        if (rect is None or rect != self._activation_rect
                or not self._foreground(window)):
            self.invalidate()
            return None
        return window, rect, identity

    def observed(self, token, capture_rect, success):
        self.invalidate()
        if not success or token is None:
            return
        window, rect, identity = token
        if (self._window is not window or self._rect(window) != rect
                or self._identity(window) != identity or not self._foreground(window)):
            return
        # 截图必须与目标窗口相交；启动器允许整屏，点击仍约束在其窗口中。
        x, y, w, h = capture_rect
        if x >= rect[0] + rect[2] or y >= rect[1] + rect[3] or x + w <= rect[0] or y + h <= rect[1]:
            return
        self._observation = (window, rect, identity, time.monotonic())

    def dry_run(self):
        # 嵌套能力（当前是自动整理）使用自己的配置；父任务只约束自己的动作。
        # daily 没有自身演练开关，子任务和 organize_bag 各自决定。
        name = self._policies[-1] if self._policies else self._policy
        if name == "daily":
            return False
        name = "teaming" if name in ("dungeon", "disband") else name
        return task_config(self.cfg, name).get("dry_run", True)

    def allowed(self, owner, action, args, kwargs):
        if current_session() is not self or self._preflight or self.stop_event.is_set() or self.dry_run():
            return False
        target = self._window if self.task_name == "launch_login" else owner.window
        rect = self._rect(target)
        if target is not self._window or rect is None or rect != self._activation_rect or not self._foreground(target):
            self.invalidate()
            return False
        # 键盘导航也不能在激活失败后发送。坐标动作必须有本窗的近期观察。
        if action in ("press_key", "send_hotkey"):
            return True
        observation = self._observation
        if (observation is None or observation[0] is not target
                or observation[1] != rect or observation[2] != self._identity(target)
                or time.monotonic() - observation[3] > 5.0):
            return False
        if action == "scroll":
            x = args[1] if len(args) > 1 else kwargs.get("x")
            y = args[2] if len(args) > 2 else kwargs.get("y")
        else:
            x = args[0] if args else kwargs.get("x")
            y = args[1] if len(args) > 1 else kwargs.get("y")
        if x is None or y is None:
            from .input import get_cursor
            x, y = get_cursor()
        return rect[0] <= x < rect[0] + rect[2] and rect[1] <= y < rect[1] + rect[3]

    def bind_mouse(self, owner, mouse):
        return SessionMouse(self, owner, mouse)


class SessionMouse:
    """仅暴露旧任务使用的公共动作，后端及 raw 注入方法不向任务转发。"""
    def __init__(self, session, owner, mouse):
        self._session = session
        self._owner = owner
        self._mouse = mouse

    def __getattr__(self, name):
        if name in ("sleep", "maybe_idle"):
            return getattr(self._mouse, name)
        if name not in _ACTIONS:
            raise AttributeError(name)

        def perform(*args, **kwargs):
            with self._session._action_lock:
                if not self._session.allowed(self._owner, name, args, kwargs):
                    return False
                result = getattr(self._mouse, name)(*args, **kwargs)
                # 旧 Mouse 成功时返回 None；只有显式 False 才代表门禁/后端拒绝。
                return result is not False
        return perform
