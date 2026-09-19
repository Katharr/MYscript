# -*- coding: utf-8 -*-
"""
活动列表「卡片 → 卡片右侧的参加按钮」定位（与玩法无关，运镖/宝图/秘境/蹈海去共用）。

背景（为什么要有这个模块）：
  「开活动 → 找卡片 → 点卡片右侧的『参加』」是四个任务共用的同一段逻辑，原先每个任务各抄一份
  `_find_join_on_row`。踩坑（2026-09-18 用户实测「认出运镖但没找到右侧参加」，三个号无法同时认出）：
  ① 判「参加」用的是【全局阈值】(默认 0.85) + cv2.TM_CCOEFF_NORMED。参加是 30×15 的小字按钮，
     同一张卡的同一颗按钮，只因卡片底色/进度文字不同，分数就在 0.72~1.00 之间飘——
     实测真按钮 0.8496 被 0.85 卡掉，而列表别处一颗无关的按钮 0.8491 几乎追平。「真按钮刚好差一点」
     就是「怎么调阈值都顾此失彼」的根因：**绝对分数对这么小的文字模板没有判别力**。
  ② 用【列表列等分】猜「卡片右缘」再裁剪，边界压在别人卡上：用户列表区 [146,130,464,233] 的等分线
     在局部 x=232，而运镖图标中心在 x=32 —— 第二张卡片的「参加」按钮正好被切进裁剪带 2px。
     随滚动/尺寸/标定误差，随时可能把邻卡的按钮扫进来甚至胜出（点到隔壁）。
  ③ 取条目和取按钮各自截图一次，中间的动画/过渡会让按钮挪位。
  ④ 条目（小图标）模板本身可能「撞脸」：实测运镖的狮头图标对【蹈海去】的图标也能给 0.877，
     只看条目最高分就会认错卡片，于是去找的按钮压根不在那张卡上。

本模块怎么修（一条铁律：**位置靠几何绑定推，分数只用来在几何候选里排序，不用来一刀切**）：
  - 几何绑定：参加按钮与它那张卡的条目【同一行】、在条目中心的右侧。于是搜索带由【条目命中点】
    直接推出（不再猜卡片右缘）：纵向以条目中心为中线开一条带，横向从条目中心往右取
    「条目模板宽 × join_reach_ratio」（默认 12，够到按钮、又在邻卡按钮之前收住），再收到识别区右缘。
  - 尺寸兼容（两级，互不冲突）：
    ① 【方案二·首选】识别前把【内存里的截图】预缩放回标定基准尺度（cv2.resize，绝不动游戏窗口），
       于是模板能照原样匹配；命中坐标再乘 1/scale 换回当前屏幕坐标。缩放的实现与坐标反算封装在
       core/window.ScaledScene（单一出口，别在任务里自己乘）；阈值按缩放比动态放宽
       （core/vision.scaled_threshold，实测依据见其 docstring）。本模块自己做这一层，
       故【无论调用方传的是 grab() 的原始图还是 ScaledScene.img，都只需要传 calib_size】。
    ② 多尺度兜底：拿不到标定尺寸、或预缩放后仍差一点时，围绕 1.0 上下试几个模板尺度。
       尺寸没变时只试 1.0，零额外开销。
  - 分数只排序：几何带内取最高分，接受下限 = join_min_score（默认 0.6；不配则取阈值的 85%）。
    日志打印「实得分 / 用的下限 / 模板尺度」，一眼看出是模板问题还是阈值问题。
  - 互相印证选卡片：条目模板可能多个位置都过阈值（撞脸），故把过阈值的条目候选都取出来，
    各自算「它右侧的参加按钮得分」，选【条目分 + 0.5×参加分】最高的那一对——认错卡片时右侧
    通常没有真按钮，自然败给正确的那对；认对了卡片就必然选到它自己那颗按钮。
  - 调用方先截一张图，条目和按钮都用它（消除两次截图之间的位移）。

调用方（任务）只需一段：
    calib_sz = calib_profiles.active_size(ctx.cfg)      # 当前激活尺寸组的尺寸；没标定过是 None
    scene = win_mod.grab(list_rect)                     # 或 win_mod.grab_scene(list_rect, calib_sz).img
    got = list_row.locate_card(scene, list_rect, anchor_tpl, join_tpl, threshold,
                               calib_sz, loop, log=ctx.log,
                               window_rect=ctx.window.rect())
    if got: ctx.mouse.click(got.join_x, got.join_y)
返回坐标一律是【屏幕绝对坐标】（方案二的缩放反算已在模块内部做完，调用方不必管）。
`cfg` 传任务 loop 字典即可（读 join_* 项）。
"""

