# -*- coding: utf-8 -*-
"""方案二（跨分辨率预缩放 + 坐标反算）纯逻辑自测：不碰游戏、不碰屏幕，全用合成图。
覆盖：window.ScaledScene 的缩放比/坐标反算、vision.scaled_threshold 的分段、
list_row.locate_card 在不同画面尺度下把「参加」点击坐标算回屏幕坐标。

跑法（项目根目录；Windows 控制台默认 GBK，输出已避开生僻字符）：
    python -m tools.check_scale_normalize
"""
import sys

import numpy as np
import cv2

from mhxy.core import window as win_mod
from mhxy.core import vision
from mhxy.core import list_row

BASE_W, BASE_H = 800, 600
# 标定基准尺度下的位置（中心点）
CARD = (150, 200)
JOIN = (420, 200)
CARD_SIZE = (60, 48)
JOIN_SIZE = (30, 15)


def _draw_scene(sc, bg=44):
    """按尺度 sc 【原生渲染】一张「活动列表」：纯色底 + 卡片图标 + 它右侧的「参加」小按钮 + 卡片内纹理线。

    为什么不加随机噪点、也不拿「基准图 resize」来造缩放场景：
    - resize 基准图会把它的噪点一起插值，与模板的噪点不再逐像素一致，分数虚低
      （实测 1.0→0.9→1.0 后条目只剩 0.45），那是在测插值质量而不是几何/坐标换算；
    - 跨尺度的噪点本来也不可能逐像素一致（游戏是重新渲染的），加了只会污染结论。
    真实场景里同样是「底色 + 高对比几何/文字」，故这里用纯色底 + 锐利几何边缘，
    跨尺度后唯一的差异就是游戏重渲染/重采样带来的边缘平滑——正是要验证的东西。
    """
    w, h = int(BASE_W * sc), int(BASE_H * sc)
    img = np.full((h, w, 3), bg, np.uint8)
    img[:, :, 2] = bg + 15

    def box(cx, cy, sw, sh, color):
        x0, y0 = int(round(cx * sc - sw * sc / 2)), int(round(cy * sc - sh * sc / 2))
        x1, y1 = int(round(cx * sc + sw * sc / 2)), int(round(cy * sc + sh * sc / 2))
        cv2.rectangle(img, (x0, y0), (x1, y1), color, -1)
        cv2.rectangle(img, (x0, y0), (x1, y1), (255, 255, 255), max(1, int(round(sc))))
        return x0, y0, x1, y1

    x0, y0, x1, y1 = box(*CARD, *CARD_SIZE, (30, 120, 220))
    # 卡片内只放几笔「粗而疏」的深色斜纹（像真实图标/文字那样低频），
    # 不放细密条纹——细条纹是高频信号，跨尺度重采样后必然错位，只会把测试变成测混叠。
    step = max(4, int(round(14 * sc)))
    for k in range(step, max(step + 1, (x1 - x0) - step // 2), step):
        cv2.line(img, (x0 + k, y1 - max(2, int(4 * sc))), (x0 + k, y0 + max(2, int(4 * sc))),
                 (25, 60, 120), max(2, int(round(3 * sc))))
    box(*JOIN, *JOIN_SIZE, (60, 200, 255))
    return img


def make_scene(sc=1.0, seed=None):
    """兼容旧调用：返回按尺度 sc 原生渲染的场景。"""
    return _draw_scene(sc)


def crop(img, center, size, sc=1.0):
    x0 = int(round(center[0] * sc - size[0] * sc / 2))
    y0 = int(round(center[1] * sc - size[1] * sc / 2))
    w, h = int(round(size[0] * sc)), int(round(size[1] * sc))
    return img[y0:y0 + h, x0:x0 + w].copy()


def check(name, cond, extra=""):
    print(("  OK  " if cond else "  FAIL") + f"  {name} {extra}")
    if not cond:
        raise AssertionError(name)


def near(a, b, tol):
    return abs(a - b) <= tol


# ----------------------------------------------------------------------
def test_scaled_scene():
    print("[1] window.ScaledScene：缩放比 + 坐标反算")
    rect = [100, 50, 1521, 1198]
    calib = [1521, 1198]
    sc = win_mod.grab_scene(rect, calib)
    check("尺寸一致时 scale=1.0", near(sc.scale, 1.0, 1e-9), sc.scale)
    check("尺寸一致时不归一化", sc.normalized is False)
    check("坐标原样（加窗口偏移）", sc.to_screen(200, 300) == (300, 350), sc.to_screen(200, 300))

    # 窗口被拉到 1.5 倍：画面要缩回基准 → 缩放后命中点 = 屏幕点 * 1/1.5
    rect2 = [100, 50, 2281, 1797]      # ≈1521*1.5 x 1198*1.5
    sc2 = win_mod.grab_scene(rect2, [2281, 1797])
    s = 1521 / 2281.0
    sc3 = win_mod.grab_scene(rect2, calib)
    check("窗口变大 → scale<1（把画面缩小）", sc3.scale < 1.0, sc3.scale)
    check("scale ≈ 基准/当前（几何平均）", near(sc3.scale, (1521 / 2281.0 * 1198 / 1797.0) ** 0.5, 0.02), sc3.scale)
    x, y = sc3.to_screen(150, 150)
    check("反算：缩放后150 → 屏幕 x≈150/scale",
          near(x, 100 + 150 / sc3.scale, 2) and near(y, 50 + 150 / sc3.scale, 2), (x, y))
    check("small 窗口（0.7x）→ scale>1（把画面放大）",
          win_mod.grab_scene([0, 0, 1065, 839], calib).scale > 1.0)
    check("没传 calib_size → 不缩放", win_mod.grab_scene(rect2).scale == 1.0)
    check("传 None 尺寸 → 不缩放", win_mod.grab_scene(rect2, None).normalized is False)
    check("非法尺寸不炸", win_mod.grab_scene(rect2, [0, 0]).scale == 1.0)


def test_threshold():
    print("[2] vision.scaled_threshold：按缩放比分段放宽")
    check("scale=1.0 原样返回（零回归）", vision.scaled_threshold(0.85, 1.0) == 0.85
          and vision.scaled_threshold(0.85, None) == 0.85
          and vision.scaled_threshold(0.85, 1.01) == 0.85)
    check("1.2x 保持 0.85", vision.scaled_threshold(0.85, 1.2) == 0.85)
    check("0.8x → 0.80", vision.scaled_threshold(0.85, 0.8) == 0.80)
    check("0.7x → 0.75", vision.scaled_threshold(0.85, 0.7) == 0.75)
    check("下限不低于 0.75（0.5x 也不更低）", vision.scaled_threshold(0.85, 0.5) == 0.75)
    check("用户阈值比放宽值更低时用用户的", vision.scaled_threshold(0.70, 0.8) == 0.70)


def test_list_row_geometry():
    print("[3] list_row.locate_card：不同画面尺度下的命中与坐标反算")
    base = _draw_scene(1.0)
    anchor = crop(base, CARD, CARD_SIZE)
    join = crop(base, JOIN, JOIN_SIZE)
    assert anchor.size and join.size

    for sc in (1.0, 0.9, 0.8, 0.7, 1.2, 1.4):
        scene = _draw_scene(sc)
        # 真实场景：模板是在 1.0x（800×600）窗口下标定的；现在窗口被改成 sc 倍，
        # 于是 window_rect/area_rect 都是 sc 倍尺寸、calib_size 仍是基准尺寸。
        rect = [1000, 500, scene.shape[1], scene.shape[0]]
        got = list_row.locate_card(scene, rect, anchor, join, 0.85,
                                   calib_size=[BASE_W, BASE_H],
                                   cfg={"join_min_score": 0.6},
                                   window_rect=list(rect))
        exp_x = 1000 + JOIN[0] * sc
        exp_y = 500 + JOIN[1] * sc
        if got is not None and got.join_x is not None:
            check(f"{sc}x 命中参加且坐标不歪（屏幕坐标系）",
                  near(got.join_x, exp_x, 6) and near(got.join_y, exp_y, 6),
                  f"got=({got.join_x},{got.join_y}) exp=({exp_x:.0f},{exp_y:.0f}) "
                  f"score={got.join_score:.3f}")
        else:
            check(f"{sc}x 命中参加", False,
                  "got=None" if got is None else "join未匹配（分<下限）")

    # 多尺度兜底（方案二没条件做时）的「能力边界」，记录实测值而不是硬性断言：
    # 画面 0.9x、calib_size=None 时估的尺度 1.111 落在 _scales 的粗档位上（最近 1.15），
    # 条目分只有 0.436、认不出来 —— 这正是「预缩放才是主力，模板缩放只是兜底」的实证。
    # （真实场景里每个窗口都会带标定组，故这条路径几乎不会走到。）
    scene = _draw_scene(0.9)
    rect = [0, 0, scene.shape[1], scene.shape[0]]
    est = list_row.estimate_scale(rect, None)
    scales = list_row._scales(est)
    got = list_row.locate_card(scene, rect, anchor, join, 0.85,
                               calib_size=None, cfg={"join_min_score": 0.6},
                               window_rect=list(rect))
    best_anchor = max(vision.best_score(scene, list_row._scaled(anchor, s))[0] for s in scales)
    print(f"  记录  未标定 + 画面 0.9x：模板缩放档位 {scales}，最高分 {best_anchor:.3f}，"
          f"识别结果={got is not None}")
    check("未标定 + 画面 0.9x 时不会误报（宁可不认，也不能乱点）", got is None or got.join_x is None)

    # 未标定 + 1.0x：行为必须与旧版一致（只试 1.0 尺度，坐标 = 画面坐标 + 区偏移）

    # 未标定 + 1.0x：行为必须与旧版一致（只试 1.0 尺度，坐标 = 画面坐标 + 区偏移）
    got = list_row.locate_card(base, [0, 0, BASE_W, BASE_H], anchor, join, 0.85,
                               calib_size=None, cfg={}, window_rect=None)
    check("未标定 1.0x 命中且坐标 = 画面坐标 + 区偏移（零回归）",
          got is not None and got.join_x is not None
          and near(got.join_x, JOIN[0], 4) and near(got.join_y, JOIN[1], 4),
          None if got is None else (got.join_x, got.join_y))

    # 未标定（calib_size=None）时行为与旧版一致：只试 1.0 尺度
    got = list_row.locate_card(make_scene(1.0), [0, 0, BASE_W, BASE_H], anchor, join, 0.85,
                               calib_size=None, cfg={}, window_rect=None)
    check("未标定时 1.0x 仍命中（零回归）", got is not None and got.join_x is not None)
    check("未标定时坐标 = 画面坐标 + 区偏移",
          got is not None and near(got.join_x, JOIN[0], 4) and near(got.join_y, JOIN[1], 4),
          None if got is None else (got.join_x, got.join_y))


def main():
    test_scaled_scene()
    test_threshold()
    test_list_row_geometry()
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
