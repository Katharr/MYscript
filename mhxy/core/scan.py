# -*- coding: utf-8 -*-
"""
通用「滚动查找」——翻列表/翻包裹找目标的统一底层（与具体玩法无关，所有任务共享）。

为什么要它（用户拍板「翻包裹这个功能抽象出来，很多流程会用到」）：
原先每个任务各写一份 `while 滚一屏找模板` 的循环，且「翻没翻完整个列表」靠
**固定次数**（scroll_max_tries，默认 8）猜——背包比 8 屏长就漏找，开列表时停在中间又会漏掉上半截。
本模块把两件事做对、并只做一次：
  ① 先滚到顶：开列表后先往上滚到「滚不动」（顶端），保证向下扫一遍能覆盖整段，不漏上半截。
  ② 帧差判到底：每向下滚一屏后，对比滚动前/后这片列表区域——几乎没变(帧差<end_diff)=滚动条到底了，
     这才是「确实翻完整个列表」的硬信号。固定次数 max_tries 退化为「防死循环」的安全网。
判定阈值 end_diff 取**小**值（默认 2.0）偏保守：图标有动画/高亮时帧差偏高→判不到「到底」→
退回老的 max_tries 行为（最多多滚几屏，不会误判「没货了」而提前收工）。运行日志会打印真实帧差，便于调。

谁来「匹配什么、命中做什么」由调用方的 probe 回调决定，返回四态之一：
  ACCEPT —— 找到并已（在 probe 内）处理完目标（点了参加/双击了道具…），结束查找、成功返回。
  SCROLL —— 当前屏没有目标，向下滚一屏继续找。
  HANDLED—— probe 在本屏就地处理完一个目标（卖/丢/用了一件物品，**改了画面**）但还要继续找下一个：
            【原地不滚、重新 probe 找同屏剩下的可处理物品】，只有同屏确实没有可处理物品了（probe 改返回
            SCROLL）才往下滚一屏——即用户拍板的「卖完一件别急着滚，等同屏找不到能操作的物品时才滚一段」。
            仍计入 max_tries 作安全网：万一动作成功但物品没消失（如「使用」类不一定消失），不会原地死循环。
            （早期曾让 HANDLED 也滚一屏，结果每卖一件就滚一屏太着急，故改成原地续找。）
  STAY   —— 找到了主目标但次级目标还没出现（如认出条目但右侧「参加」没匹配上）：**原地不滚**重试，
            仍计入 max_tries（超了按「翻完没找到」收尾，交回调用方恢复）。避免一滚就把目标滚走。

注入项（不依赖 Task / ctx，teaming 等非 Task 调用方也能用）：
  grab_rect()      -> 该列表区域的屏幕矩形 (x,y,w,h)；返回 None 表示窗口没了/取不到→中止(STOPPED)。
  probe(scene,rect)-> (decision, payload)；scene 是该矩形已截好的 BGR 图，rect 是其屏幕矩形。
  mouse            -> core.input.Mouse；本模块在区域中心做滚轮。
  should_stop()    -> True 则立即停（手动停止/失败保护）。
  sleep(sec)       -> 可被打断的睡眠（每滚一屏后等画面落定，调用方传自己的拟人化抖动睡眠）。
"""

import time

from . import vision
from . import window as win_mod

ACCEPT = "accept"   # probe 已处理完目标，查找成功结束
SCROLL = "scroll"   # 本屏没有，向下滚一屏继续（probe 无副作用，可拿滚前画面判到底）
HANDLED = "handled" # probe 就地处理完一个目标（改了画面）但要继续找：原地不滚、重 probe 找同屏剩下的，找不到才滚
STAY = "stay"       # 主目标在、次级目标缺：原地重试不滚（仍计入 max_tries）