import math

import cv2

from . import vision

# 多尺度搜索：围绕 1.0 按步长上下取，最多几档；始终包含 1.0。
_SCALE_STEP = 0.15
_MAX_SCALE_STEPS = 3
# 各配置项的兜底默认（任务 loop 里可覆盖，见 core/config.py 的注释）
_DEF_REACH_RATIO = 12.0    # 横向搜索：条目中心往右最多「条目宽 × 这么多」
_DEF_BAND_RATIO = 3.2      # 纵向搜索带 = 条目模板高 × 这么多（罩住本卡、不探进上下邻卡）
_DEF_MIN_SCORE = 0.6       # 参加按钮的绝对接受下限兜底（loop.join_min_score 优先，默认见 config.py 的 0.7）
_MAX_ANCHORS = 6           # 条目候选最多取几个（防撞脸时只在极少数候选里做互相印证）


class CardHit:
    """一次定位结果。坐标均为屏幕绝对坐标。

    anchor_*：条目（卡片）模板的命中点与分数。
    join_*  ：参加按钮的命中点、分数，以及命中时所用的模板缩放比。
    """

    __slots__ = ("anchor_x", "anchor_y", "anchor_score",
                 "join_x", "join_y", "join_score", "join_scale")

    def __init__(self, anchor_x, anchor_y, anchor_score,
                 join_x, join_y, join_score, join_scale):
        self.anchor_x = anchor_x
        self.anchor_y = anchor_y
        self.anchor_score = anchor_score
        self.join_x = join_x
        self.join_y = join_y
        self.join_score = join_score
        self.join_scale = join_scale


# ----------------------------------------------------------------------
# 小工具
# ----------------------------------------------------------------------
def _scaled(tpl, scale):
    """把模板缩放 scale 倍；scale≈1 直接返回原图（省一次重采样）。"""
    if abs(scale - 1.0) < 0.02:
        return tpl
    h, w = tpl.shape[:2]
    nw, nh = max(6, int(round(w * scale))), max(6, int(round(h * scale)))
    if nw == w and nh == h:
        return tpl
    interp = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
    return cv2.resize(tpl, (nw, nh), interpolation=interp)


def _scales(est):
    """要试的模板缩放比列表（必然含 1.0，按离 est 由近到远排）。
    est = 「当前窗口尺寸 ÷ 标定基准尺寸」估的尺度；尺寸一致时 est≈1，只试 1.0，零额外开销。
    est=None（没标定基准/取不到窗口尺寸）也只试 1.0，与旧行为一致。"""
    if est is None or abs(est - 1.0) < 0.02:
        return [1.0]
    out = [1.0]
    for i in range(1, _MAX_SCALE_STEPS + 1):
        for s in (1.0 + _SCALE_STEP * i, 1.0 - _SCALE_STEP * i):
            if 0.5 <= s <= 2.0:
                out.append(round(s, 3))
    out.sort(key=lambda s: abs(s - est))
    return out


def estimate_scale(window_rect, calib_size):
    """估当前画面相对「标定时窗口尺寸」的缩放比（几何平均，抗单维误差）。
    window_rect 必须是【当前窗口矩形】(w/h 与 calib_size 同一坐标系)。
    拿不到尺寸返回 None（=不做多尺度）。"""
    if not window_rect or not calib_size or len(calib_size) < 2:
        return None
    try:
        w, h = float(window_rect[2]), float(window_rect[3])
        bw, bh = float(calib_size[0]), float(calib_size[1])
        if w <= 0 or h <= 0 or bw <= 0 or bh <= 0:
            return None
        return max(0.5, min(2.0, math.sqrt((w / bw) * (h / bh))))
    except Exception:
        return None


