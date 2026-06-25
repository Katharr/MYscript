# -*- coding: utf-8 -*-
"""
图像识别。模板匹配 + 兼容中文路径的图片读写。与具体玩法无关，所有任务通用。
"""

import os

import numpy as np
import cv2

from .config import PROJECT_ROOT


def _abspath(path):
    if os.path.isabs(path):
        return path
    return str(PROJECT_ROOT / path)


def load_template(path):
    """读取模板图（兼容中文路径）。失败返回 None。"""
    p = _abspath(path)
    if not os.path.exists(p):
        return None
    data = np.fromfile(p, dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def save_image(path, img):
    """保存图片（兼容中文路径）。"""
    p = _abspath(path)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    ext = os.path.splitext(p)[1] or ".png"
    ok, buf = cv2.imencode(ext, img)
    if ok:
        buf.tofile(p)
    return ok


def match(scene_bgr, template_bgr, threshold):
    """
    在 scene 里找 template。命中返回 (cx, cy, score)，cx/cy 为命中中心相对 scene 左上角；
    未命中返回 None。
    """
    if scene_bgr is None or template_bgr is None:
        return None
    th, tw = template_bgr.shape[:2]
    if scene_bgr.shape[0] < th or scene_bgr.shape[1] < tw:
        return None
    res = cv2.matchTemplate(scene_bgr, template_bgr, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(res)
    if max_val >= threshold:
        return (max_loc[0] + tw // 2, max_loc[1] + th // 2, float(max_val))
    return None


def frame_diff(a, b):
    """两帧平均像素绝对差。形状不一致返回大值（视为仍在变化/不静止）。
    用于「画面是否静止」和「列表滚不动了=到顶/到底」判定。"""
    if a is None or b is None or a.shape != b.shape:
        return 999.0
    return float(np.abs(a.astype(np.int16) - b.astype(np.int16)).mean())


def best_score(scene_bgr, template_bgr):
    """诊断用：返回 template 在 scene 里的【最高匹配分】(不卡阈值)及命中中心 (score, (cx, cy))。
    尺寸不符/空图返回 (0.0, None)。用来判断「模板根本不在画面里(分很低)」还是「在画面里但阈值太高」。"""
    if scene_bgr is None or template_bgr is None:
        return (0.0, None)
    th, tw = template_bgr.shape[:2]
    if scene_bgr.shape[0] < th or scene_bgr.shape[1] < tw:
        return (0.0, None)
    res = cv2.matchTemplate(scene_bgr, template_bgr, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(res)
    return (float(max_val), (max_loc[0] + tw // 2, max_loc[1] + th // 2))