class ScanResult:
    """滚动查找结果。
    outcome: 'accept'（找到）/ 'exhausted'（翻完整段都没有）/ 'stopped'（被停止或区域取不到）。
             ScanProgress 另有 'paused'，表示本次预算用尽、可继续当前一轮。
    payload: probe 在 ACCEPT 时返回的负载（如点中的坐标分数），透传给调用方。
    reached_end: 仅 exhausted 时有意义——True=因「滚到底(帧差判定)」而结束；
                 False=因 max_tries 用尽（可能没翻完，列表比上限长或一直没滚动）。
    reached_top: 已经通过向上滚动确认顶部；默认 False，旧结果不能冒充完整覆盖。
                 判定全段无目标时，必须同时检查 reached_top 和 reached_end。"""

    def __init__(self, outcome, payload=None, scrolls=0, reached_end=False,
                 reached_top=False):
        self.outcome = outcome
        self.payload = payload
        self.scrolls = scrolls
        self.reached_end = reached_end
        self.reached_top = reached_top

    @property
    def found(self):
        return self.outcome == ACCEPT

    @property
    def exhausted(self):
        return self.outcome == "exhausted"

    @property
    def stopped(self):
        return self.outcome == "stopped"

    @property
    def paused(self):
        return self.outcome == "paused"


class ScanProgress:
    """可恢复的一轮严格扫描；只保存覆盖证据，不保存输入许可。

    capture 每次提供新截图和坐标；让出后先核验当前位置。扫描预算只控制
    本次占用鼠标的时间，整轮的次数/时间上限独立计算，绝不把预算耗尽当无目标。
    """

    def __init__(self):
        self.phase = "top"
        self.reached_top = False
        self.stable = 0
        self.previous = None
        self.checkpoint = None
        self.rect = None
        self.pending = False
        self.pending_since = 0.0
        self.scrolls = 0
        self.started = time.monotonic()
        self.last_reason = ""
        self.timings = {"observe": 0.0, "probe": 0.0, "scroll": 0.0, "wait": 0.0}

    def advance(self, *, capture, probe, mouse, should_stop, sleep,
                scroll_step=-3, max_scrolls=8, budget_sec=3.0,
                min_settle_sec=0.16, settle_sec=0.35, end_diff=2.0,
                total_scrolls=256, timeout_sec=120.0):
        """推进一小段；paused 保留进度，exhausted 仅代表确认从顶扫到底。

        未变化的早期截图必须等够 settle_sec 后再确认，防游戏尚未刷新被判到底。
        动画未落定时只等待，不继续滚动；小目标由 probe 优先于边界判断检查。
        """
        call_start = time.monotonic()
        actions = 0
        unchanged_wait = max(0.05, settle_sec)

        def measured(key, fn, *args, **kwargs):
            start = time.monotonic()
            try:
                return fn(*args, **kwargs)
            finally:
                self.timings[key] += time.monotonic() - start

        def result(outcome, payload=None, reason=""):
            self.last_reason = reason
            return ScanResult(outcome, payload, self.scrolls,
                              outcome == "exhausted", self.reached_top)

        def expired():
            return (self.scrolls >= total_scrolls
                    or time.monotonic() - self.started >= timeout_sec)

        def observe():
            if should_stop():
                return None
            frame, rect = measured("observe", capture)
            if should_stop() or not rect or not _valid_scan_scene(frame):
                return None
            return frame, tuple(rect)

        if int(scroll_step) >= 0:
            return result("stopped", reason="invalid_step")
        current = observe()
        if current is None:
            return result("stopped", reason="observation")
        frame, rect = current
        if self.pending and self.rect is not None and rect != self.rect:
            return result("stopped", reason="geometry")
        if self.checkpoint is not None and not self.pending:
            if (rect != self.rect or self.checkpoint.shape != frame.shape
                    or vision.frame_diff(frame, self.checkpoint) >= max(8.0, end_diff * 4)):
                # 背包被整理/手动滚动/换尺寸，旧覆盖证据不再成立；总预算不重置。
                self.phase, self.reached_top, self.stable = "top", False, 0
                self.previous = None
        self.checkpoint = None
        self.rect = rect

        while not should_stop():
            if expired():
                return result("stopped", reason="scan_limit")
            if self.pending:
                # 等待滚动后的连续画面稳定。上次因动画让出时仍在这里续等。
                last = frame
                measured("wait", sleep, 0.05)
                current = observe()
                if current is None:
                    return result("stopped", reason="observation")
                frame, new_rect = current
                if new_rect != rect:
                    return result("stopped", reason="geometry")
                diff = vision.frame_diff(frame, last)
                unchanged = (self.previous is not None
                             and vision.frame_diff(frame, self.previous) < end_diff)
                if diff >= end_diff or (unchanged and time.monotonic() - self.pending_since < unchanged_wait):
                    if time.monotonic() - call_start >= budget_sec:
                        return result("paused", reason="settling")
                    continue
                self.pending = False

            if self.phase == "down":
                decision, payload = measured("probe", probe, frame, rect)
                if should_stop():
                    return result("stopped", reason="action")
                if decision == ACCEPT:
                    return result(ACCEPT, payload)
                if decision != SCROLL:
                    return result("stopped", reason="probe")

            if self.previous is not None:
                self.stable = (self.stable + 1 if vision.frame_diff(frame, self.previous)
                               < end_diff else 0)
                if self.stable >= 2:
                    if self.phase == "down":
                        return result("exhausted")
                    self.phase, self.reached_top = "down", True
                    self.stable, self.previous = 0, None
                    continue  # 顶部画面必须经过 probe，不能直接下滚。

            if actions >= max(1, max_scrolls) or time.monotonic() - call_start >= budget_sec:
                self.checkpoint, self.rect = frame.copy(), rect
                # 边界计数已包含当前帧，下次不能重复累加同一次滚动。
                self.previous = None
                return result("paused", reason="budget")
            direction = -int(scroll_step) if self.phase == "top" else int(scroll_step)
            if measured("scroll", mouse.scroll, direction, *_center(rect), region=rect) is False:
                return result("stopped", reason="input")
            if should_stop():
                return result("stopped", reason="stopped")
            self.previous = frame
            self.scrolls += 1
            actions += 1
            self.pending, self.pending_since = True, time.monotonic()
            measured("wait", sleep, max(0.05, min_settle_sec))
            current = observe()
            if current is None:
                return result("stopped", reason="observation")
            frame, new_rect = current
            if rect != new_rect:
                return result("stopped", reason="geometry")
        return result("stopped", reason="stopped")