def _band_h(anchor_h, join_h, cfg):
    """纵向搜索带高度：以条目中心为中线、上下各一半。
    按模板高的比例算（换窗口尺寸自动跟着缩放），再给下限防模板太小框不住按钮。"""
    ratio = float((cfg or {}).get("join_band_ratio") or _DEF_BAND_RATIO)
    return max(int((anchor_h or 20) * ratio), int((join_h or 15) * 2.6), 32)


def _accept_floor(match_threshold, cfg):
    """参加按钮的接受下限：优先 loop.join_min_score；否则取「阈值的 85%」。
    再兜一个 0.6 的绝对地板——比这分还低说明那不是「参加」，宁可判没找到、让状态机重试。"""
    raw = (cfg or {}).get("join_min_score")
    if raw is None:
        raw = float(match_threshold) * 0.85
    return max(_DEF_MIN_SCORE, float(raw))


def _anchor_peaks(scene, tpl, threshold, scales, norm=1.0):
    """列出条目模板在 scene 里所有 ≥ 阈值、且互不重叠（各向至少隔开半个模板）的候选命中点。
    返回 [(x, y, score)]，按分数降序。返回空 = 没认出条目。

    norm = 画面已被预缩放的比例（1.0=没缩放）。缩放过的画面边缘被重采样平滑、分数会掉，
    故按 vision.scaled_threshold 动态放宽阈值（与单模板匹配同一套依据，别在这里另写一份）。
    ⚠ 调用点上 norm 与 scales 互斥（预缩放过就只试 1.0、没预缩放才试模板缩放），故两处放宽不会叠加。

    为什么要多尺度：拿不到标定尺寸（或预缩放后仍差一点）时，模板和画面尺度不一致，
    条目会【第一个认不出】，后面的按钮定位根本没机会跑。故这里按 estimate_scale 估的尺度一并试
    （尺度 1.0 是标定态、照用户阈值卡；其它尺度是兜底，阈值再放宽 10% 防误认）。

    为什么不止取最高分：小图标模板会「撞脸」（实测运镖图标对蹈海去图标也给 0.877），
    只认最高分可能认错卡片；多个候选交给上层用「右侧有没有真参加按钮」互相印证。"""
    th, tw = tpl.shape[:2]
    sh, sw = scene.shape[:2]
    peaks = []
    for sc in scales:
        t = _scaled(tpl, sc)
        th_, tw_ = t.shape[:2]
        if sh < th_ or sw < tw_:
            continue
        # 阈值：非 1.0 的模板尺度再放宽 10%（兜底档位是粗步长，模板缩放本身就有近似误差）。
        # 注意调用点上 scales 与 norm 互斥：画面已预缩放时 scales=[1.0]、这个 0.9 分支不会走；
        # 只有「没做预缩放、靠模板缩放兜底」时才生效，两者不会叠加放宽。
        need = threshold if abs(sc - 1.0) < 0.02 else max(0.5, threshold * 0.9)
        if norm and abs(norm - 1.0) >= 0.02:
            need = vision.scaled_threshold(need, norm)
        res = cv2.matchTemplate(scene, t, cv2.TM_CCOEFF_NORMED)
        flat = res.ravel()
        for idx in flat.argsort()[::-1][:64]:
            score = float(flat[idx])
            if score < need:
                break
            yy, xx = divmod(int(idx), res.shape[1])
            cx, cy = xx + tw_ // 2, yy + th_ // 2
            if any(abs(cx - px) < max(4, tw // 2) and abs(cy - py) < max(4, th // 2)
                   for px, py, _ in peaks):
                continue
            peaks.append((cx, cy, score))
    # 同分并列（撞脸图标很常见）时取更靠上靠左的那个，保证同一画面每次判定一致
    peaks.sort(key=lambda p: (-p[2], p[1], p[0]))
    return peaks[:_MAX_ANCHORS]


def _tpl_shape(tpl):
    h, w = tpl.shape[:2]
    return f"{w}x{h}"


# ----------------------------------------------------------------------
# 主入口
# ----------------------------------------------------------------------
def locate_card(scene, area_rect, anchor_tpl, join_tpl,
                match_threshold=0.85, calib_size=None, cfg=None, log=None,
                window_rect=None):
    """在活动列表截图 scene 里定位「卡片 + 它右侧的参加按钮」。

    参数：
      scene           该列表区域的 BGR 截图（调用方截一次，条目/按钮都用它）
      area_rect       该截图的屏幕矩形 [x, y, w, h]（局部坐标 → 屏幕绝对坐标）
      anchor_tpl      条目（卡片）模板；join_tpl=参加按钮模板
      match_threshold 用户设的匹配阈值：条目候选照它卡；参加按几何带内的最高分接受，
                      下限见 _accept_floor（默认 0.6）。
      calib_size      标定时记录的窗口尺寸 [w,h]（传 core/calib_profiles.active_size()）。要和 window_rect 成对传。
      cfg             任务 loop 配置（join_reach_ratio / join_band_ratio / join_min_score）
      log             可选日志回调 log(msg, level=...)
      window_rect     当前窗口矩形（估缩放用；缺省退回 area_rect，只在列表区≈整窗时才准）

    返回 CardHit；返回 None = 根本没认出条目（该继续滚动找）。
    认出了条目但按钮没匹配上：返回 CardHit 且 join_x is None——调用方据此打「认出了但找不到参加」
    的提示并原地重试（不要把条目滚走）。
    """
    cfg = cfg or {}
    if scene is None or area_rect is None or anchor_tpl is None or join_tpl is None:
        return None
    est = estimate_scale(window_rect or area_rect, calib_size)   # 当前窗口 ÷ 标定基准
    # ① 方案二：把画面预缩放回标定基准尺度（只在内存里缩，绝不动游戏窗口），再照原样匹配模板。
    #    缩放比取 est 的【倒数】（est=画面相对基准的尺度；要缩回基准就得乘 1/est）。
    #    命中坐标属于缩放后的画面，末尾统一用 1/norm 换回屏幕绝对坐标。
    norm = 1.0
    if est is not None and abs(est - 1.0) >= 0.02:
        try:
            scene, norm = _normalize(scene, 1.0 / est)
        except Exception:
            scene, norm = scene, 1.0
        if log and abs(norm - 1.0) >= 0.02:
            log(f"窗口尺寸与标定基准不同：画面预缩放 {norm:.3f}× 回基准尺度后匹配"
                f"（阈值按 vision.scaled_threshold 动态放宽）", level="debug")
    # ② 多尺度兜底：预缩放没做成（拿不到尺寸/异常）时，仍按估的尺度试几档模板缩放。
    scales = [1.0] if abs(norm - 1.0) >= 0.02 else _scales(est)
    peaks = _anchor_peaks(scene, anchor_tpl, match_threshold, scales, norm=norm)
    if not peaks:
        return None
    floor = _accept_floor(match_threshold, cfg)
    # 条目可能命中多处（撞脸）：每处都去找它自己右侧的参加按钮，
    # 取「条目分 + 0.5×参加分」最高的一对——认错卡片时右侧通常没有真按钮，自然落选。
    best_res = None          # (pair_score, anchor, join_attempt)
    for px, py, pscore in peaks:
        attempt = _locate_join(scene, anchor_tpl, join_tpl, (px, py), cfg, scales)
        pair = pscore + 0.5 * (attempt[2] if attempt else 0.0)
        if best_res is None or pair > best_res[0]:
            best_res = (pair, (px, py, pscore), attempt)
    _, (ax, ay, ascore), attempt = best_res

    if len(peaks) > 1 and log:
        log(f"条目模板命中 {len(peaks)} 处（最高 {peaks[0][2]:.3f}@{peaks[0][0]},{peaks[0][1]}），"
            f"按「右侧有没有参加按钮」定为 @{ax},{ay}（{ascore:.3f}）", level="debug")
    if attempt is None:
        return CardHit(*_to_screen(area_rect, ax, ay, norm), ascore, None, None, None, None)
    jx, jy, jscore, jscale = attempt
    if jscore < floor:
        if log:
            ah0 = anchor_tpl.shape[0]
            jw, jh = join_tpl.shape[1], join_tpl.shape[0]
            log(f"条目「{_tpl_shape(anchor_tpl)}」@{ax},{ay}（{ascore:.3f}）右侧没找到「参加」："
                f"最佳候选 {jscore:.3f} < 下限 {floor:.2f}（参加模板 {jw}x{jh}，模板尺度 {jscale:.2f}，"
                f"行带高 {_band_h(ah0, jh, cfg)}）", level="warn")
        return CardHit(*_to_screen(area_rect, ax, ay, norm), ascore, None, None, None, None)
    rx, ry = _to_screen(area_rect, ax, ay, norm)
    sjx, sjy = _to_screen(area_rect, jx, jy, norm)
    return CardHit(rx, ry, ascore, sjx, sjy, jscore, jscale)


def _normalize(scene, norm):
    """把画面按 norm 缩放（缩小时 INTER_AREA / 放大时 INTER_LINEAR）。返回 (新图, 实际缩放比)。
    实际比例从缩放前后宽高反算，保证坐标反算与真实重采样一致（不是拿估算值硬套）。"""
    h, w = scene.shape[:2]
    nw, nh = max(6, int(round(w * norm))), max(6, int(round(h * norm)))
    if nw == w and nh == h:
        return scene, 1.0
    interp = cv2.INTER_AREA if norm < 1.0 else cv2.INTER_LINEAR
    out = cv2.resize(scene, (nw, nh), interpolation=interp)
    return out, (nw / float(w))


def _to_screen(area_rect, x, y, norm):
    """把（可能被预缩放的）画面坐标换回屏幕绝对坐标。norm≈1 时就是单纯的加偏移，与旧行为一致。"""
    if x is None or y is None:
        return (None, None)
    s = norm or 1.0
    if abs(s - 1.0) < 0.001:
        return (area_rect[0] + int(x), area_rect[1] + int(y))
    return (area_rect[0] + int(round(x / s)), area_rect[1] + int(round(y / s)))


def _locate_join(scene, anchor_tpl, join_tpl, anchor_local, cfg, scales):
    """在条目所在行的右侧找参加按钮。返回【缩放后画面】坐标 (x, y, score, scale)；
    该行右侧根本放不下模板/取不到区域返回 None（注意：分数是否达标由上层按 floor 判，
    这样「差一点点」也留得下诊断日志）。坐标由上层统一 _to_screen() 换回屏幕绝对坐标。"""
    sh, sw = scene.shape[:2]
    ah, aw = anchor_tpl.shape[:2]
    jh, jw = join_tpl.shape[:2]
    ax, ay = anchor_local
    # 几何带按【模板尺寸】比例算。画面已预缩放回基准尺度时，模板像素与画面像素同尺度，
    # 比例天然成立；没缩放时由 _scales 里的模板缩放兜底。
    band = _band_h(ah, jh, cfg)
    y0 = max(0, ay - band // 2)
    y1 = min(sh, ay + band // 2)
    # 横向：从条目中心往右，最多到「条目宽 × join_reach_ratio」（默认 12：
    #   够到本卡的参加、又收在邻卡按钮之前）；仍受识别区右缘限制。
    reach = float(cfg.get("join_reach_ratio") or _DEF_REACH_RATIO)
    x0 = max(0, ax - aw // 4)          # 往左回退一点：容忍条目中心判偏右几个像素
    x1 = min(sw, max(ax + int(aw * reach), ax + aw))
    if y1 - y0 < jh or x1 - x0 < jw:
        return None

    sub = scene[y0:y1, x0:x1]
    best = None
    for sc in scales:
        t = _scaled(join_tpl, sc)
        th, tw = t.shape[:2]
        if sub.shape[0] < th or sub.shape[1] < tw:
            continue
        res = cv2.matchTemplate(sub, t, cv2.TM_CCOEFF_NORMED)
        _, mx, _, loc = cv2.minMaxLoc(res)
        if best is None or mx > best[2]:
            best = (x0 + loc[0] + tw // 2, y0 + loc[1] + th // 2, float(mx), sc)
    if best is None:
        return None
    return best
