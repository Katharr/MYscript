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

    def rect(self):
        """[left, top, width, height]（屏幕绝对坐标，已叠加 offset）。未定位则返回 None。"""
        if not self._win:
            return None
        return [self._win.left + self.offset[0], self._win.top + self.offset[1],
                self._win.width, self._win.height]

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
