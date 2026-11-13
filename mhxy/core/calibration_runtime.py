"""标定项的运行时坐标和尺度转换；只处理内存画面，不调整游戏窗口。

``VisualObserver`` 是任务和这些规则之间的 seam。它把当前窗口上下文、任务
名称和模板实际来源放在同一个显式对象里；调用者不需要把这些信息暂存在任务
实例上，也不会因为先扫描了另一个窗口而复用旧的尺寸。
"""

from . import calibration, vision, window as win_mod


class VisualObserver:
    """绑定一次识别所需的上下文；不修改任务或上下文对象。"""

    def __init__(self, ctx, task, template_sources=None):
        if ctx is None or not getattr(ctx, "window", None):
            raise ValueError("视觉观察需要窗口上下文")
        self.ctx = ctx
        self.task = task
        self.task_name = task if isinstance(task, str) else task.name
        self.template_sources = dict(template_sources or {})

    def item_size(self, key, group="templates", source=None):
        source_task, source_key = source or self.template_sources.get(key, (self.task_name, key))
        return item_size(self.ctx, source_task, group, source_key)

    def region_rect(self, key, value):
        return region_rect(self.ctx, self.task_name, key, value)

    def scene_rect(self, regions, key="scene"):
        return self.region_rect(key, regions.get(key))

    def grab_scene(self, regions, key="scene"):
        rect = self.scene_rect(regions, key)
        return rect, (win_mod.grab(rect) if rect else None)

    def match(self, scene, template, key, threshold, *, source=None):
        return match_flag(self.task_name, scene, template, key, threshold, self.ctx,
                          source=source or self.template_sources.get(key))

    def match_scene(self, scene, rect, template, key, threshold, *, source=None):
        if scene is None or template is None or rect is None:
            return None
        hit = self.match(scene, template, key, threshold, source=source)
        if hit is None:
            return None
        return rect[0] + hit[0], rect[1] + hit[1], hit[2]

    def match_subregion(self, regions, template, key, threshold, x_frac, y_frac):
        rect, scene = self.grab_scene(regions)
        if rect is None or scene is None or template is None:
            return None
        sh, sw = scene.shape[:2]
        x0 = max(0, int(sw * x_frac[0]))
        x1 = min(sw, int(sw * x_frac[1]))
        y0 = max(0, int(sh * y_frac[0]))
        y1 = min(sh, int(sh * y_frac[1]))
        if x1 - x0 < 1 or y1 - y0 < 1:
            return None
        hit = self.match(scene[y0:y1, x0:x1], template, key, threshold)
        if hit is None:
            return None
        return rect[0] + x0 + hit[0], rect[1] + y0 + hit[1], hit[2]


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


def match_flag(task, scene, template, key, threshold, ctx, *, source=None):
    """用显式窗口上下文匹配单项模板并返回当前截图的局部坐标。

    ``source`` 用于模板来自其它任务命名空间的情况；省略时使用当前任务和
    ``key``。不再从 task 实例读取隐式上下文或模板来源。
    """
    if scene is None or template is None:
        return None
    rect = ctx.window.rect()
    if isinstance(task, str):
        task_name = task
    else:
        task_name = task.name
    source_task, source_key = source or (task_name, key)
    size = item_size(ctx, source_task, "templates", source_key)
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
