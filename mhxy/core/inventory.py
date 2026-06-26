# -*- coding: utf-8 -*-
"""
整理背包（可复用的「翻包裹 + 逐物执行使用/丢弃/出售」编排）。

与组队一样是【跨任务的通用能力】：任何任务流程都可 InventoryOrganizer(ctx, ob_cfg, dry_run).organize()
穿插调用（如刷完副本顺手清背包）。本模块与 GUI、与具体玩法解耦，只依赖 ctx（window/mouse/cfg/log/stop）
+ core 的 vision/scan/window；core 绝不反向依赖 tasks。

原理（截屏+模板匹配+拟人化点击，不读内存）：
  ① 快捷键 open_bag 打开包裹；
  ② 走通用 scan.scroll_search 从顶向下逐屏翻包裹（先滚到顶、帧差判到底，见 core/scan.py）；
       【物品识别只在标定的包裹区 regions.bag_list 内做】，避免把弹出的详情/出售窗里的物品图标误当包裹物品；
  ③ 每屏对【用户标定的物品图】逐个模板匹配，取最靠上的一个命中物品，按它配置的动作执行一段声明式步骤序列
       （见 _ACTION_SPECS）：先「点开物品」(open，右键弹操作菜单 / 左键弹详情) → 逐步在整窗里找并点序列里的
       按钮模板（某步可标 optional，缺了就跳过，如详情里不一定有的「更多」）→ 收尾(close)关掉残留窗口回干净列表。例：
         · 使用/丢弃：点物品弹菜单 → 点「使用」/「丢弃」→（丢弃这类破坏性动作再点确认「确定」）；
         · 商会出售：左键点物品弹详情 →（有「更多」就点）→「商会出售」→ 出售窗点「满」→「出售」；
         · 摆摊出售：左键点物品弹详情 →（有「更多」就点）→「摆摊出售」→「本服上架」→ 右键关出售窗回包裹；
       加新出售/处理方式只需在 _ACTION_SPECS 加一项 + 注册对应按钮模板，core 其余与 GUI 都不用动。
  ④ 一遍 = 一次完整 top→bottom 扫描；可配 passes 多遍兜底（丢/卖后列表会变短，位置变化保证覆盖）。

每屏【逐个】处理命中物品：每次取最靠上的一个，动作 _cleanup 收回干净列表（菜单已关、物品已消失）后**原地不滚、
重新识别**接着处理同屏剩下的，直到同屏再没有可处理物品才往下滚一屏——用户拍板「卖完一件别急着滚，等同屏找不到
能操作的物品时才滚一段」（早期每卖一件就滚一屏太着急，已改）；多遍 passes 兜底，滚动后位置变化保证覆盖。
对「使用」这类不一定消失的物品，靠 passes 上限保证一定终止（不会死循环）。
dry_run：只识别并打日志（物品名+计划动作），不点任何动作、不丢不卖，作为安全自检。
"""

import time
import random

from . import vision
from . import window as win_mod
from . import scan

