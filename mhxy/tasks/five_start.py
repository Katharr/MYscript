# -*- coding: utf-8 -*-
"""
一键五开任务（状态机实现五开流程自动化）。

完整流程：
  运行计划任务启动游戏客户端 → 等待启动完成
  → 点击「开始游戏」按钮3次（打开号1-3）
  → 处理上限提示框（点确定）
  → 点击「开始游戏」按钮2次（打开号4-5）
  → 分阶段批量处理：
      第1阶段：选择所有账号
      第2阶段：统一进入游戏
      第3阶段：统一登录游戏
      第4阶段：统一等待排队 → 关闭广告

停止条件：
  ①完成所有账号登录 ②时间上限 ③手动停止/急停键。

安全默认 dry_run=true：不运行计划任务、不点击按钮，只做模板识别自检。
"""

import time
import subprocess

from ..core import scan
from ..core import vision
from ..core import window as win_mod
from .base import Task, register

# 状态机状态
S_START_CLIENT = "START_CLIENT"       # 运行计划任务启动客户端
S_CLICK_START = "CLICK_START"         # 点击开始游戏按钮
S_HANDLE_LIMIT = "HANDLE_LIMIT"       # 处理上限提示框
S_SELECT_ALL_ACCOUNTS = "SELECT_ALL_ACCOUNTS"   # 选择所有账号阶段
S_ENTER_ALL_GAMES = "ENTER_ALL_GAMES"           # 统一进入游戏阶段
S_LOGIN_ALL = "LOGIN_ALL"                       # 统一登录阶段
S_WAIT_ALL_QUEUES = "WAIT_ALL_QUEUES"           # 统一等待排队阶段
S_CLOSE_ALL_ADS = "CLOSE_ALL_ADS"               # 统一关闭广告阶段

_REQUIRED_FLAGS = ["start_game_btn", "limit_dialog", "limit_confirm",
                   "account_dropdown", "account_1", "account_2", "account_3",
                   "account_4", "account_5", "enter_game_btn", "login_game_btn",
                   "exit_queue_btn"]


