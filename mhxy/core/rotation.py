# -*- coding: utf-8 -*-
"""
多开窗口轮转推进器（core 层可复用控制流）。

项目所有多开任务（组队/运镖/宝图/秘境/刷副本）都用同一套「非阻塞轮转」：每个号各持一份
record（含 state/ctx/done…），主循环 while 不停地 for 遍历所有号，逐号 activate 切前台后推进
状态机。这套 while/for 骨架以前每个任务各抄一份，本模块把它收敛成一份可复用实现。

核心优化——「连续推进到等待点才让出」：
  以前是「每号每轮只推进一小步就切下一个号」，于是一个号本来自主、不依赖其他号的连续操作
  （如组队队员「开好友→翻找队长→申请入队→关窗」）被拆成多轮、每步都白白 activate 一次切前台。
  现在改成：切一次前台后，连续推进本号，直到撞到「等待点」或本号 done 才让出、切下一号。

  让出判据几乎零额外标注——它天然写在状态机里：
    - 本号能自主往下做 → 该步会改 rec["state"]（_goto 到下一态）→ state 变了 → 继续在本号推进。
    - 在等别人/等游戏响应 → 该步不改 state，原地等（门控未就绪 / 没找到目标 / 监控态盯帧差）
      → state 没变 → 让出切下一号。
  即：一次 step 后比较 state 前后是否变化；变了继续推，没变（且没 done）就让出。
  这条判据对监控态（运镖/宝图盯屏）也天然正确：它们没触发转移时 state 不变，立即让出，
  绝不在一个号上空转盯屏饿死别人。

本模块不懂任何任务语义——所有语义经回调注入（见 RotationConfig）。core 不依赖 tasks 层，
故 _jitter/_sleep 在此自带一份等价实现。
"""

import time
import random


# 让出原因（仅用于上限触发时打日志；调用方一般无需关心）
YIELD_WAIT = "wait"        # state 未变 = 等待点 → 让出
YIELD_DONE = "done"        # 本号 done → 让出
YIELD_BARRIER = "barrier"  # 进入声明的屏障状态 → 让出
YIELD_CAP = "cap"          # 撞连续推进安全上限 → 强制让出


class RotationConfig:
    """轮转控制流参数包。语义全部由回调注入，推进器本身不懂任务。

    必填：
      records:       list      每个号一份 record（任意结构，经下面回调访问）
      step_once:     (rec)->*  推进本号一小步（沿用各任务现有 _step_once，不改其返回值）
      should_stop:   ()->bool  全局停止信号
      log:           (msg, level=...)->None  全局日志出口

    可选回调（带默认）：
      is_done:   (rec)->bool      本号是否结束（默认 rec["done"]）
      get_state: (rec)->hashable  取当前状态键，用于「前后是否变化」判据（默认 rec["state"]）
      get_ctx:   (rec)->wctx      取窗口上下文（默认 rec["ctx"]）

    窗口消失/前台失败钩子（语义留调用方；默认即「跳过本号」）：
      on_window_gone: (rec)->"abort"|"skip"|"done"
            窗口 rect 为 None 时调用。返回 "abort" 整体中止（run_rotation 返回 "aborted"）；
            "done"/"skip" 跳过该号本轮（done 语义由回调内自行置位）。默认返回 "skip"。
      on_activate_fail: (rec)->None   activate 失败时调用（节流警告由回调内自管）。

    数值/开关：
      multi:           bool   多开才 activate 切前台、才有号间 switch_delay
      switch_delay:    float  号间拟人化间隔基准（秒）
      tick:            float  轮间间隔基准（秒）
      overall_timeout: float  总超时（秒），0 = 不限
      jitter_ratio:    float  间隔抖动比例（默认从无→0.4；一般传 cfg.humanize.interval_jitter）
      barrier_states:  set    可选「进入即让出」屏障集（默认空，允许下一态空跑一次）
      max_consec_steps:int    单号一次最多连续推进步数（防异常号霸占前台）
      max_consec_sec:  float  单号一次最多连续推进秒数
    """

    def __init__(self, records, step_once, should_stop, log,
                 is_done=None, get_state=None, get_ctx=None,
                 on_window_gone=None, on_activate_fail=None,
                 multi=False, switch_delay=0.15, tick=0.5, overall_timeout=0,
                 jitter_ratio=0.4, barrier_states=None,
                 max_consec_steps=12, max_consec_sec=4.0):
        self.records = records
        self.step_once = step_once
        self.should_stop = should_stop
        self.log = log
        self.is_done = is_done or (lambda r: r["done"])
        self.get_state = get_state or (lambda r: r["state"])
        self.get_ctx = get_ctx or (lambda r: r["ctx"])
        self.on_window_gone = on_window_gone or (lambda r: "skip")
        self.on_activate_fail = on_activate_fail or (lambda r: None)
        self.multi = multi
        self.switch_delay = switch_delay
        self.tick = tick
        self.overall_timeout = overall_timeout or 0
        self.jitter_ratio = jitter_ratio
        self.barrier_states = barrier_states or set()
        self.max_consec_steps = max(1, max_consec_steps)
        self.max_consec_sec = max(0.1, max_consec_sec)