def _center(rect):
    return rect[0] + rect[2] // 2, rect[1] + rect[3] // 2


def _valid_scan_scene(scene):
    """严格覆盖证据必须来自有效画面；空帧、黑屏、纯色占位不能证明滚到边界。"""
    return (scene is not None and getattr(scene, "ndim", 0) >= 2
            and scene.size > 0 and scene.shape[0] > 0 and scene.shape[1] > 0
            and float(scene.var(axis=(0, 1)).max()) > 0.25)


def _scroll_to_top(grab_rect, mouse, should_stop, sleep, scroll_step,
                   settle_sec, end_diff, reset_max, log,
                   stable_scrolls=1, require_valid_scene=False):
    """向上滚动并比较前后画面，连续 stable_scrolls 次不变才确认顶部。
    返回 True=确认顶部，None=仅耗尽上限，False=被停止或截图/滚动失败。"""
    up = -int(scroll_step)          # scroll_step 负=向下；取反得「向上」格数
    prev = None
    ups = 0
    stable = 0
    while not should_stop():
        rect = grab_rect()
        if rect is None:
            return False
        cur = win_mod.grab(rect)
        if cur is None or should_stop():
            return False
        if require_valid_scene and not _valid_scan_scene(cur):
            return False
        if prev is not None:
            stable = stable + 1 if vision.frame_diff(cur, prev) < end_diff else 0
            if stable >= stable_scrolls:
                return True         # 足够次数有效上滚后，画面仍保持不变
        if ups >= reset_max:
            if log:
                log(f"滚到顶达上限 {reset_max} 屏，未确认顶部。")
            return None
        cx, cy = _center(rect)
        if mouse.scroll(up, cx, cy) is False:
            return False
        prev = cur
        ups += 1
        sleep(settle_sec)
    return False