# 动作声明式步骤序列：每个动作 = 点开物品(open) → 逐步点序列按钮(steps) → 收尾(close)。
#   open : 点物品方式 left/right/double；None=用 loop.open_item_click（缺省 right）。
#   steps: 逐步在【整窗】里找并点的按钮模板；每步 {tpl 模板键, label 显示名, optional 可选(缺了跳过),
#          button 点法(默认 left), wait 点后等待秒(缺省 step_wait_sec)}。
#   confirm: True 时走旧式 confirm_button 确认框（丢弃/旧出售）；显式步骤序列的出售不用它。
#   close: panel=发 close_panel(ESC) 关菜单/确认框——用于「会一直开着等你 ESC」的右键菜单（使用/丢弃）；
#          right =在窗口中心右键关弹窗——用于「会一直开着等你右键」的弹窗（摆摊出售窗）；
#          auto  =动作末步会【自我关闭】其窗口（如商会出售点「出售」后窗口自动关回包裹）→ 默认**啥都不做**，
#                 只有探测到该动作的子面板还在（出售/满 等按钮仍能匹配到）才补发一记 ESC 关它。
#                 ⚠ 绝不无脑 ESC：窗口已自动关掉时，那记 ESC 会落到包裹本身把包裹关了——已踩坑：
#                   商会出售完一件后整理就提前收工（ESC 关了包裹，后续物品全没了，见 _cleanup）。
# 加新出售/处理方式只需在此加一项 + 在 config/CALIBRATION 注册对应按钮模板，core 其余与 GUI 全不动。
_ACTION_SPECS = {
    "use": {
        "label": "使用", "open": None, "confirm": False, "close": "panel",
        "steps": [{"tpl": "use_button", "label": "使用"}],
    },
    "discard": {
        "label": "丢弃", "open": None, "confirm": True, "close": "panel",
        "steps": [{"tpl": "discard_button", "label": "丢弃"}],
    },
    "sell": {   # 旧·笼统单按钮出售（兼容旧配置；新建议用 shop_sell/stall_sell）
        "label": "出售", "open": None, "confirm": True, "close": "panel",
        "steps": [{"tpl": "sell_button", "label": "出售"}],
    },
    "shop_sell": {
        "label": "商会出售", "open": "left", "confirm": False, "close": "auto",
        "steps": [
            {"tpl": "more_button", "label": "更多", "optional": True, "wait": 0.3},
            {"tpl": "shop_sell_button", "label": "商会出售"},
            {"tpl": "sell_full_button", "label": "满"},
            {"tpl": "sell_confirm_button", "label": "出售"},
        ],
    },
    "stall_sell": {
        "label": "摆摊出售", "open": "left", "confirm": False, "close": "right",
        "steps": [
            {"tpl": "more_button", "label": "更多", "optional": True, "wait": 0.3},
            {"tpl": "stall_sell_button", "label": "摆摊出售"},
            {"tpl": "stall_shelf_button", "label": "本服上架"},
        ],
    },
}

# 物品清单 GUI 下拉的动作顺序（不列旧 sell）；标签表含 sell 以便显示遗留配置。
ACTION_ORDER = ["use", "discard", "shop_sell", "stall_sell"]
ACTION_LABELS = {k: v["label"] for k, v in _ACTION_SPECS.items()}

# 所有动作步骤会用到的按钮模板键（含旧式确认框，及整理末尾点的游戏「整理」按钮）——__init__ 据此一次性加载。
_ALL_BTN_KEYS = {s["tpl"] for spec in _ACTION_SPECS.values() for s in spec["steps"]}
_ALL_BTN_KEYS.add("confirm_button")
_ALL_BTN_KEYS.add("sort_button")   # 游戏自带「整理」按钮：实战整理跑完点它把背包重新排列收拢（非任何动作的步骤）


def action_label(action):
    """动作内部值 → 中文显示名（未知动作原样返回）。"""
    spec = _ACTION_SPECS.get(action)
    return spec["label"] if spec else action


def required_templates(action):
    """该动作【必需】标定的按钮模板，返回 [(tpl_key, 中文名), ...]。
    可选步骤(optional，如「更多」)不计；confirm 动作追加 confirm_button。供 preflight 校验缺哪些标定。"""
    spec = _ACTION_SPECS.get(action)
    if not spec:
        return []
    out = [(s["tpl"], s["label"]) for s in spec["steps"] if not s.get("optional")]
    if spec.get("confirm"):
        out.append(("confirm_button", "确定"))
    return out


