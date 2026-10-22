"""标定项的运行时坐标和尺度转换；只处理内存画面，不调整游戏窗口。"""

from . import calibration, vision, window as win_mod


def item_size(ctx, task, group, key):
    cfg = getattr(ctx, "cfg", None)
    return calibration.size_for(cfg, task, group, key) if isinstance(cfg, dict) else None


def region_rect(ctx, task, key, value):
    """将该区域自己的标定坐标换算到当前窗口，留空仍表示整窗。"""
    rect = ctx.window.rect()
    if rect is None or not value:
        return rect
    size = item_size(ctx, task, "regions", key)
    if not size:
        return ctx.window.region_to_screen_rect(value)
    sx, sy = rect[2] / size[0], rect[3] / size[1]
    # 尺寸组允许少量外壳尺寸误差，与截图归一化保持相同容差。
    if abs(rect[2] - size[0]) <= win_mod.SIZE_TOL:
        sx = 1.0
    if abs(rect[3] - size[1]) <= win_mod.SIZE_TOL:
        sy = 1.0
    x, y, w, h = value
    return [rect[0] + round(x * sx), rect[1] + round(y * sy),
            max(1, round(w * sx)), max(1, round(h * sy))]


def match_flag(task, scene, template, key, threshold, ctx=None):
    """匹配单项模板并返回当前截图的局部坐标，不复用其它模板的尺寸。"""
    if scene is None or template is None:
        return None
    ctx = ctx if ctx is not None else getattr(task, "_calibration_ctx", None)
    rect = ctx.window.rect() if ctx is not None else None
    source_task, source_key = getattr(task, "_template_sources", {}).get(key, (task.name, key))
    size = item_size(ctx, source_task, "templates", source_key) if ctx is not None else None
    if rect is None or not size:
        return vision.match(scene, template, threshold)
    # _rect 的宽高用于计算窗口尺度；img 可以是窗口内任意裁剪。
    # 零原点让 ScaledScene 负责将匹配点换回裁剪图局部坐标。
    normalized = win_mod.ScaledScene([0, 0, rect[2], rect[3]], size, img=scene)
    # 旧尺寸组可能记录的是外壳/DPI 尺寸，游戏控件却仍保持原像素大小。
    # 先保留原版严格阈值匹配，未命中再按记录的尺寸缩放；不重写旧归属。
    if normalized.normalized:
        native = vision.match(scene, template, threshold)
        if native is not None:
            return native
    hit = normalized.match(template, threshold)
    if hit is None:
        return None
    x, y = normalized.to_screen(hit[0], hit[1])
    return x, y, hit[2]