@register
class FiveStartTask(Task):
    name = "five_start"
    title = "一键五开"
    description = "自动启动游戏客户端并完成5个账号的登录流程"
    is_dungeon = False
    CHAINS_PER_WINDOW = False

    _FLAG_KEYS = [
        "start_game_btn",       # 开始游戏按钮
        "limit_dialog",         # 上限提示框
        "limit_confirm",        # 上限提示框的确定按钮
        "account_dropdown",     # 登录账号下拉列表
        "account_1",            # 号1账号选项
        "account_2",            # 号2账号选项
        "account_3",            # 号3账号选项
        "account_4",            # 号4账号选项
        "account_5",            # 号5账号选项
        "enter_game_btn",       # 进入游戏按钮
        "login_game_btn",       # 登录游戏按钮
        "exit_queue_btn",       # 退出排队按钮（检测排队状态）
        "welcome_screen",       # 开屏宣传广告（可选）
    ]

    CALIBRATION = {
        "no_game_window": True,  # 一键五开在启动器界面操作，不需要游戏窗口
        "regions": [
            ("scene", "主识别区", "留空=整个屏幕当识别区(推荐)；启动器界面各按钮都在这里找", True),
            ("account_dropdown_list", "账号下拉列表区域", "账号下拉列表展开后显示的列表区域，用于滚动查找账号选项"),
        ],
        "templates": [
            ("start_game_btn", "开始游戏按钮", "游戏启动器界面里的「开始游戏」按钮"),
            ("limit_dialog", "上限提示框", "打开第3个窗口后弹出的「窗口数量已达上限」提示框"),
            ("limit_confirm", "上限提示框确定按钮", "上限提示框里的「确定」按钮"),
            ("account_dropdown", "账号下拉列表", "登录界面的账号选择下拉列表"),
            ("account_1", "号1账号选项", "下拉列表展开后的「号1」选项"),
            ("account_2", "号2账号选项", "下拉列表展开后的「号2」选项"),
            ("account_3", "号3账号选项", "下拉列表展开后的「号3」选项"),
            ("account_4", "号4账号选项", "下拉列表展开后的「号4」选项"),
            ("account_5", "号5账号选项", "下拉列表展开后的「号5」选项"),
            ("enter_game_btn", "进入游戏按钮", "选择账号后要点的「进入游戏」按钮"),
            ("login_game_btn", "登录游戏按钮", "登录界面里的「登录游戏」按钮"),
            ("exit_queue_btn", "退出排队按钮", "排队时显示的「退出排队」按钮（用于检测排队状态）"),
            ("welcome_screen", "开屏广告(可选)", "登录成功后弹出的开屏宣传广告画面"),
        ],
        "watchlist": False,
    }

    def preflight(self, ctx):
        """启动前自检：验证配置和模板是否就绪"""
        problems = []
        tc = ctx.task_cfg(self.name)

        # 检查计划任务名称配置
        task_name = tc.get("task_name", "mhxy")
        if not task_name:
            problems.append("计划任务名称(task_name)未配置 —— 请在配置文件中填写Windows计划任务名称")

        # 检查账号数量配置
        account_count = tc.get("account_count", 5)
        if account_count < 1 or account_count > 10:
            problems.append(f"账号数量(account_count)配置无效: {account_count}（应在1-10之间）")

        # 检查必备模板
        templates = tc.get("templates", {})
        for fk in _REQUIRED_FLAGS:
            path = templates.get(fk)
            if not path or vision.load_template(path) is None:
                problems.append(f"模板『{fk}』缺失或加载失败 —— 请在标定向导里框选裁图")

        # 检查窗口选择（至少要有目标窗口的概念）
        if not ctx.cfg.get("window_title"):
            problems.append("窗口标题(window_title)未配置 —— 请在配置文件中填写游戏窗口标题")

        # 可选模板缺失只提示
        optional = ["welcome_screen"]
        for tk in optional:
            if not templates.get(tk) or vision.load_template(templates.get(tk)) is None:
                ctx.log(f"提示：可选模板『{tk}』未标定，将降级靠固定位置点击（可靠性略降）。", level="warn")

        return (len(problems) == 0), problems

    # ------------------------------------------------------------------
    def run(self, ctx):
        """执行五开流程"""
        tc = ctx.task_cfg(self.name)
        loop = tc.get("loop", {})
        dry_run = tc.get("dry_run", True)
        task_name = tc.get("task_name", "mhxy")
        account_count = tc.get("account_count", 5)
        start_wait_sec = tc.get("start_wait_sec", 5)
        queue_timeout_sec = tc.get("queue_timeout_sec", 300)
        login_interval_sec = tc.get("login_interval_sec", 2)
        threshold = loop.get("match_threshold", 0.85)

        # 加载模板
        self.flags = self._load_flags(tc)

        start_ts = time.time()
        time_limit = loop.get("time_limit_min", 0) or 0
        deadline = start_ts + time_limit * 60 if time_limit > 0 else None

        # 状态机初始化
        state = S_START_CLIENT
        launcher_count = 0     # 已启动的启动器数量（0, 1, 2, 3）
        click_per_launcher = 0 # 当前启动器已点击开始游戏的次数（0, 1, 2）
        window_count = 0       # 已打开的游戏窗口总数（0-5）
        # 每个阶段独立的计数器
        select_account_index = 0  # 选择账号阶段的计数器
        enter_game_index = 0      # 进入游戏阶段的计数器
        login_index = 0           # 登录阶段的计数器
        queue_index = 0           # 排队阶段的计数器
        t_state = time.time()

        ctx.log("开始一键五开流程…")
        if dry_run:
            ctx.log("演练模式：不会真正启动客户端和点击按钮，只做模板识别自检。", level="warn")

        while not ctx.should_stop():
            if deadline and time.time() >= deadline:
                ctx.log(f"已达时间上限 {time_limit} 分钟，停止。", level="warn")
                break

            # 状态机推进
            if state == S_START_CLIENT:
                # 启动启动器（一个启动器最多打开2个窗口）
                # 启动器1: 打开窗口1-2; 启动器2: 打开窗口3-4; 启动器3: 打开窗口5
                if self._start_client(ctx, task_name, start_wait_sec, dry_run, launcher_count + 1):
                    launcher_count += 1
                    click_per_launcher = 0  # 重置点击计数器
                    state = S_CLICK_START
                    t_state = time.time()
                    ctx.log(f"启动器{launcher_count}启动完成，开始打开游戏窗口…")
                else:
                    ctx.log(f"启动器{launcher_count + 1}启动失败，中止流程。", level="error")
                    break

            elif state == S_CLICK_START:
                # 点击开始游戏按钮（每个启动器点击1次，打开1个窗口）
                # 启动器1：点击1次 → 打开窗口1
                # 启动器2：点击1次 → 打开窗口2
                # ...
                # 启动器5：点击1次 → 打开窗口5
                max_clicks = 1  # 所有启动器都只点击1次

                result = self._click_start_game(ctx, window_count, account_count,
                                                 launcher_count, click_per_launcher,
                                                 threshold, dry_run)
                if result == "next_launcher":
                    # 当前启动器已用完（点击了1次），需要启动下一个启动器
                    # 重要：启动器不能同时运行多个实例，需要等待当前游戏窗口启动完成
                    if not dry_run:
                        wait_sec = tc.get("launcher_launch_wait_sec", 5.0)
                        ctx.log(f"等待 {wait_sec:.1f} 秒让游戏窗口启动完成，再启动下一个启动器…")
                        self._interruptible_sleep(ctx, wait_sec)

                    click_per_launcher = 0
                    state = S_START_CLIENT
                    t_state = time.time()
                elif result == "done":
                    # 已打开所有窗口（5个窗口）
                    ctx.log(f"已打开 {account_count} 个游戏窗口，开始等待窗口完全启动…")
                    state = S_HANDLE_LIMIT  # 通过 S_HANDLE_LIMIT 触发等待和排列
                    t_state = time.time()
                elif result == "fail":
                    ctx.log("点击开始游戏按钮失败，中止流程。", level="error")
                    break
                elif result == "continue":
                    # 继续点击打开下一个窗口（一次点击打开1个窗口）
                    window_count += 1
                    click_per_launcher += 1

                    # 判断是否已打开所有窗口
                    if window_count >= account_count:
                        ctx.log(f"已打开 {account_count} 个游戏窗口，开始等待窗口完全启动…")
                        state = S_HANDLE_LIMIT  # 通过 S_HANDLE_LIMIT 触发等待和排列
                        t_state = time.time()

            elif state == S_HANDLE_LIMIT:
                # 等待所有游戏窗口完全启动、排列窗口、统一调整尺寸
                # 不处理上限提示框（每个启动器只打开1个窗口，不会触发上限）

                # 步骤1：等待所有游戏窗口完全启动
                ctx.log("等待所有游戏窗口完全启动（出现登录界面）…")
                ok, count = self._wait_game_windows(ctx, account_count)
                if not ok:
                    ctx.log(f"游戏窗口启动失败，中止流程。", level="error")
                    break
                ctx.log(f"✓ 检测到 {count} 个游戏窗口已启动。")

                # 步骤2：自动排列窗口（从左到右水平布局）
                ctx.log("开始自动排列窗口…")
                if not dry_run:
                    ok = self._arrange_windows(ctx)
                    if ok:
                        ctx.log("✓ 已排列窗口（从左到右水平布局）。")
                    else:
                        ctx.log("窗口排列失败，继续登录流程。", level="warn")
                else:
                    ctx.log("演练模式：跳过窗口排列。")

                # 步骤3：统一调整所有窗口尺寸为500x900
                ctx.log("开始统一调整窗口尺寸为 500x900…")
                if not dry_run:
                    ok = self._resize_all_windows(ctx, 500, 900)
                    if ok:
                        ctx.log("✓ 已统一调整所有窗口尺寸为 500x900。")
                    else:
                        ctx.log("窗口尺寸调整失败，继续登录流程。", level="warn")
                else:
                    ctx.log("演练模式：跳过窗口尺寸调整。")

                # 进入账号登录流程
                ctx.log("开始登录账号…")
                state = S_SELECT_ALL_ACCOUNTS
                t_state = time.time()

            elif state == S_SELECT_ALL_ACCOUNTS:
                # 第1阶段：选择所有账号
                if select_account_index >= account_count:
                    ctx.log(f"✓ 第1阶段完成：已选择所有 {account_count} 个账号。", level="hit")
                    state = S_ENTER_ALL_GAMES
                    t_state = time.time()
                    ctx.log("开始第2阶段：统一进入游戏…")
                else:
                    ctx.log(f"第1阶段：选择号{select_account_index + 1}账号…")
                    if self._select_account(ctx, select_account_index, threshold, dry_run):
                        select_account_index += 1
                        # 演练模式下跳过间隔等待
                        if not dry_run and select_account_index < account_count:
                            self._interruptible_sleep(ctx, login_interval_sec)
                    else:
                        ctx.log(f"选择号{select_account_index + 1}失败，中止流程。", level="error")
                        break

            elif state == S_ENTER_ALL_GAMES:
                # 第2阶段：统一进入游戏
                if enter_game_index >= account_count:
                    ctx.log(f"✓ 第2阶段完成：所有 {account_count} 个账号已点击进入游戏。", level="hit")
                    state = S_LOGIN_ALL
                    t_state = time.time()
                    ctx.log("开始第3阶段：统一登录游戏…")
                else:
                    ctx.log(f"第2阶段：号{enter_game_index + 1}点击进入游戏…")
                    if self._click_enter_game(ctx, enter_game_index, threshold, dry_run):
                        enter_game_index += 1
                        # 演练模式下跳过间隔等待
                        if not dry_run and enter_game_index < account_count:
                            self._interruptible_sleep(ctx, login_interval_sec)
                    else:
                        ctx.log(f"号{enter_game_index + 1}点击进入游戏失败，中止流程。", level="error")
                        break

            elif state == S_LOGIN_ALL:
                # 第3阶段：统一登录游戏
                if login_index >= account_count:
                    ctx.log(f"✓ 第3阶段完成：所有 {account_count} 个账号已点击登录。", level="hit")
                    state = S_WAIT_ALL_QUEUES
                    t_state = time.time()
                    ctx.log("开始第4阶段：统一等待排队并关闭广告…")
                else:
                    ctx.log(f"第3阶段：号{login_index + 1}点击登录游戏…")
                    if self._click_login_game(ctx, login_index, threshold, dry_run):
                        login_index += 1
                        # 演练模式下跳过间隔等待
                        if not dry_run and login_index < account_count:
                            self._interruptible_sleep(ctx, login_interval_sec)
                    else:
                        ctx.log(f"号{login_index + 1}点击登录游戏失败，中止流程。", level="error")
                        break

            elif state == S_WAIT_ALL_QUEUES:
                # 第4阶段：统一等待排队并关闭广告
                if queue_index >= account_count:
                    ctx.log(f"✓ 第4阶段完成：所有 {account_count} 个账号已登录完成。", level="hit")
                    break

                ctx.log(f"第4阶段：号{queue_index + 1}等待排队结束…")
                result = self._wait_queue(ctx, queue_index, queue_timeout_sec, threshold, dry_run)
                if result == "done":
                    # 排队结束，关闭广告
                    ctx.log(f"号{queue_index + 1}排队结束，关闭广告…")
                    if self._close_welcome_ad(ctx, queue_index, threshold, dry_run):
                        ctx.log(f"✓ 号{queue_index + 1}登录完成。", level="hit")
                        queue_index += 1
                        # 演练模式下跳过间隔等待
                        if not dry_run and queue_index < account_count:
                            self._interruptible_sleep(ctx, login_interval_sec)
                    else:
                        ctx.log(f"号{queue_index + 1}关闭广告失败，跳过该账号。", level="warn")
                        queue_index += 1
                        if not dry_run and queue_index < account_count:
                            self._interruptible_sleep(ctx, login_interval_sec)
                elif result == "timeout":
                    ctx.log(f"号{queue_index + 1}排队超时，跳过该账号。", level="warn")
                    queue_index += 1
                    # 演练模式下跳过间隔等待
                    if not dry_run and queue_index < account_count:
                        self._interruptible_sleep(ctx, login_interval_sec)
                elif result == "fail":
                    ctx.log(f"号{queue_index + 1}等待排队失败，中止流程。", level="error")
                    break

            self._interruptible_sleep(ctx, loop.get("tick_interval_sec", 0.5))

        # 结束汇总
        elapsed = time.time() - start_ts
        if queue_index >= account_count:
            ctx.log(f"一键五开完成：成功登录 {account_count} 个账号，用时 {elapsed:.1f} 秒。")
        else:
            ctx.log(f"一键五开中止：已完成 {queue_index}/{account_count} 个账号登录，用时 {elapsed:.1f} 秒。", level="warn")

    def _arrange_windows(self, ctx):
        """一键五开完成后自动排列窗口"""
        from ..core import arrange

        base = ctx.cfg.get("targets", {}).get("base_size")
        if not base or len(base) < 2:
            ctx.log(f"未设置基准尺寸（当前：{base}），跳过自动排列窗口。", level="warn")
            return

        # 调试：显示窗口标题配置
        window_title = ctx.cfg.get("window_title", "梦幻西游")
        window_offset = ctx.cfg.get("window_offset", [0, 0])
        ctx.log(f"准备排列窗口：标题含「{window_title}」，偏移 {window_offset}")

        wins = ctx.select_windows()
        if not wins:
            # 尝试列出所有窗口，帮助诊断问题
            try:
                import pygetwindow as gw
                all_windows = gw.getAllWindows()
                ctx.log(f"检测到 {len(all_windows)} 个桌面窗口：", level="warn")
                for i, w in enumerate(all_windows[:10], 1):  # 只显示前10个
                    try:
                        title = w.title or "(无标题)"
                        ctx.log(f"  [{i}] {title}")
                    except Exception:
                        pass
                if len(all_windows) > 10:
                    ctx.log(f"  ...（还有 {len(all_windows) - 10} 个窗口）")
            except Exception as e:
                ctx.log(f"列出桌面窗口失败：{e}", level="warn")

            ctx.log(f"没有检测到标题含「{window_title}」的游戏窗口，跳过排列。", level="warn")
            return

        # 获取排列配置
        tc = ctx.task_cfg(self.name)
        gap = tc.get("arrange_gap", 10)

        # 检查是否能排列
        can_arrange, msg = arrange.get_arrangement_info(base, len(wins), gap)
        if not can_arrange:
            ctx.log(f"无法排列窗口：{msg}", level="warn")
            return

        # 执行排列
        ok = arrange.arrange_windows(wins, base, screen_gap=gap)
        if ok < len(wins):
            ctx.log(f"已排列 {ok}/{len(wins)} 个窗口（部分窗口可能锁定了分辨率）。", level="warn")
        else:
            ctx.log(f"✓ 已排列 {ok} 个窗口（从左到右水平布局）。")

    def _resize_all_windows(self, ctx, target_w, target_h):
        """统一调整所有游戏窗口尺寸

        Args:
            ctx: 上下文对象
            target_w: 目标宽度（如500）
            target_h: 目标高度（如900）

        Returns:
            bool: 是否成功调整至少一个窗口
        """
        try:
            wins = ctx.select_windows()
            if not wins:
                ctx.log("未找到游戏窗口，无法调整尺寸。", level="warn")
                return False

            success_count = 0
            for i, win in enumerate(wins):
                try:
                    win.resize_to(target_w, target_h)
                    ctx.log(f"窗口 {i+1} 尺寸已调整为 {target_w}x{target_h}。")
                    success_count += 1
                except Exception as e:
                    ctx.log(f"窗口 {i+1} 尺寸调整失败：{e}", level="warn")

            ctx.log(f"✓ 成功调整 {success_count}/{len(wins)} 个窗口尺寸。")
            return success_count > 0
        except Exception as e:
            ctx.log(f"统一调整窗口尺寸失败：{e}", level="error")
            return False

    # ------------------------------------------------------------------
    # 状态处理方法
    # ------------------------------------------------------------------
    def _start_client(self, ctx, task_name, start_wait_sec, dry_run, launcher_index):
        """运行计划任务启动游戏客户端（第N个启动器），并等待窗口出现"""
        if dry_run:
            ctx.log(f"演练：跳过运行计划任务（启动器{launcher_index}）。", level="warn")
            return True

        # 获取启动器窗口标题配置
        tc = ctx.task_cfg(self.name)
        launcher_title = tc.get("launcher_window_title", "MyPCLauncher_x64r")
        launcher_offset = ctx.cfg.get("window_offset", [0, 0])

        ctx.log(f"运行计划任务『{task_name}』启动启动器{launcher_index}…")

        try:
            # 使用 schtasks 命令运行计划任务
            cmd = f'schtasks /run /tn "{task_name}"'
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)

            if result.returncode != 0:
                ctx.log(f"运行计划任务失败：{result.stderr}", level="error")
                return False

            ctx.log(f"计划任务已触发，等待启动器{launcher_index}（窗口：{launcher_title}）出现…")

            # 等待启动器窗口出现（最多等待30秒）
            # 重要：启动器1可能在点击后关闭，所以每次启动都重新计数
            max_wait_sec = 30
            check_interval = 1.0
            elapsed = 0.0
            initial_launcher_count = 0  # 初始启动器窗口数量

            # 先检测当前已有的启动器窗口数量
            try:
                import pygetwindow as gw
                all_windows = gw.getAllWindows()
                for w in all_windows:
                    try:
                        if launcher_title in (w.title or ""):
                            initial_launcher_count += 1
                    except Exception:
                        continue
                ctx.log(f"当前已有 {initial_launcher_count} 个启动器窗口，等待第 {launcher_index} 个启动器窗口出现（期望总数：{initial_launcher_count + 1}）")
            except Exception:
                pass

            while elapsed < max_wait_sec:
                if ctx.should_stop():
                    return False

                # 检测窗口是否存在（使用底层API，不检查尺寸）
                try:
                    import pygetwindow as gw
                    all_windows = gw.getAllWindows()
                    launcher_count = 0
                    launcher_titles = []  # 记录所有启动器窗口的标题（用于调试）

                    for w in all_windows:
                        try:
                            title = w.title or ""
                            # 标题匹配（不检查尺寸和最小化状态）
                            if launcher_title in title:
                                launcher_count += 1
                                launcher_titles.append(title)
                        except Exception:
                            continue

                    # 需要等待新的启动器窗口出现（总数应增加）
                    expected_count = initial_launcher_count + 1
                    
                    # 每次检测都打印日志（前5秒），方便诊断
                    if elapsed < 5 or launcher_count >= expected_count:
                        if launcher_titles:
                            ctx.log(f"检测：共 {launcher_count} 个启动器窗口（标题：{', '.join(launcher_titles)}），期望 {expected_count} 个")
                        else:
                            ctx.log(f"检测：共 0 个启动器窗口（未找到标题含「{launcher_title}」的窗口），期望 {expected_count} 个")
                    
                    if launcher_count >= expected_count:
                        ctx.log(f"启动器{launcher_index}窗口已出现（当前共 {launcher_count} 个启动器窗口），等待 0.5 秒让界面加载…")
                        self._interruptible_sleep(ctx, 0.5)
                        return True
                except Exception as e:
                    # 捕获异常并记录（仅第一次）
                    if elapsed == 0:
                        ctx.log(f"检测窗口时异常：{e}", level="warn")

                self._interruptible_sleep(ctx, check_interval)
                elapsed += check_interval

            ctx.log(f"启动器{launcher_index}窗口未在 {max_wait_sec} 秒内出现。", level="error")
            return False

        except subprocess.TimeoutExpired:
            ctx.log("运行计划任务超时。", level="error")
            return False
        except Exception as e:
            ctx.log(f"运行计划任务异常：{e}", level="error")
            return False

    def _click_start_game(self, ctx, window_count, account_count, launcher_index, click_count, threshold, dry_run):
        """点击开始游戏按钮（打开游戏窗口）

        参数：
        - window_count: 已打开的窗口总数
        - account_count: 目标窗口总数（5）
        - launcher_index: 当前启动器编号（1, 2, 3）
        - click_count: 当前启动器已点击次数（0, 1, 2）

        返回值：
          "continue" - 成功点击，继续打开下一个窗口
          "limit" - 需要处理上限提示框（第3个启动器第1次点击后）
          "next_launcher" - 当前启动器已用完（点击了2次），需要启动下一个启动器
          "done" - 已打开所有窗口
          "fail" - 点击失败
        """
        # 已打开足够的窗口
        if window_count >= account_count:
            return "done"

        # 每个启动器只点击1次，点击后返回 "next_launcher"（如果是第5个启动器，返回 "done"）
        if click_count >= 1:
            if launcher_index >= 5:
                # 第5个启动器已点击1次，所有窗口已打开
                return "done"
            else:
                # 其他启动器已点击1次，需要启动下一个启动器
                ctx.log(f"启动器{launcher_index}已用完（点击了1次），需要启动下一个启动器…")
                return "next_launcher"

        btn_tpl = self.flags.get("start_game_btn")
        if btn_tpl is None:
            ctx.log("❌ start_game_btn 模板未加载。", level="error")
            return "fail"

        # 重试机制：如果未找到按钮，等待后重试（最多3次）
        max_retries = 3
        retry_interval = 2.0

        for attempt in range(1, max_retries + 1):
            # 激活启动器窗口到前台（使用底层API，避免尺寸限制）
            tc = ctx.task_cfg(self.name)
            launcher_title = tc.get("launcher_window_title", "MyPCLauncher_x64r")
            try:
                import pygetwindow as gw
                all_windows = gw.getAllWindows()
                launcher_wins = []
                for w in all_windows:
                    try:
                        if launcher_title in (w.title or ""):
                            launcher_wins.append(w)
                    except Exception:
                        continue

                if launcher_wins:
                    # 激活最后一个启动器窗口（最新出现的）
                    target_win = launcher_wins[-1]  # 使用最后一个窗口
                    target_win.activate()
                    if attempt == 1:
                        ctx.log(f"已激活启动器{launcher_index}窗口到前台（共 {len(launcher_wins)} 个启动器窗口，激活最后一个）。")
                    else:
                        ctx.log(f"重试{attempt}：重新激活启动器{launcher_index}窗口（最后一个）。")
                    self._interruptible_sleep(ctx, 0.3)  # 等待窗口激活
            except Exception as e:
                ctx.log(f"激活启动器窗口失败：{e}，继续尝试查找按钮。", level="warn")

            # 截取整个屏幕（启动器界面）
            scene = win_mod.grab(None)  # 截取整个屏幕
            if scene is None:
                ctx.log("截图失败。", level="error")
                return "fail"

            hit = vision.match(scene, btn_tpl, threshold)
            if hit is not None:
                # 找到按钮，继续执行
                break

            # 未找到按钮
            if attempt < max_retries:
                ctx.log(f"未找到「开始游戏」按钮（阈值 {threshold}），等待 {retry_interval:.1f} 秒后重试（{attempt}/{max_retries}）…", level="warn")
                self._interruptible_sleep(ctx, retry_interval)
            else:
                ctx.log(f"未找到「开始游戏」按钮（阈值 {threshold}），已重试 {max_retries} 次，中止流程。", level="error")
                return "fail"

        x, y, score = hit
        if dry_run:
            ctx.log(f"演练：找到「开始游戏」按钮（{score:.3f}），跳过点击。", level="warn")
            return "continue"

        ctx.mouse.click(x, y)
        ctx.log(f"点击「开始游戏」按钮（{score:.3f}），打开第 {window_count + 1} 个窗口（启动器{launcher_index}第{click_count + 1}次点击）。")
        self._interruptible_sleep(ctx, 1.5)  # 等待窗口打开
        return "continue"

    def _handle_limit_dialog(self, ctx, threshold, dry_run):
        """处理上限提示框"""
        dialog_tpl = self.flags.get("limit_dialog")
        confirm_tpl = self.flags.get("limit_confirm")

        if dialog_tpl is None or confirm_tpl is None:
            ctx.log("❌ limit_dialog 或 limit_confirm 模板未加载。", level="error")
            return False

        # 截取整个屏幕
        scene = win_mod.grab(None)
        if scene is None:
            ctx.log("截图失败。", level="error")
            return False

        # 检查上限提示框是否存在
        dialog_hit = vision.match(scene, dialog_tpl, threshold)
        if dialog_hit is None:
            ctx.log("上限提示框未出现（可能已关闭）。", level="warn")
            return True  # 假设已处理

        # 查找确定按钮
        confirm_hit = vision.match(scene, confirm_tpl, threshold)
        if confirm_hit is None:
            ctx.log("未找到上限提示框的确定按钮。", level="error")
            return False

        x, y, score = confirm_hit
        if dry_run:
            ctx.log(f"演练：找到上限提示框确定按钮（{score:.3f}），跳过点击。", level="warn")
            return True

        ctx.mouse.click(x, y)
        ctx.log(f"点击上限提示框确定按钮（{score:.3f}）。")
        self._interruptible_sleep(ctx, 0.5)
        return True

    def _activate_account_window(self, ctx, account_index):
        """激活第 account_index 个游戏窗口，并返回该 GameWindow。"""
        wins = ctx.select_windows()
        if not wins:
            ctx.log("未找到游戏窗口，无法激活。", level="error")
            return None
        if len(wins) <= account_index:
            ctx.log(f"警告：游戏窗口数量不足（{len(wins)} 个），无法激活第 {account_index + 1} 个窗口。", level="warn")
            return None
        win = wins[account_index]
        if not win.activate():
            ctx.log(f"第 {account_index + 1} 个游戏窗口激活失败。", level="warn")
            return None
        ctx.log(f"已激活第 {account_index + 1} 个游戏窗口到前台。")
        self._interruptible_sleep(ctx, 0.5)
        return win

    @staticmethod
    def _offset_hit(hit, rect):
        """把窗口内匹配坐标转换成屏幕坐标。"""
        x, y, score = hit
        return rect[0] + x, rect[1] + y, score

    def _select_account(self, ctx, account_index, threshold, dry_run):
        """选择账号（点击账号下拉列表，然后滚动查找对应账号选项）

        参数：
        - account_index: 账号索引（0-4，对应号1-号5）
        - threshold: 匹配阈值
        - dry_run: 是否演练模式
        """
        tc = ctx.task_cfg(self.name)
        regions = tc.get("regions", {})
        loop = tc.get("loop", {})

        dropdown_tpl = self.flags.get("account_dropdown")
        if dropdown_tpl is None:
            ctx.log("❌ account_dropdown 模板未加载。", level="error")
            return False

        win = self._activate_account_window(ctx, account_index)
        if win is None:
            return False
        win_rect = win.rect()
        if win_rect is None:
            ctx.log("无法获取当前游戏窗口区域。", level="error")
            return False

        # 截取当前游戏窗口
        scene = win_mod.grab(win_rect)
        if scene is None:
            ctx.log("截图失败。", level="error")
            return False

        # 第一步：点击账号下拉列表
        hit = vision.match(scene, dropdown_tpl, threshold)
        if hit is None:
            ctx.log(f"当前窗口内未找到账号下拉列表（阈值 {threshold}）。", level="warn")
            return False

        x, y, score = self._offset_hit(hit, win_rect)
        if dry_run:
            ctx.log(f"演练：找到账号下拉列表（{score:.3f}），跳过点击。", level="warn")
            # 演练模式下继续检查账号选项是否存在
            account_key = f"account_{account_index + 1}"
            account_tpl = self.flags.get(account_key)
            if account_tpl is None:
                ctx.log(f"演练：账号模板 {account_key} 未加载。", level="warn")
            else:
                ctx.log(f"演练：账号模板 {account_key} 已加载，可进行滚动查找。")
            return True

        # 点击下拉列表
        ctx.mouse.click(x, y)
        ctx.log(f"点击账号下拉列表（{score:.3f}）。")
        self._interruptible_sleep(ctx, 0.8)  # 等待下拉列表展开

        # 第二步：滚动查找对应账号选项
        account_key = f"account_{account_index + 1}"
        account_tpl = self.flags.get(account_key)
        if account_tpl is None:
            ctx.log(f"❌ {account_key} 模板未加载，无法选择号{account_index + 1}。", level="error")
            return False

        # 定义账号下拉列表区域（用于滚动查找）
        list_region = regions.get("account_dropdown_list")

        def grab_rect():
            """获取账号下拉列表区域的屏幕矩形"""
            if list_region:
                return win.region_to_screen_rect(list_region)
            return win.rect()

        def probe(scene, rect):
            """探测函数：匹配账号模板"""
            if scene is None:
                return scan.SCROLL, None

            # 匹配账号选项模板（使用专用阈值）
            hit = vision.match(scene, account_tpl, account_threshold)
            if hit is None:
                # 尝试低阈值匹配，提示用户
                hit_low = vision.match(scene, account_tpl, 0.70)
                if hit_low:
                    cx, cy, score = hit_low
                    ctx.log(f"⚠ 低阈值(0.70)匹配到疑似{account_key}（{score:.3f}<{threshold}），建议降低阈值。", level="warn")
                else:
                    ctx.log(f"未找到{account_key}选项（阈值 {account_threshold}），继续滚动…", level="info")
                return scan.SCROLL, None

            cx, cy, score = hit
            # 计算屏幕坐标（rect 格式为 (x, y, w, h)）
            if rect:
                screen_x = rect[0] + cx
                screen_y = rect[1] + cy
            else:
                screen_x, screen_y = cx, cy

            ctx.log(f"✓ 找到{account_key}选项（{score:.3f}），坐标 ({screen_x}, {screen_y})。", level="info")

            # 找到账号选项，点击
            ctx.mouse.click(screen_x, screen_y)
            ctx.log(f"点击号{account_index + 1}账号选项（{score:.3f}）。", level="hit")
            self._interruptible_sleep(ctx, 0.3)

            return scan.ACCEPT, (screen_x, screen_y, score)

        # 从配置读取滚动参数
        scroll_step = tc.get("account_scroll_step", -3)
        max_tries = tc.get("account_scroll_max_tries", 8)
        settle_sec = tc.get("account_scroll_settle_sec", 0.35)

        # 使用账号识别专用阈值（因为邮箱账号用户名是递增数字，容易选错）
        account_threshold = tc.get("account_match_threshold", 0.9)

        ctx.log(f"开始在账号下拉列表滚动查找{account_key}（阈值 {account_threshold}，滚动步长 {scroll_step}，最大尝试 {max_tries} 次）…")

        try:
            res = scan.scroll_search(
                grab_rect=grab_rect, probe=probe, mouse=ctx.mouse,
                should_stop=ctx.should_stop,
                sleep=lambda s: self._interruptible_sleep(ctx, s),
                scroll_step=scroll_step,
                max_tries=max(1, max_tries),
                settle_sec=settle_sec,
                reset_to_top=False,  # 下拉列表不需要先滚到顶
                log=ctx.log, label="账号下拉列表"
            )
            return res.found
        except Exception as e:
            ctx.log(f"滚动查找账号异常：{e}", level="error")
            return False

    def _click_enter_game(self, ctx, account_index, threshold, dry_run):
        """点击进入游戏按钮

        参数：
        - account_index: 账号索引（0-4，对应号1-号5）
        - threshold: 匹配阈值
        - dry_run: 是否演练模式
        """
        btn_tpl = self.flags.get("enter_game_btn")
        if btn_tpl is None:
            ctx.log("❌ enter_game_btn 模板未加载。", level="error")
            return False

        win = self._activate_account_window(ctx, account_index)
        if win is None:
            return False
        win_rect = win.rect()
        if win_rect is None:
            ctx.log("无法获取当前游戏窗口区域。", level="error")
            return False

        scene = win_mod.grab(win_rect)
        if scene is None:
            ctx.log("截图失败。", level="error")
            return False

        hit = vision.match(scene, btn_tpl, threshold)
        if hit is None:
            ctx.log(f"当前窗口内未找到「进入游戏」按钮（阈值 {threshold}）。", level="warn")
            return False

        x, y, score = self._offset_hit(hit, win_rect)
        if dry_run:
            ctx.log(f"演练：找到「进入游戏」按钮（{score:.3f}），跳过点击。", level="warn")
            return True

        ctx.mouse.click(x, y)
        ctx.log(f"点击「进入游戏」按钮（{score:.3f}）。")
        self._interruptible_sleep(ctx, 0.5)
        return True

    def _click_login_game(self, ctx, account_index, threshold, dry_run):
        """点击登录游戏按钮

        参数：
        - account_index: 账号索引（0-4，对应号1-号5）
        - threshold: 匹配阈值
        - dry_run: 是否演练模式
        """
        btn_tpl = self.flags.get("login_game_btn")
        if btn_tpl is None:
            ctx.log("❌ login_game_btn 模板未加载。", level="error")
            return False

        win = self._activate_account_window(ctx, account_index)
        if win is None:
            return False
        win_rect = win.rect()
        if win_rect is None:
            ctx.log("无法获取当前游戏窗口区域。", level="error")
            return False

        scene = win_mod.grab(win_rect)
        if scene is None:
            ctx.log("截图失败。", level="error")
            return False

        hit = vision.match(scene, btn_tpl, threshold)
        if hit is None:
            ctx.log(f"当前窗口内未找到「登录游戏」按钮（阈值 {threshold}）。", level="warn")
            return False

        x, y, score = self._offset_hit(hit, win_rect)
        if dry_run:
            ctx.log(f"演练：找到「登录游戏」按钮（{score:.3f}），跳过点击。", level="warn")
            return True

        ctx.mouse.click(x, y)
        ctx.log(f"点击「登录游戏」按钮（{score:.3f}）。")
        self._interruptible_sleep(ctx, 1.0)
        return True

    def _wait_queue(self, ctx, account_index, queue_timeout_sec, threshold, dry_run):
        """等待排队结束

        参数：
        - account_index: 账号索引（0-4，对应号1-号5）
        - queue_timeout_sec: 排队超时秒数
        - threshold: 匹配阈值
        - dry_run: 是否演练模式

        返回值：
          "done" - 排队结束
          "timeout" - 排队超时
          "fail" - 检测失败
        """
        if dry_run:
            ctx.log("演练：跳过排队等待。", level="warn")
            return "done"

        win = self._activate_account_window(ctx, account_index)
        if win is None:
            return "fail"
        win_rect = win.rect()
        if win_rect is None:
            ctx.log("无法获取当前游戏窗口区域。", level="error")
            return "fail"

        exit_queue_tpl = self.flags.get("exit_queue_btn")
        if exit_queue_tpl is None:
            ctx.log("❌ exit_queue_btn 模板未加载。", level="error")
            return "fail"

        start = time.time()
        ctx.log(f"开始等待排队结束（超时 {queue_timeout_sec} 秒）…")

        while time.time() - start < queue_timeout_sec:
            if ctx.should_stop():
                return "fail"

            scene = win_mod.grab(win_rect)
            if scene is None:
                self._interruptible_sleep(ctx, 0.5)
                continue

            # 检查是否还在排队（退出排队按钮存在=正在排队）
            hit = vision.match(scene, exit_queue_tpl, threshold)
            if hit is None:
                # 退出排队按钮消失，排队结束
                ctx.log("排队结束。")
                return "done"

            # 每10秒报告一次排队状态
            elapsed = int(time.time() - start)
            if elapsed % 10 == 0 and elapsed > 0:
                ctx.log(f"排队中…已等待 {elapsed}/{queue_timeout_sec} 秒。")

            self._interruptible_sleep(ctx, 0.5)

        ctx.log(f"排队超时（{queue_timeout_sec} 秒）。", level="warn")
        return "timeout"

    def _wait_game_windows(self, ctx, expected_count):
        """等待游戏窗口完全启动（出现登录界面）

        参数：
        - expected_count: 期望的窗口数量（5）

        返回值：
        - (ok, count): ok表示是否成功，count表示检测到的窗口数量
        """
        window_title = ctx.cfg.get("window_title", "梦幻西游")
        window_offset = ctx.cfg.get("window_offset", [0, 0])

        # 最多等待30秒让所有游戏窗口启动完成
        max_wait_sec = 30
        check_interval = 1.0
        elapsed = 0.0

        ctx.log(f"等待 {expected_count} 个游戏窗口出现（标题含「{window_title}」）…")

        while elapsed < max_wait_sec:
            if ctx.should_stop():
                return (False, 0)

            # 检测游戏窗口数量
            try:
                import pygetwindow as gw
                all_windows = gw.getAllWindows()
                game_window_count = 0

                for w in all_windows:
                    try:
                        if window_title in (w.title or ""):
                            game_window_count += 1
                    except Exception:
                        continue

                if game_window_count >= expected_count:
                    ctx.log(f"✓ 检测到 {game_window_count} 个游戏窗口已启动。")
                    # 额外等待2秒，确保登录界面完全加载
                    ctx.log("等待 2 秒让登录界面完全加载…")
                    self._interruptible_sleep(ctx, 2.0)
                    return (True, game_window_count)
                else:
                    if elapsed == 0 or int(elapsed) % 5 == 0:  # 每5秒打印一次
                        ctx.log(f"检测到 {game_window_count}/{expected_count} 个游戏窗口，继续等待…")
            except Exception as e:
                ctx.log(f"检测窗口时异常：{e}", level="warn")

            self._interruptible_sleep(ctx, check_interval)
            elapsed += check_interval

        ctx.log(f"警告：等待超时，可能部分游戏窗口未完全启动。", level="warn")
        return (False, game_window_count)

    def _close_welcome_ad(self, ctx, account_index, threshold, dry_run):
        """关闭开屏广告

        参数：
        - account_index: 账号索引（0-4，对应号1-号5）
        - threshold: 匹配阈值
        - dry_run: 是否演练模式
        """
        win = self._activate_account_window(ctx, account_index)
        if win is None:
            return False
        win_rect = win.rect()
        if win_rect is None:
            ctx.log("无法获取当前游戏窗口区域。", level="error")
            return False

        welcome_tpl = self.flags.get("welcome_screen")

        if welcome_tpl is not None:
            # 有开屏广告模板，先检测是否存在
            scene = win_mod.grab(win_rect)
            if scene is None:
                ctx.log("截图失败。", level="error")
                return False

            hit = vision.match(scene, welcome_tpl, threshold)
            if hit is None:
                ctx.log("当前窗口内未检测到开屏广告（可能已关闭或不存在）。")
                return True

            if dry_run:
                ctx.log("演练：检测到开屏广告，跳过关闭。", level="warn")
                return True

            # 点击广告画面中心偏上位置关闭（假设点击任意位置都能关闭）
            x, y, score = self._offset_hit(hit, win_rect)
            # 点击检测到的广告中心偏上位置
            click_x = x + welcome_tpl.shape[1] // 2
            click_y = y + welcome_tpl.shape[0] // 3  # 上三分之一位置
            ctx.mouse.click(click_x, click_y)
            ctx.log(f"点击开屏广告关闭位置（检测得分 {score:.3f}）。")
        else:
            # 没有开屏广告模板，点击屏幕固定位置关闭
            if dry_run:
                ctx.log("演练：跳过关闭开屏广告。", level="warn")
                return True

            # 点击当前窗口中心偏上位置（假设广告点击此处可关闭）
            center_x = win_rect[0] + win_rect[2] // 2
            center_y = win_rect[1] + int(win_rect[3] * 0.35)
            ctx.mouse.click(center_x, center_y)
            ctx.log(f"点击当前窗口中心偏上位置关闭开屏广告（{center_x}, {center_y}）。")

        self._interruptible_sleep(ctx, 0.5)
        return True

    # ------------------------------------------------------------------
    def _interruptible_sleep(self, ctx, sec):
        """可被停止打断的等待"""
        interval = 0.1
        elapsed = 0.0
        while elapsed < sec and not ctx.should_stop():
            time.sleep(min(interval, sec - elapsed))
            elapsed += interval