class InventoryOrganizer:
    """整理背包编排器。给 ctx（已切到前台的单窗口上下文）、tasks.organize_bag 配置块、dry_run。
    调 organize() 在该号背包上一气呵成跑完（与滚轮查找同原则）。返回 (handled, reason)。"""

    def __init__(self, ctx, ob_cfg, dry_run=False):
        self.ctx = ctx
        self.cfg = ctx.cfg
        self.ob = ob_cfg or {}
        self.dry_run = dry_run
        self.loop = self.ob.get("loop", {})
        self.regions = self.ob.get("regions", {})
        self.threshold = self.loop.get("match_threshold", 0.85)
        items = [it for it in self.ob.get("items", []) if it.get("template")]
        self._item_tpls = [(it, vision.load_template(it.get("template"))) for it in items]
        self._item_tpls = [(it, t) for it, t in self._item_tpls if t is not None]
        tpls = self.ob.get("templates", {})
        self._btn = {k: (vision.load_template(tpls.get(k)) if tpls.get(k) else None)
                     for k in _ALL_BTN_KEYS}

    def _jitter(self, base):
        r = self.cfg.get("humanize", {}).get("interval_jitter", 0.4)
        return max(0.05, base * (1 + random.uniform(-r, r)))

    def _sleep(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            if self.ctx.should_stop():
                return
            time.sleep(min(0.05, max(0.0, end - time.time())))

    def _region_rect(self):
        region = self.regions.get("bag_list")
        return self.ctx.window.region_to_screen_rect(region) if region else self.ctx.window.rect()

    def organize(self):
        ctx = self.ctx
        if not self._item_tpls:
            ctx.log("整理背包：没有可整理的物品（请先在「管理物品」框选添加），跳过。", level="warn")
            return 0, "no_items"
        if not ctx.send_hotkey("open_bag"):
            ctx.log("整理背包：打不开背包（open_bag 未配置），跳过。", level="error")
            return 0, "no_hotkey"
        self._sleep(self._jitter(0.6))

        passes = max(1, int(self.loop.get("passes", 1)))
        handled = 0
        for p in range(passes):
            if ctx.should_stop():
                break
            ctx.log(f"整理背包：第 {p + 1}/{passes} 遍扫描…")
            n = self._sweep_once()
            handled += n
            if n == 0:
                ctx.log("整理背包：本遍未发现可处理物品，提前结束。")
                break
        # 收尾点游戏自带「整理」按钮把背包重新排列收拢（用户要求）。趁包裹还开着点，再关包裹。
        # 演练不点（dry_run 只识别不动手）。
        if not self.dry_run:
            self._click_sort_button()
        ctx.send_hotkey("close_panel")
        if self.dry_run:
            ctx.log(f"整理背包·演练完成：共识别到 {handled} 次可处理物品（未执行任何动作）。", level="hit")
        else:
            ctx.log(f"整理背包完成：共处理 {handled} 件物品。", level="hit")
        return handled, "done"

    def _click_sort_button(self):
        """整理流程末尾点游戏自带的「整理」按钮，把背包重新排列收拢一下（用户要求）。
        没标定 sort_button 就静默跳过（向后兼容旧配置）；标了但找不到就告警、不强求（不影响整理结果）。"""
        if self._btn.get("sort_button") is None:
            return
        hit = self._find_full_wait("sort_button", self.loop.get("step_find_timeout_sec", 1.5))
        if hit is None:
            self.ctx.log("没找到游戏「整理」按钮（请检查 sort_button 标定），跳过收拢。", level="warn")
            return
        self.ctx.mouse.click(hit[0], hit[1])
        self.ctx.log(f"点游戏「整理」按钮收拢背包（{hit[2]:.2f}）。", level="hit")
        self._sleep(self._jitter(self.loop.get("step_wait_sec", 0.4)))

    def _sweep_once(self):
        """一次完整 top→bottom 扫描；每屏【逐个】处理命中物品：每次取最靠上的一个处理，处理完不急着滚，
        原地重新识别接着找同屏剩下的，等同屏再没有可处理物品了才往下滚一屏（用户拍板「卖完一件别急着滚」）。
        实战：成功处理完一件物品返回 HANDLED（scroll_search 据此原地不滚、重 probe 续找同屏剩下的，且不拿被
        动作污染的画面判到底）；演练只识别无副作用，返回 SCROLL 正常往下翻并帧差判到底。
        ⚠ 动作【失败】（按钮没找到、放弃该物品）返回 SCROLL：此时画面已被 _cleanup 收回干净列表、未被污染，
          继续 SCROLL 让帧差能在到底时正常收工；若失败也返回 HANDLED，会在「包裹底部有个按钮永远匹配不到的
          物品」上原地反复『点开→放弃』空转直到 scroll_max_tries 耗尽，正是用户撞到的卡死放大场景。"""
        count = {"n": 0}

        def probe(scene, rect):
            if scene is None:
                return scan.SCROLL, None
            best = None   # (y_local, it, (sx, sy, score))
            for it, tpl in self._item_tpls:
                m = vision.match(scene, tpl, self.threshold)
                if m is None:
                    continue
                if best is None or m[1] < best[0]:
                    best = (m[1], it, (rect[0] + m[0], rect[1] + m[1], m[2]))
            if best is None:
                return scan.SCROLL, None
            _, it, (sx, sy, score) = best
            action = it.get("action", "use")
            if self.dry_run:
                count["n"] += 1
                self.ctx.log(f"识别到物品『{it.get('name', '?')}』（{score:.2f}）→ 计划动作："
                             f"{action_label(action)}（演练不执行）。", level="hit")
                return scan.SCROLL, None
            # 只有真正完成动作（卖/用/丢成功）才计数 + HANDLED：scroll_search 据此原地不滚、重新识别接着
            # 找同屏剩下的可处理物品（卖完别急着滚）。失败时物品仍在、画面未污染，返回 SCROLL 让它往下翻并帧差判到底。
            if self._do_action(it, action, sx, sy, score):
                count["n"] += 1
                return scan.HANDLED, None
            return scan.SCROLL, None

        scan.scroll_search(
            grab_rect=self._region_rect,
            probe=probe,
            mouse=self.ctx.mouse,
            should_stop=self.ctx.should_stop,
            sleep=lambda s: self._sleep(self._jitter(s)),
            scroll_step=self.loop.get("scroll_step", -3),
            max_tries=max(1, self.loop.get("scroll_max_tries", 30)),
            settle_sec=self.loop.get("scroll_settle_sec", 0.35),
            reset_to_top=self.loop.get("scroll_reset_top", True),
            end_diff=self.loop.get("scroll_end_diff", 2.0),
            reset_max=self.loop.get("scroll_reset_max", 20),
            log=self.ctx.log,
            label="背包",
        )
        return count["n"]

    def _do_action(self, it, action, sx, sy, score):
        """对命中物品执行其动作的声明式步骤序列（见 _ACTION_SPECS）。
        返回 True=动作整套跑完（成功）；False=未知动作 / 必需按钮没找到放弃 / 中途收到停止信号。
        probe 据此决定 HANDLED(成功，画面已变) 还是 SCROLL(失败，画面未污染、继续翻并帧差判到底)。"""
        ctx = self.ctx
        name = it.get("name", "?")
        spec = _ACTION_SPECS.get(action)
        if spec is None:
            ctx.log(f"物品『{name}』动作「{action}」未知，跳过。", level="warn")
            return False
        label = spec["label"]
        # ① 点中物品 → 右键弹操作菜单 / 左键弹详情（由 spec.open 指定，缺省用 open_item_click）。
        how = spec.get("open") or self.loop.get("open_item_click", "right")
        self._click(how, sx, sy)
        ctx.log(f"物品『{name}』（{score:.2f}）→ {label}：已点开。", level="hit")
        self._sleep(self._jitter(self.loop.get("action_settle_sec", 0.4)))
        # 各步「等按钮出现」的超时窗口与可选步骤跳过后的稳定等待（走 loop.* 配置，缺省回退现有键）。
        step_timeout = self.loop.get("step_find_timeout_sec", 1.5)        # 必需按钮：吸收子菜单淡入/掉帧
        # 可选按钮（如「更多」）默认用与必需步同样长的超时：若它确实存在、只是详情面板渲染慢，
        # 给够时间等出来再决定跳过，避免「面板没渲染完→更多被过早判没有→后续出售按钮因子菜单未展开必然 miss」。
        opt_timeout = self.loop.get("optional_find_timeout_sec", step_timeout)
        opt_settle = self.loop.get("optional_skip_settle_sec",
                                   self.loop.get("action_settle_sec", 0.3))
        # ② 逐步在整窗里找并点序列按钮；可选步骤缺了就跳过，必需步骤缺了就放弃该物品并收尾。
        #    查找改成【带超时轮询】：子菜单/详情面板常有淡入动画，单帧匹配会踩在空窗期误判按钮不存在。
        for step in spec["steps"]:
            if ctx.should_stop():
                return False
            tpl_key = step["tpl"]
            optional = step.get("optional")
            timeout = step.get("find_timeout", opt_timeout if optional else step_timeout)
            btn = self._find_full_wait(tpl_key, timeout)
            if btn is None:
                if optional:
                    # 可选步骤没出现：跳过前补一段稳定等待，让详情面板/子菜单渲染到位再找下一步按钮，
                    # 避免「未见更多→立刻一次性 miss 出售按钮」的级联假阴性。
                    ctx.log(f"  · 未见「{step['label']}」（可选步骤），跳过。")
                    self._sleep(self._jitter(opt_settle))
                    continue
                # 区分「模板没标定」与「标了但超时没匹配到」，给不同排查指引。
                if self._btn.get(tpl_key) is None:
                    ctx.log(f"  · 「{step['label']}」按钮模板未标定，放弃该物品"
                            f"（请到「标定」里补标 {tpl_key}）。", level="warn")
                else:
                    ctx.log(f"  · 没找到「{step['label']}」按钮（等待 {timeout:.1f}s 超时未匹配到），"
                            f"放弃该物品（请检查标定，或调大 loop.step_find_timeout_sec）。", level="warn")
                self._cleanup(spec)
                return False
            self._click(step.get("button", "left"), btn[0], btn[1])
            ctx.log(f"  · 点「{step['label']}」（{btn[2]:.2f}）。")
            self._sleep(self._jitter(step.get("wait", self.loop.get("step_wait_sec", 0.4))))
        # ③ 旧式单按钮动作(丢弃/旧出售)：弹「确定」确认框就点（破坏性、用户明确要）。
        if spec.get("confirm"):
            self._confirm_if_present()
        # ④ 收尾：关掉残留菜单/弹窗，回干净列表再继续滚动；
        # 否则点击会落在残留模态上被吞/点歪，残留窗口也会拉高帧差、干扰 scroll_search 的「帧差判到底」。
        self._cleanup(spec)
        return True

    def _click(self, how, x, y):
        """按 how(left/right/double) 拟人化点击屏幕坐标。"""
        if how == "right":
            self.ctx.mouse.right_click(x, y)
        elif how == "double":
            self.ctx.mouse.double_click(x, y)
        else:
            self.ctx.mouse.click(x, y)

    def _cleanup(self, spec):
        """动作收尾，把残留菜单/弹窗关掉回干净的包裹列表（见 _ACTION_SPECS 的 close 说明）。
        close=right：窗口中心右键关弹窗（摆摊出售窗靠右键回包裹）；
        close=auto ：动作窗口会自我关闭（商会出售点「出售」即自动关回包裹）——【默认不发任何键】，
                     仅当探测到子面板还在（出售/满 等按钮仍匹配）才补发 close_panel 关它；
                     **绝不无脑 ESC**，否则窗口已自动关掉时这记 ESC 会把包裹关了（踩坑：整理提前收工）。
        其余(panel)：发 close_panel(ESC) 关「会一直开着等 ESC」的右键菜单/确认框（使用/丢弃）。"""
        close = spec.get("close")
        if close == "right":
            rect = self.ctx.window.rect()
            if rect is not None:
                self.ctx.mouse.right_click(rect[0] + rect[2] // 2, rect[1] + rect[3] // 2)
            else:
                self.ctx.send_hotkey("close_panel")
        elif close == "auto":
            if self._action_panel_present(spec):
                self.ctx.send_hotkey("close_panel")   # 子面板没自动关掉，补一记 ESC 关它
            # 否则什么都不做：窗口已自我关闭、当前只剩包裹，发 ESC 会误关包裹。
        else:
            self.ctx.send_hotkey("close_panel")
        self._sleep(self._jitter(0.3))

    def _action_panel_present(self, spec):
        """该动作【自己的子面板】是否还在屏上：用它步骤里的按钮模板单帧探测，任一仍匹配即认为面板还开着。
        供 close=auto 判断「要不要补 ESC」：能匹配到出售/满 等按钮=窗口没自动关→补 ESC 关它；
        全匹配不到=窗口已关回包裹→绝不发 ESC（否则会把包裹关了）。模板未标定的步骤跳过不计。"""
        for s in spec.get("steps", []):
            if self._btn.get(s["tpl"]) is None:
                continue
            if self._find_full_wait(s["tpl"], 0.0) is not None:
                return True
        return False

    def _confirm_if_present(self):
        if self._btn.get("confirm_button") is None:
            return
        # 复用统一的「带超时轮询」：在 confirm_timeout_sec 内反复找「确定」，命中即点。
        hit = self._find_full_wait("confirm_button", self.loop.get("confirm_timeout_sec", 6))
        if hit is not None:
            self.ctx.mouse.click(hit[0], hit[1])
            self.ctx.log(f"点「确定」确认（{hit[2]:.2f}）。", level="hit")
            return
        # 确认框确实弹了但没匹配到「确定」：告警，靠 _do_action 收尾的 close_panel 关掉模态，不留模态继续滚。
        self.ctx.log("等「确定」确认超时（没匹配到确认按钮），将关菜单跳过该物品（请检查 confirm_button 标定）。",
                     level="warn")

    def _find_full(self, tpl_key):
        """整窗找按钮模板（单帧）。命中返回屏幕 (x,y,score)，否则 None。
        保留作向后兼容的单次匹配入口；新代码请用 _find_full_wait 带超时轮询。"""
        return self._find_full_wait(tpl_key, 0.0)

    def _find_full_wait(self, tpl_key, timeout=0.0):
        """整窗找按钮模板（菜单/子菜单位置不定且常有淡入动画），带超时轮询。
        在 timeout 秒内反复 grab+match，命中即返回屏幕 (x,y,score)；模板未标定/超时/收到停止信号返回 None。
        timeout<=0 时退化为单次匹配（取一帧判一次），等价旧 _find_full 行为。
        轮询节流用 self._jitter 保持拟人化；只在实战路径调用，dry_run 在 probe 阶段已 return，不触达。"""
        tpl = self._btn.get(tpl_key)
        if tpl is None:
            return None
        poll = self.loop.get("find_poll_interval_sec", 0.2)
        deadline = time.time() + max(0.0, timeout)
        while True:
            if self.ctx.should_stop():
                return None
            rect = self.ctx.window.rect()
            if rect is not None:
                scene = win_mod.grab(rect)
                if scene is not None:
                    m = vision.match(scene, tpl, self.threshold)
                    if m is not None:
                        return (rect[0] + m[0], rect[1] + m[1], m[2])
            if time.time() >= deadline:
                return None
            self._sleep(self._jitter(poll))