def _jitter(c, base):
    r = c.jitter_ratio
    return max(0.05, base * (1 + random.uniform(-r, r)))


def _sleep(c, seconds):
    """可被停止打断的等待。"""
    end = time.time() + seconds
    while time.time() < end:
        if c.should_stop():
            return
        time.sleep(min(0.05, max(0.0, end - time.time())))


def _drive_until_yield(c, rec):
    """切前台后连续推进本号，直到 state 不变 / done / 撞屏障 / 撞上限才返回（让出）。"""
    steps, t0 = 0, time.time()
    while not c.should_stop():
        before = c.get_state(rec)
        c.step_once(rec)                       # 各任务现有 step，内部自带拟人 sleep
        steps += 1
        if c.is_done(rec):
            return YIELD_DONE
        after = c.get_state(rec)
        if after == before:                    # state 没变 = 在等待 → 让出
            return YIELD_WAIT
        if after in c.barrier_states:           # 进入声明的屏障态 → 让出
            return YIELD_BARRIER
        if steps >= c.max_consec_steps or time.time() - t0 >= c.max_consec_sec:
            c.log(f"单号连续推进达上限（{steps} 步 / {time.time() - t0:.1f}s），强制让出。", level="warn")
            return YIELD_CAP
        # state 变了且未 done/未撞屏障/未超上限 → 本号还能自主往下，继续推
    return YIELD_WAIT


def run_rotation(c):
    """跑轮转直到全员 done / 总超时 / 停止。返回 "ok" | "aborted" | "stopped"。"""
    start = time.time()
    while not c.should_stop():
        if c.overall_timeout and time.time() - start > c.overall_timeout:
            c.log(f"轮转总超时 {c.overall_timeout}s 未完成 → 降级结束。", level="warn")
            break
        if all(c.is_done(r) for r in c.records):
            break

        active = [r for r in c.records if not c.is_done(r)]
        for rec in c.records:
            if c.should_stop():
                break
            if c.is_done(rec):
                continue
            wctx = c.get_ctx(rec)
            if wctx.window.rect() is None:
                action = c.on_window_gone(rec)
                if action == "abort":
                    return "aborted"
                continue  # "skip" / "done"：本轮跳过（done 由回调内置位）
            # 操作某号前先切前台（多开必须）；失败本轮跳过、下轮重试，绝不在后台号瞎点。
            if c.multi:
                if not wctx.window.activate():
                    c.on_activate_fail(rec)
                    continue
                if wctx.should_stop():
                    break
            _drive_until_yield(c, rec)
            if c.multi and len(active) > 1:
                _sleep(c, _jitter(c, c.switch_delay))
        _sleep(c, _jitter(c, c.tick))

    if c.should_stop():
        return "stopped"
    return "ok"