def scroll_search(*, grab_rect, probe, mouse, should_stop, sleep,
                  scroll_step=-3, max_tries=8, settle_sec=0.35,
                  reset_to_top=True, end_diff=2.0, reset_max=20,
                  log=None, label="列表", require_full_scan=False):
    """滚动查找主循环。先（可选）滚到顶，再从顶向下逐屏 probe，命中即停；
    每向下滚一屏用帧差判是否到底（到底=已翻完整段）。返回 ScanResult。

    require_full_scan=True 会强制回顶，并要求上下边界均连续两次有效滚动后不变；
    只有确认顶部、到底且 exhausted 的结果，才表示完成一轮全段无目标扫描。

    用户拍板：滚轮查找在【同一个号】上一气呵成跑完（找到/翻完才返回），不在滚动中途让出去轮转别号——
    故这是个自带内循环的阻塞函数，只在 ACCEPT/翻完/被停止时返回；全程勤查 should_stop。"""
    # 本模块约定负数向下；零步长或反向配置不能形成从顶到底的覆盖证据。
    if require_full_scan and int(scroll_step) >= 0:
        if log:
            log(f"{label}严格扫描要求 scroll_step < 0，当前为 {scroll_step}，停止本轮查找。")
        return ScanResult("stopped")
    # 严格扫描不能由调用方关闭回顶；否则当前位置以上没有经过检查。
    reached_top = False
    if reset_to_top or require_full_scan:
        top = _scroll_to_top(grab_rect, mouse, should_stop, sleep, scroll_step,
                             settle_sec, end_diff, reset_max, log,
                             stable_scrolls=2 if require_full_scan else 1,
                             require_valid_scene=require_full_scan)
        if top is False or (require_full_scan and top is not True):
            return ScanResult("stopped")
        reached_top = top is True

    tries = 0
    pre_scroll_scene = None         # 上一次「向下滚之前」看到的那片画面，用来判滚动后有没有动
    stable = 0
    while not should_stop():
        rect = grab_rect()
        if rect is None:
            return ScanResult("stopped", scrolls=tries, reached_top=reached_top)
        scene = win_mod.grab(rect)
        if scene is None or should_stop():
            return ScanResult("stopped", scrolls=tries, reached_top=reached_top)
        if require_full_scan and not _valid_scan_scene(scene):
            return ScanResult("stopped", scrolls=tries, reached_top=reached_top)

        decision, payload = probe(scene, rect)
        if decision == ACCEPT:
            return ScanResult(ACCEPT, payload=payload, scrolls=tries,
                              reached_top=reached_top)
        if should_stop():
            return ScanResult("stopped", scrolls=tries, reached_top=reached_top)

        if decision == SCROLL:
            # 必须先 probe 当前帧：小目标出现时总帧差也可能很低。
            if pre_scroll_scene is not None:
                stable = (stable + 1 if vision.frame_diff(scene, pre_scroll_scene)
                          < end_diff else 0)
                if stable >= (2 if require_full_scan else 1):
                    if log:
                        log(f"{label}已滚到底（翻完整段未再发现目标）。")
                    return ScanResult("exhausted", scrolls=tries,
                                      reached_end=True, reached_top=reached_top)
            cx, cy = _center(rect)
            if mouse.scroll(scroll_step, cx, cy) is False:
                return ScanResult("stopped", scrolls=tries, reached_top=reached_top)
            # SCROLL：probe 只看没动手 → 滚前画面干净，记下它，下一轮和滚后对比判到底。
            pre_scroll_scene = scene
        else:                               # HANDLED / STAY：都不滚，原地重 probe，清掉「刚滚过」标记
            # HANDLED：probe 卖/丢/用了一件物品（改了画面）→ 不滚，原地再 probe 找同屏剩下的可处理物品，
            #   只有同屏确实没有可处理物品了（probe 返回 SCROLL）才往下滚一屏（「卖完一件别急着滚」）。
            #   注：被动作污染的画面不会被拿来判到底——因为没记进 pre_scroll_scene（保持 None）。
            # STAY：主目标在、次级目标缺，原地重试。两者都计入 max_tries 作安全网（见上方 tries += 1）。
            pre_scroll_scene = None
            stable = 0

        tries += 1
        if tries > max_tries:
            return ScanResult("exhausted", scrolls=tries, reached_end=False,
                              reached_top=reached_top)
        sleep(settle_sec)

    return ScanResult("stopped", scrolls=tries, reached_top=reached_top)
