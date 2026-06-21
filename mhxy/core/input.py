# -*- coding: utf-8 -*-
"""
底层 + 拟人化的鼠标输入。

·「底层」：默认用 Windows SendInput(user32) 注入鼠标事件——用户态最底层的标准输入接口，
          比 pyautogui 之类高层封装更难被简单规则识别。
·「拟人化」：鼠标走贝塞尔曲线、有加减速、落点随机偏移、按下/抬起与各种间隔均带随机抖动、
            偶尔“走神”停顿。目标是不像机器。

⚠ 真正“完全测不到”需硬件级(KMBox/Arduino)或驱动级注入，是另一量级工程且自身也可能被风控盯。
  本方案是性价比折中，不保证 100% 不被发现。务必小号测试。
"""

import time
import math
import random
import ctypes

user32 = ctypes.windll.user32

# ---- SendInput 结构体与常量 ----
ULONG_PTR = ctypes.POINTER(ctypes.c_ulong)
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_ABSOLUTE = 0x8000
INPUT_MOUSE = 0
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 76, 77, 78, 79


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong), ("dwExtraInfo", ULONG_PTR)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT)]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", ctypes.c_ulong), ("u", _INPUTUNION)]


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def get_cursor():
    p = _POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return (p.x, p.y)


def _virtual_screen():
    return (user32.GetSystemMetrics(SM_XVIRTUALSCREEN),
            user32.GetSystemMetrics(SM_YVIRTUALSCREEN),
            user32.GetSystemMetrics(SM_CXVIRTUALSCREEN),
            user32.GetSystemMetrics(SM_CYVIRTUALSCREEN))


def _to_absolute(x, y):
    vx, vy, vw, vh = _virtual_screen()
    return (int((x - vx) * 65535 / max(1, vw - 1)),
            int((y - vy) * 65535 / max(1, vh - 1)))


def _send(flags, ax=0, ay=0):
    extra = ctypes.c_ulong(0)
    mi = _MOUSEINPUT(ax, ay, 0, flags, 0, ctypes.cast(ctypes.pointer(extra), ULONG_PTR))
    inp = _INPUT()
    inp.type = INPUT_MOUSE
    inp.mi = mi
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))


def _raw_move(x, y):
    ax, ay = _to_absolute(x, y)
    _send(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, ax, ay)


def _bezier(p0, p1, p2, p3, t):
    mt = 1 - t
    x = (mt**3)*p0[0] + 3*(mt**2)*t*p1[0] + 3*mt*(t**2)*p2[0] + (t**3)*p3[0]
    y = (mt**3)*p0[1] + 3*(mt**2)*t*p1[1] + 3*mt*(t**2)*p2[1] + (t**3)*p3[1]
    return x, y


class Mouse:
    """拟人化鼠标。backend: sendinput(默认) / pyautogui / pydirectinput。"""

    def __init__(self, backend="sendinput", humanize=None):
        self.backend = backend
        self.hz = humanize or {}

    def _speed(self, override=None):
        """整体速度倍率：用于缩短拟人化延迟。限制下限避免除零/过慢。
        override 非空时用它（命中下单的『极速』倍率），否则用配置里的常速 speed。"""
        try:
            v = override if override is not None else self.hz.get("speed", 1.0)
            return max(0.2, float(v))
        except (TypeError, ValueError):
            return 1.0

    # ---- 后端无关的底层动作 ----
    def _move(self, x, y):
        if self.backend == "sendinput":
            _raw_move(x, y)
        else:
            self._clk().moveTo(int(x), int(y))

    def _down(self):
        if self.backend == "sendinput":
            _send(MOUSEEVENTF_LEFTDOWN)
        else:
            self._clk().mouseDown()

    def _up(self):
        if self.backend == "sendinput":
            _send(MOUSEEVENTF_LEFTUP)
        else:
            self._clk().mouseUp()

    def _clk(self):
        if self.backend == "pydirectinput":
            import pydirectinput
            pydirectinput.FAILSAFE = True
            return pydirectinput
        import pyautogui
        pyautogui.FAILSAFE = True
        return pyautogui

    # ---- 拟人化动作 ----
    def human_move(self, x, y, speed=None):
        """移动到 (x,y) 附近（带随机落点），返回实际落点。
        speed 非空时按该倍率提速（命中下单用，越快越像直线瞬移）。"""
        sx, sy = get_cursor()
        r = int(self.hz.get("click_radius", 4))
        tx, ty = x + random.randint(-r, r), y + random.randint(-r, r)
        dist = math.hypot(tx - sx, ty - sy)
        spd = self._speed(speed)
        # 极速时步数随倍率收缩（更少步、更直），常速保持原来的平滑曲线
        px_per_step = max(4, self.hz.get("px_per_step", 12) * max(1.0, spd / 1.5))
        steps = max(4, min(80, int(dist / px_per_step) + random.randint(6, 14)))
        off = min(140.0, dist * 0.35 + 8)
        c1 = (sx + (tx - sx) * 0.3 + random.uniform(-off, off),
              sy + (ty - sy) * 0.3 + random.uniform(-off, off))
        c2 = (sx + (tx - sx) * 0.7 + random.uniform(-off, off),
              sy + (ty - sy) * 0.7 + random.uniform(-off, off))
        for i in range(1, steps + 1):
            t = i / steps
            te = t * t * (3 - 2 * t)  # ease-in-out
            mx, my = _bezier((sx, sy), c1, c2, (tx, ty), te)
            self._move(mx, my)
            time.sleep(random.uniform(0.004, 0.018) / spd)
        return tx, ty

    def click(self, x, y, speed=None):
        """拟人化移动并点击。speed 非空时整段按该倍率提速（命中下单的极速直击）。"""
        spd = self._speed(speed)
        self.human_move(x, y, speed=speed)
        time.sleep(random.uniform(0.03, 0.10) / spd)
        self._down()
        time.sleep(random.uniform(0.04, 0.13) / spd)
        self._up()
        time.sleep(random.uniform(0.02, 0.08) / spd)

    # ---- 拟人化等待 ----
    def sleep(self, base, jitter_ratio=None):
        if jitter_ratio is None:
            jitter_ratio = self.hz.get("interval_jitter", 0.4)
        j = base * jitter_ratio
        time.sleep(max(0.0, base + random.uniform(-j, j)))

    def maybe_idle(self):
        """以一定概率走神停顿，触发返回 True。"""
        if random.random() < self.hz.get("idle_chance", 0.02):
            self.sleep(random.uniform(self.hz.get("idle_min_sec", 1.5),
                                      self.hz.get("idle_max_sec", 5.0)), 0.2)
            return True
        return False
