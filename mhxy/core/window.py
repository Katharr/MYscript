# -*- coding: utf-8 -*-
"""
游戏窗口与屏幕截图。封装“找窗口 / 取窗口矩形 / 截图 / 窗口内坐标换算”，
让上层任务不用关心 mss / pygetwindow 细节。
"""

import time
import ctypes
import threading

import numpy as np
import cv2
import mss
import pygetwindow as gw


def set_dpi_aware():
    """让脚本按真实像素工作，避免 Win 缩放(125%/150%)导致坐标错位。进程级，调一次即可。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


class GameWindow:
    """对一个游戏窗口的封装。"""

    def __init__(self, title_substr, offset=(0, 0)):
        self.title_substr = title_substr
        self.offset = tuple(offset)
        self._win = None

    # ---- 查找与激活 ----
    def locate(self):
        """按标题关键字找窗口，找到返回 True。"""
        candidates = []
        for w in gw.getAllWindows():
            try:
                if self.title_substr in (w.title or "") and w.width > 100 and w.height > 100:
                    candidates.append(w)
            except Exception:
                continue
        if not candidates:
            self._win = None
            return False
        candidates.sort(key=lambda x: (not x.isMinimized, x.width * x.height), reverse=True)
        self._win = candidates[0]
        return True

    @property
    def found(self):
        return self._win is not None

    @property
    def title(self):
        return self._win.title if self._win else ""

    def bind(self, win):
        """直接绑定一个已找到的窗口对象（多开枚举用），返回 self。
        绑定后 rect()/activate() 等都作用在这个固定窗口上，不再自动选最大。"""
        self._win = win
        return self

    def rect(self):
        """[left, top, width, height]（屏幕绝对坐标，已叠加 offset）。未定位/窗口已关返回 None。"""
        if not self._win:
            return None
        try:
            return [self._win.left + self.offset[0], self._win.top + self.offset[1],
                    self._win.width, self._win.height]
        except Exception:
            # 绑定的窗口被关闭后，访问 .left/.width 会抛异常（win32 句柄失效）。
            return None

    def activate(self):
        if not self._win:
            return
        try:
            if self._win.isMinimized:
                self._win.restore()
            self._win.activate()
            time.sleep(0.3)
        except Exception:
            pass

    def resize_to(self, w, h, move_to=None):
        """把窗口尺寸还原到 [w, h]（可选 move_to=(left,top) 一并复位位置）。
        成功(尺寸误差≤4px)返回 True；否则返回 False（游戏锁分辨率档位时 resize 会被忽略）。
        窗口失效/异常也返回 False。"""
        if not self._win:
            return False
        try:
            if self._win.isMinimized:
                self._win.restore()
            self._win.resizeTo(int(w), int(h))
            if move_to is not None:
                self._win.moveTo(int(move_to[0]), int(move_to[1]))
            time.sleep(0.12)
            r = self.rect()
            if r is None:
                return False
            if abs(r[2] - int(w)) > 4 or abs(r[3] - int(h)) > 4:
                # 差太多再试一次（个别窗口首帧未跟上）
                self._win.resizeTo(int(w), int(h))
                time.sleep(0.12)
                r = self.rect()
                if r is None:
                    return False
            return abs(r[2] - int(w)) <= 4 and abs(r[3] - int(h)) <= 4
        except Exception:
            return False

    # ---- 坐标换算 ----
    def region_to_screen_rect(self, region):
        """窗口内 [x,y,w,h] -> 屏幕绝对 [left,top,w,h]。"""
        r = self.rect()
        if r is None or not region:
            return None
        return [r[0] + region[0], r[1] + region[1], region[2], region[3]]

    def region_center_screen(self, region):
        """窗口内 [x,y,w,h] 的中心点 -> 屏幕绝对 (x,y)。"""
        sr = self.region_to_screen_rect(region)
        if sr is None:
            return None
        return (sr[0] + sr[2] // 2, sr[1] + sr[3] // 2)


# ---- 多窗口枚举与目标选择（多开/选择窗口基础特性）----
def locate_all(title_substr, offset=(0, 0), max_n=0):
    """枚举所有标题含 title_substr、非最小化的窗口，按屏幕位置排序后各包一个 GameWindow 返回。

    用于「选择窗口/多开」：用户把多个号并排摆在桌面上，这里把它们稳定地认成 号1/号2/号3…
    排序规则：先按上边缘分行（每 120px 一带），同一行内按左边缘左→右——和肉眼「从左到右数」一致。
    max_n>0 时最多取前 max_n 个。找不到返回空列表。
    """
    found = []
    for w in gw.getAllWindows():
        try:
            if (title_substr in (w.title or "")
                    and w.width > 100 and w.height > 100 and not w.isMinimized):
                found.append(w)
        except Exception:
            continue
    found.sort(key=lambda x: (int(x.top) // 120, int(x.left)))
    if max_n and max_n > 0:
        found = found[:max_n]
    return [GameWindow(title_substr, offset).bind(w) for w in found]


def resolve_targets(title_substr, offset, targets):
    """按 targets 配置从 locate_all 结果里选出要操作的窗口列表（纯函数，供任务与 GUI 共用）。

    targets 结构见 config.DEFAULT_CONFIG["targets"]：
      - 单开(multi=False)：返回 [第 single_index 个窗口]（序号越界自动回退 0）。
      - 多开(multi=True) ：按 multi_indices 选子集（空=全部），再按 max_windows 截断。
    找不到任何窗口返回 []。
    """
    targets = targets or {}
    wins = locate_all(title_substr, offset)
    if not wins:
        return []
    if targets.get("multi"):
        idxs = targets.get("multi_indices") or list(range(len(wins)))
        sel = [wins[i] for i in idxs if 0 <= i < len(wins)]
        if not sel:                       # 选中的序号全失效 → 兜底用全部
            sel = wins
        cap = targets.get("max_windows", 0)
        if cap and cap > 0:
            sel = sel[:cap]
        return sel
    i = targets.get("single_index", 0)
    if not (isinstance(i, int) and 0 <= i < len(wins)):
        i = 0
    return [wins[i]]


def restore_targets_size(title_substr, offset, targets, base_size):
    """把当前选中的目标窗口（单开1个/多开多个）逐个还原到 base_size=[w,h]。

    复用 resolve_targets 选窗，保证和任务实际操作的是同一批号。操作每个号前先 activate()
    切前台再 resize。返回 (ok_count, total, actual_sizes)：
      - ok_count : 成功还原(尺寸误差≤4px)的号数
      - total    : 选中的号数
      - actual_sizes : 各号 resize 后的实际 [w,h]（窗口失效为 None），供上层判断是否真生效。
    base_size 非法(空/非两元素)时返回 (0, 0, [])。
    """
    if not base_size or len(base_size) < 2:
        return (0, 0, [])
    w, h = int(base_size[0]), int(base_size[1])
    wins = resolve_targets(title_substr, offset, targets)
    ok = 0
    actual = []
    for win in wins:
        win.activate()
        success = win.resize_to(w, h)
        r = win.rect()
        actual.append([r[2], r[3]] if r else None)
        if success:
            ok += 1
    return (ok, len(wins), actual)


# ---- 截图 ----
# mss 用 GDI，srcdc 等句柄存在「线程本地」里：在 A 线程建的实例不能在 B 线程用，
# 否则报 'object has no attribute srcdc'。任务跑在后台线程，故每个线程各持一份。
_tls = threading.local()


def _get_sct():
    sct = getattr(_tls, "sct", None)
    if sct is None:
        sct = mss.mss()
        _tls.sct = sct
    return sct


def grab(rect):
    """截取屏幕矩形 [left, top, w, h]，返回 OpenCV BGR 图像。"""
    left, top, w, h = rect
    raw = _get_sct().grab({"left": int(left), "top": int(top),
                           "width": int(w), "height": int(h)})
    img = np.array(raw)  # BGRA
    return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
