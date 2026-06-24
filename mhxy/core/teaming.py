# -*- coding: utf-8 -*-
"""
组队（可复用的跨窗口握手编排）。

组队不是「每个号各跑同一条独立状态机」，而是几个号有时序依赖的握手：
  队长：开队伍 → 创建队伍 → 点「申请」标签页(切到入队申请列表) → 在申请页见「同意」就点、点够人数 → 右键关队伍弹窗
  队员：开好友 → 滚轮找队长ID → 点其右侧箭头 → 申请入队 → 右键关好友弹窗

注意（用户拍板的真实流程）：建好队伍后队员默认就能申请，「申请」不是「开放入队」按钮，
而是队长队伍面板里的一个**标签页**——必须点它切过去才能看到队员的入队申请、才能逐个同意。
关键时序：队长要「创建队伍 → 立刻切「申请」标签页」一气呵成，期间队员被门控不动，
**点完「申请」（或等不到「申请」超时兜底）才置 team_ready=True** 放队员去申请。
这样队长切前台做这两步时不会被队员抢去前台/鼠标打断（踩过的坑：建队即置 team_ready，
队员马上抢前台、队长迟迟点不到「申请」；更早的坑则是只在点到「申请」后才置位、
一旦找不到「申请」队员就永远不动——故现在超时也兜底置位）。

但它仍融入项目「非阻塞轮转 + activate 切前台」框架：每个号各持一份 record、按角色走不同状态分支，
编排器（本类）持有共享标志 team_ready / joined 作为号间信号量来协调时序——
队长把「创建+申请」做完才置 team_ready=True，队员在此之前被门控空转（不抢鼠标、不开界面）；
队员申请后队长在 C_ACCEPT 见「同意」就点、joined++，直到点够人数（队员数）。

本类与具体任务、与 GUI 完全解耦，只依赖 ctx（含 window/mouse/cfg/log/stop）。
未来「师门组队/帮派组队」等任务都可 self.team = TeamFormation(...) 复用。
core 不依赖 tasks 层，故 _jitter/_sleep 在类内自带一份等价实现。
"""

import time
import random

from . import vision
from . import window as win_mod


# 组队标定 spec（单一真相源；calibrate_dialog 与 DungeonTask 都引用，避免漂移）
TEAM_CALIBRATION = {
    "regions": [
        ("team_panel", "队伍面板区", "队伍界面那片区域，创建/申请/接受 按钮都在这里找"),
        ("friend_list", "好友列表区", "好友界面里那片列表，滚轮在此翻找队长ID"),
    ],
    "templates": [
        ("team_create", "「创建队伍」按钮", "队伍面板里的「创建队伍」按钮，框按钮本身、要独特"),
        ("team_apply", "「申请」标签页", "队伍面板里的「申请」标签页——点它切过去才能看到队员的入队申请列表，框这个页签"),
        ("team_accept", "「同意」按钮", "切到申请标签页后，队员申请那行右侧的「同意」按钮，见即点、不认申请人"),
        ("team_apply_join", "「申请入队」按钮", "队员点队长右侧箭头后弹出的「申请入队」按钮"),
        ("team_arrow", "队长ID右侧箭头(可选)", "好友列表里ID右侧的箭头按钮；在命中右侧小范围内找它。"
                                          "不标则用固定偏移兜底点击"),
        ("leader_id", "队长ID", "队员据此在好友列表定位队长，框队长名字、要独特"),
    ],
    "watchlist": False,
}

# 全部模板键
_TPL_KEYS = ["team_create", "team_apply", "team_accept", "team_apply_join", "team_arrow", "leader_id"]
# preflight 用：必备区域 / 必备模板（team_arrow 可选，缺失走固定偏移兜底）
TEAM_REQUIRED_REGIONS = ["team_panel", "friend_list"]
TEAM_REQUIRED_TEMPLATES = ["team_create", "team_apply", "team_accept", "team_apply_join", "leader_id"]

