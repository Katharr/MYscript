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
