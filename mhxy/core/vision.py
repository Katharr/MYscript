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


# ----------------------------------------------------------------------
# 跨分辨率匹配（方案二：把画面预缩放回标定基准尺度）
# ----------------------------------------------------------------------
# 实测依据（项目真实模板 12 张，合成不同尺度场景，阈值 0.85）：不补偿时只有 1.0x 认得出；
# 预缩放回基准后「位置正确率 100%」，但重采样平滑边缘让小字按钮（如 30×15 的「参加」）掉分，
# 故需按缩放比动态放宽阈值。⚠ 别用灰度/CLAHE：实测非基准命中率 0.0%（连 1.0x 都从 1.000 掉到
# 0.959）——TM_CCOEFF_NORMED 已做均值减法归一化，CLAHE 只会放大噪点并丢掉颜色这个真实判别信息。
_SCALE_RELAX = ((0.75, 0.75), (0.90, 0.80))   # (缩放比上限, 该段用的阈值)；再往上是原阈值
SCALE_MIN = 0.70                              # 低于此缩放无解（笔画信息已丢失），只能重新标定
SCALE_MAX = 1.50


def scaled_threshold(threshold, scale):
    """按缩放比把阈值动态放宽（见上面实测依据）。scale≈1 时【原样返回 threshold】，零回归。

    分段（与实测表格一致）：0.9~1.5x 保持原阈值；0.75~0.9x 用 0.80；0.6~0.75x 用 0.75。
    下限不低于 0.75——再低会引入误认；真正的防线是「位置靠几何绑定推、分数只排序」（见 core/list_row）。
    """
    if scale is None:
        return threshold
    try:
        s = abs(float(scale))
    except (TypeError, ValueError):
        return threshold
    if abs(s - 1.0) < 0.02:
        return threshold
    for hi, relaxed in _SCALE_RELAX:
        if s < hi:
            return min(float(threshold), relaxed)
    return threshold


def match_scaled(scene_bgr, template_bgr, threshold, scale):
    """match() 的跨分辨率版：scene 已被预缩放回标定基准尺度时，用 scale 动态放宽阈值后再匹配。

    scale = 「当前窗口尺寸 ÷ 标定基准尺寸」的缩放比（见 window.ScaledScene.scale）。
    ⚠ 返回的 cx/cy 是【缩放后画面】里的坐标，调用方必须用 window.ScaledScene.to_screen() 或
    自己乘 1/scale 换算回当前屏幕坐标，否则会点歪（见 core/window.ScaledScene 的约定）。
    scale=None/≈1 时行为与 match() 逐字节一致（零回归）。
    """
    return match(scene_bgr, template_bgr, scaled_threshold(threshold, scale))


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