# 队长状态链
C_OPEN_TEAM = "C_OPEN_TEAM"   # 发 open_team 开队伍面板
C_CREATE = "C_CREATE"         # 点「创建队伍」→ 立刻去切申请页（队员仍门控、不抢前台）
C_APPLY = "C_APPLY"           # 点「申请」标签页 → 置 team_ready 放队员去申请（超时也兜底置位）
C_ACCEPT = "C_ACCEPT"         # 在申请页见「同意」就点，点够人数
C_CLOSE = "C_CLOSE"           # 右键关队伍弹窗
# 队员状态链
M_WAIT_READY = "M_WAIT_READY"     # 门控：队长没就绪前不动
M_OPEN_FRIEND = "M_OPEN_FRIEND"   # 发 open_friend 开好友面板
M_FIND_LEADER = "M_FIND_LEADER"   # 滚轮找队长ID → 点右侧箭头
M_APPLY_JOIN = "M_APPLY_JOIN"     # 点「申请入队」
M_CLOSE = "M_CLOSE"               # 右键关好友弹窗


class TeamFormation:
    """跨窗口组队编排器。给 leader_ctx（用于 should_stop/cfg/全局日志）、
    assignments=[(wctx, role), ...]（队长建议放第 0 位）、组队全局配置块、dry_run。
    调 run_until_formed() 跑到组队成功/超时/停止，返回 (ok, reason)。"""

    ROLE_CAPTAIN = "captain"
    ROLE_MEMBER = "member"

    def __init__(self, leader_ctx, assignments, team_cfg, dry_run=False):
        self.lead = leader_ctx
        self.cfg = leader_ctx.cfg
        self.team_cfg = team_cfg or {}
        self.dry_run = dry_run
        self.loop = self.team_cfg.get("loop", {})
        self.regions = self.team_cfg.get("regions", {})
        self.threshold = self.loop.get("match_threshold", 0.85)
        self.tpl = self._load_templates(self.team_cfg)
        self.member_count = sum(1 for _, r in assignments if r == self.ROLE_MEMBER)
        # 共享握手标志（编排器持有，号与号之间据此同步时序）
        self.team_ready = False     # 队长已建队并「申请」开放，队员才能开始申请
        self.joined = 0             # 队长已点过的「接受」次数（≈已入队人数）
        self.records = [self._new_record(wctx, role) for wctx, role in assignments]

    # ------------------------------------------------------------------
    # 初始化辅助
    # ------------------------------------------------------------------
    @staticmethod
    def _load_templates(team_cfg):
        templates = team_cfg.get("templates", {})
        return {k: (vision.load_template(templates.get(k)) if templates.get(k) else None)
                for k in _TPL_KEYS}

    @staticmethod
    def _new_record(wctx, role):
        return {"ctx": wctx, "role": role,
                "state": C_OPEN_TEAM if role == TeamFormation.ROLE_CAPTAIN else M_WAIT_READY,
                "t_state": 0.0, "scrolls": 0, "recover": 0,
                "done": False, "ok": False, "dead_logged": False, "fg_warned": False}

    # ------------------------------------------------------------------
    # 拟人化等待/抖动（core 不依赖 tasks，故自带一份等价实现）
    # ------------------------------------------------------------------
    def _jitter(self, base):
        r = self.cfg.get("humanize", {}).get("interval_jitter", 0.4)
        return max(0.05, base * (1 + random.uniform(-r, r)))

    def _sleep(self, seconds):
        """可被停止打断的等待。"""
        end = time.time() + seconds
        while time.time() < end:
            if self.lead.should_stop():
                return
            time.sleep(min(0.05, max(0.0, end - time.time())))

    @staticmethod
    def _goto(rec, state):
        rec["state"] = state
        rec["t_state"] = time.time()

    @staticmethod
    def _elapsed(rec):
        return time.time() - rec["t_state"]

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------
    def run_until_formed(self):
        """逐号 activate + 各推进一小步，直到组队成功/超时/停止。返回 (ok: bool, reason: str)。"""
        multi = self.cfg.get("targets", {}).get("multi", False)
        switch_delay = self.cfg.get("targets", {}).get("switch_delay_sec", 0.15)
        tick = self.loop.get("tick_interval_sec", 0.5)
        overall = self.loop.get("form_timeout_sec", 180) or 0
        start = time.time()

        if self.dry_run:
            return self._dry_run_selfcheck(multi, switch_delay)

        while not self.lead.should_stop():
            if overall and time.time() - start > overall:
                self.lead.log(f"组队总超时 {overall}s 未成形 → 降级结束。", level="warn")
                break
            if all(r["done"] for r in self.records):
                break

            active = [r for r in self.records if not r["done"]]
            for rec in self.records:
                if self.lead.should_stop():
                    break
                if rec["done"]:
                    continue
                wctx = rec["ctx"]
                if wctx.window.rect() is None:
                    if rec["role"] == self.ROLE_CAPTAIN:
                        self.lead.log("队长窗口不见了，组队无法继续。", level="error")
                        return False, "captain_window_gone"
                    if not rec["dead_logged"]:
                        wctx.log("队员窗口不见了，跳过该号（其余继续）。", level="warn")
                        rec["dead_logged"] = True
                    rec["done"] = True
                    continue
                rec["dead_logged"] = False
                # 操作某号前先切前台（多开必须）；失败本轮跳过、下轮重试，绝不在后台号瞎点。
                if multi:
                    if not wctx.window.activate():
                        if not rec["fg_warned"]:
                            wctx.log("未能切到前台（系统拒绝焦点抢占），本轮跳过、下轮重试。", level="warn")
                            rec["fg_warned"] = True
                        continue
                    rec["fg_warned"] = False
                    if wctx.should_stop():
                        break
                self._step_once(rec)
                if multi and len(active) > 1:
                    self._sleep(self._jitter(switch_delay))
            self._sleep(self._jitter(tick))

        if self.lead.should_stop():
            return False, "stopped"
        if self.joined >= self.member_count:
            self.lead.log(f"★ 组队成功：{self.joined}/{self.member_count} 名队员已入队。", level="hit")
            return True, "formed"
        self.lead.log(f"组队未达人数：{self.joined}/{self.member_count} 入队。", level="warn")
        return False, "not_enough"

    def _step_once(self, rec):
        """按该号当前 state 推进【一小步】后立刻返回（好轮转到下一个号）。"""
        handlers = {
            C_OPEN_TEAM: self._cap_open, C_CREATE: self._cap_create,
            C_APPLY: self._cap_apply, C_ACCEPT: self._cap_accept, C_CLOSE: self._cap_close,
            M_WAIT_READY: self._mem_wait, M_OPEN_FRIEND: self._mem_open,
            M_FIND_LEADER: self._mem_find, M_APPLY_JOIN: self._mem_apply, M_CLOSE: self._mem_close,
        }
        h = handlers.get(rec["state"])
        if h is not None:
            h(rec)

    # ------------------------------------------------------------------
    # 队长分支
    # ------------------------------------------------------------------
    def _cap_open(self, rec):
        ctx = rec["ctx"]
        if not ctx.send_hotkey("open_team"):
            ctx.log("队长：打不开队伍界面（open_team 未配置），组队放弃。", level="error")
            rec["done"] = True
            return
        ctx.log("队长：已开队伍界面，准备创建队伍…")
        self._sleep(self._jitter(0.6))
        self._goto(rec, C_CREATE)

    def _cap_create(self, rec):
        ctx = rec["ctx"]
        hit = self._find_in_region(ctx, "team_panel", "team_create")
        if hit is not None:
            ctx.mouse.click(hit[0], hit[1])
            ctx.log(f"队长：点「创建队伍」（{hit[2]:.2f}），立刻切「申请」标签页。", level="hit")
            # 注意：此处【不】置 team_ready——队员继续门控，让队长趁前台一气呵成把「申请」也点了，
            # 不被队员抢前台/鼠标打断。team_ready 改到 _cap_apply 点完「申请」（或超时兜底）才置。
            self._sleep(self._jitter(0.6))
            self._goto(rec, C_APPLY)
            return
        if self._elapsed(rec) > self.loop.get("create_timeout_sec", 15):
            ctx.log("队长：没找到「创建队伍」（可能已在队中），直接去切「申请」标签页。", level="warn")
            self._goto(rec, C_APPLY)

    def _cap_apply(self, rec):
        ctx = rec["ctx"]
        # 「申请」是队伍面板里的标签页，点它切过去才能看到队员的入队申请列表。
        # 点完（或等不到超时兜底）才置 team_ready 放队员去申请——在此之前队员被门控、不抢前台，
        # 保证队长「创建→申请」两步一气呵成，不被队员切前台/抢鼠标打断。
        hit = self._find_in_region(ctx, "team_panel", "team_apply")
        if hit is not None:
            ctx.mouse.click(hit[0], hit[1])
            ctx.log(f"队长：点「申请」标签页（{hit[2]:.2f}），队伍已开放，放队员去申请。", level="hit")
            self.team_ready = True
            self._sleep(self._jitter(0.5))
            self._goto(rec, C_ACCEPT)
            return
        # 诊断：连找几秒还没「申请」，抓一张 team_panel 区域图 + 报最高匹配分（只抓一次），
        # 帮判断到底是「申请页根本没出现/被弹窗挡住(分很低)」还是「在画面里但阈值卡太高(分接近阈值)」。
        if not rec.get("diag_apply") and self._elapsed(rec) > 2.5:
            rec["diag_apply"] = True
            self._diag_dump(ctx, "team_panel", "team_apply", "cap_apply")
        if self._elapsed(rec) > self.loop.get("create_timeout_sec", 15):
            ctx.log("队长：没找到「申请」标签页，仍按已开放处理，放队员申请并开始找同意。", level="warn")
            self.team_ready = True
            self._goto(rec, C_ACCEPT)

    def _cap_accept(self, rec):
        ctx = rec["ctx"]
        if self.joined >= self.member_count:
            self._goto(rec, C_CLOSE)
            return
        # 「同意」按钮在申请标签页内（队员申请那一行右侧）→ 在 team_panel 区域内找
        hit = self._find_in_region(ctx, "team_panel", "team_accept")
        if hit is not None:
            ctx.mouse.click(hit[0], hit[1])
            self.joined += 1
            ctx.log(f"队长：点「同意」（{hit[2]:.2f}），已接受 {self.joined}/{self.member_count}。", level="hit")
            self._sleep(self._jitter(0.4))
            rec["t_state"] = time.time()      # 接受到人就刷新计时，防止误超时
            if self.joined >= self.member_count:
                self._goto(rec, C_CLOSE)
            return
        if self._elapsed(rec) > self.loop.get("accept_timeout_sec", 90):
            ctx.log(f"队长：等申请超时，仅 {self.joined}/{self.member_count} 入队，收尾关窗。", level="warn")
            self._goto(rec, C_CLOSE)

    def _cap_close(self, rec):
        ctx = rec["ctx"]
        self._close_panel(ctx, "team_panel")
        ctx.log("队长：右键关闭队伍弹窗，完成。", level="hit")
        rec["ok"] = self.joined >= self.member_count
        rec["done"] = True

    # ------------------------------------------------------------------
    # 队员分支
    # ------------------------------------------------------------------
    def _mem_wait(self, rec):
        # 门控：队长没把队建好开放前，队员不动（不抢鼠标、不开界面）
        if self.team_ready:
            self._goto(rec, M_OPEN_FRIEND)

    def _mem_open(self, rec):
        ctx = rec["ctx"]
        if not ctx.send_hotkey("open_friend"):
            ctx.log("队员：打不开好友界面（open_friend 未配置），该号放弃。", level="error")
            rec["done"] = True
            return
        ctx.log("队员：已开好友界面，滚轮翻找队长ID…")
        self._sleep(self._jitter(0.6))
        rec["scrolls"] = 0
        self._goto(rec, M_FIND_LEADER)

    def _mem_find(self, rec):
        """滚轮找队长ID（内部 while 一气呵成跑完，不在搜索中途轮转别的号）。
        命中后点其右侧箭头按钮 → 进申请入队。翻完/超时仍找不到则放弃该号（不拖累其他队员）。"""
        ctx = rec["ctx"]
        leader_tpl = self.tpl.get("leader_id")
        if leader_tpl is None:
            ctx.log("队员：leader_id 模板未标定，放弃该号。", level="error")
            rec["done"] = True
            return
        max_tries = max(1, self.loop.get("scroll_max_tries", 10))
        settle = self.loop.get("scroll_settle_sec", 0.35)
        deadline = rec["t_state"] + self.loop.get("find_leader_timeout_sec", 60)
        while not self.lead.should_stop():
            rect = self._region_rect(ctx, "friend_list")
            if rect is None:
                rec["done"] = True
                return
            scene = win_mod.grab(rect)
            hit = vision.match(scene, leader_tpl, self.threshold) if scene is not None else None
            if hit is not None:
                lx, ly, score = rect[0] + hit[0], rect[1] + hit[1], hit[2]
                arrow = self._find_arrow_on_row(ctx, "friend_list", (lx, ly))
                if arrow is None:        # 仅当区域取不到（窗口没了）才会是 None
                    rec["done"] = True
                    return
                ctx.mouse.click(arrow[0], arrow[1])
                if arrow[2] > 0:
                    ctx.log(f"队员：找到队长（{score:.2f}）→ 点右侧箭头（{arrow[2]:.2f}），准备申请入队。", level="hit")
                else:
                    ctx.log(f"队员：找到队长（{score:.2f}）→ 行内没匹配到箭头模板，按右侧固定偏移兜底点击。",
                            level="warn")
                self._sleep(self._jitter(0.5))
                self._goto(rec, M_APPLY_JOIN)
                return
            else:
                cx_c, cy_c = rect[0] + rect[2] // 2, rect[1] + rect[3] // 2
                ctx.mouse.scroll(self.loop.get("scroll_step", -3), cx_c, cy_c)
            rec["scrolls"] += 1
            if rec["scrolls"] > max_tries or time.time() > deadline:
                ctx.log("队员：翻找队长ID多次未果，放弃该号。", level="warn")
                rec["done"] = True
                return
            self._sleep(self._jitter(settle))

    def _mem_apply(self, rec):
        ctx = rec["ctx"]
        # 「申请入队」多是点箭头后弹出的小菜单，未必落在 friend_list 区内 → 整窗找更稳
        hit = self._find_full(ctx, "team_apply_join")
        if hit is not None:
            ctx.mouse.click(hit[0], hit[1])
            ctx.log(f"队员：点「申请入队」（{hit[2]:.2f}）。", level="hit")
            self._sleep(self._jitter(0.5))
            self._goto(rec, M_CLOSE)
            return
        if self._elapsed(rec) > self.loop.get("apply_timeout_sec", 15):
            ctx.log("队员：没找到「申请入队」按钮，超时收尾。", level="warn")
            self._goto(rec, M_CLOSE)

    def _mem_close(self, rec):
        ctx = rec["ctx"]
        self._close_panel(ctx, "friend_list")
        ctx.log("队员：右键关闭好友弹窗，完成。", level="hit")
        rec["ok"] = True
        rec["done"] = True

    # ------------------------------------------------------------------
    # 识别/点击工具
    # ------------------------------------------------------------------
    def _region_rect(self, ctx, key):
        region = self.regions.get(key)
        return ctx.window.region_to_screen_rect(region) if region else ctx.window.rect()

    def _find_in_region(self, ctx, region_key, tpl_key):
        """在某标定区域内匹配模板，命中返回屏幕绝对 (x,y,score)，否则 None。"""
        tpl = self.tpl.get(tpl_key)
        rect = self._region_rect(ctx, region_key)
        if tpl is None or rect is None:
            return None
        scene = win_mod.grab(rect)
        if scene is None:
            return None
        m = vision.match(scene, tpl, self.threshold)
        if m is None:
            return None
        return (rect[0] + m[0], rect[1] + m[1], m[2])

    def _find_full(self, ctx, tpl_key):
        """在整窗范围内匹配模板（弹出菜单位置不定时用）。"""
        tpl = self.tpl.get(tpl_key)
        rect = ctx.window.rect()
        if tpl is None or rect is None:
            return None
        scene = win_mod.grab(rect)
        if scene is None:
            return None
        m = vision.match(scene, tpl, self.threshold)
        if m is None:
            return None
        return (rect[0] + m[0], rect[1] + m[1], m[2])

    def _find_arrow_on_row(self, ctx, region_key, leader_screen_xy):
        """在队长ID命中【行】的右侧找箭头按钮(team_arrow)。返回屏幕 (x, y, score)。
        参照 secret_realm._find_join_on_row 的「按行」思路抗布局漂移、不串到别人那行：
        - 横向：从队长名字的【右边缘】一直扫到该区域【右缘】（整行右半部分都找）。
          注意 match() 返回的是模板【中心】，名字模板有几十像素宽，老逻辑从中心起算只取 80px
          常够不到更靠右的箭头 → 「认出队长却找不到箭头」。改成扫到行尾，彻底消除这个够不着。
          （arrow_band_w>0 时仍按它当上限裁，便于个别布局收窄；默认 0=扫到区域右缘。）
        - 纵向：只取约 1.6 行高，不会串到上下相邻好友那一行的箭头。
        模板未标定、或行内没匹配到，统一退化为「名字右边缘 + arrow_offset_x」固定偏移点击
        （返回 score=0 表示走了兜底，调用方据此区分日志）。"""
        rect = self._region_rect(ctx, region_key)
        if rect is None:
            return None
        rx, ry = rect[0], rect[1]
        lx, ly = leader_screen_xy
        leader_tpl = self.tpl.get("leader_id")
        name_w = leader_tpl.shape[1] if leader_tpl is not None else 64
        right_edge_x = lx + name_w // 2          # 队长名字的右边缘（屏幕坐标）
        fallback = (right_edge_x + int(self.loop.get("arrow_offset_x", 28)), ly, 0.0)
        arrow_tpl = self.tpl.get("team_arrow")
        if arrow_tpl is None:
            return fallback
        scene = win_mod.grab(rect)
        if scene is None:
            return fallback
        sh, sw = scene.shape[:2]
        row_h = leader_tpl.shape[0] if leader_tpl is not None else 32
        band = max(32, int(row_h * 1.6))
        ly_local = int(ly - ry)
        y0 = max(0, ly_local - band // 2)
        y1 = min(sh, ly_local + band // 2)
        x0 = max(0, int(right_edge_x - rx) - 2)            # 从名字右边缘起（留 2px 容差）
        band_w = int(self.loop.get("arrow_band_w", 0))
        x1 = min(sw, x0 + band_w) if band_w > 0 else sw    # 0=扫到区域右缘（整行右半部分）
        if y1 - y0 < 1 or x1 - x0 < 1:
            return fallback
        crop = scene[y0:y1, x0:x1]
        m = vision.match(crop, arrow_tpl, self.threshold)
        if m is None:
            return fallback
        cx, cy, score = m
        return (rx + x0 + cx, ry + y0 + cy, score)

    def _diag_dump(self, ctx, region_key, tpl_key, tag):
        """诊断：抓该区域截图存到 captures/，并打印模板在区域内的最高匹配分（不卡阈值）。
        分很低(≈0.2~0.5)=模板根本不在这片画面里（区域标错/被弹窗挡/页面没切过去）；
        分接近阈值(≈0.7~0.84)=在画面里但阈值太严或模板有点漂移。看截图即可定位。"""
        rect = self._region_rect(ctx, region_key)
        if rect is None:
            return
        scene = win_mod.grab(rect)
        if scene is None:
            return
        tpl = self.tpl.get(tpl_key)
        score = vision.best_score(scene, tpl)[0] if tpl is not None else 0.0
        ts = time.strftime("%H%M%S")
        path = f"captures/diag_{tag}_{ts}.png"
        vision.save_image(path, scene)
        ctx.log(f"诊断：『{tpl_key}』在 {region_key} 区域最高匹配分={score:.2f}（阈值 {self.threshold:.2f}）；"
                f"该区域截图已存 {path}，可打开比对。", level="warn")

    def _close_panel(self, ctx, region_key):
        """右键关弹窗：右键点该区域中心；区域未标定则用 close_panel(Esc) 兜底。"""
        rect = self._region_rect(ctx, region_key)
        if rect is None:
            ctx.send_hotkey("close_panel")
            return
        cx, cy = rect[0] + rect[2] // 2, rect[1] + rect[3] // 2
        ctx.mouse.right_click(cx, cy)
        self._sleep(self._jitter(0.3))

    # ------------------------------------------------------------------
    # 演练：只识别各号角色相关标志，不发快捷键/不点
    # ------------------------------------------------------------------
    def _dry_run_selfcheck(self, multi, switch_delay):
        cap_keys = [("team_create", "创建队伍"), ("team_apply", "申请标签页"), ("team_accept", "同意")]
        mem_keys = [("leader_id", "队长ID"), ("team_arrow", "箭头"), ("team_apply_join", "申请入队")]
        self.lead.log("组队演练：对每个号识别其角色相关标志，验证模板/阈值，不发快捷键/不点。", level="warn")
        rounds = 0
        while not self.lead.should_stop() and rounds < 1000:
            rounds += 1
            for rec in self.records:
                if self.lead.should_stop():
                    break
                ctx = rec["ctx"]
                if ctx.window.rect() is None:
                    continue
                if multi:
                    ctx.window.activate()
                rect = ctx.window.rect()
                scene = win_mod.grab(rect) if rect else None
                keys = cap_keys if rec["role"] == self.ROLE_CAPTAIN else mem_keys
                found = []
                for tpl_key, label in keys:
                    tpl = self.tpl.get(tpl_key)
                    if tpl is None or scene is None:
                        continue
                    hit = vision.match(scene, tpl, self.threshold)
                    if hit is not None:
                        found.append(f"{label}({hit[2]:.2f})")
                if found:
                    ctx.log("识别到：" + "、".join(found), level="hit")
                else:
                    ctx.log("当前屏幕未识别到该角色标志（请打开对应界面再看）。")
                if multi and len(self.records) > 1:
                    self._sleep(self._jitter(switch_delay))
            self._sleep(self._jitter(1.5))
        return True, "dry_run"
