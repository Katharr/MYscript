# -*- coding: utf-8 -*-
"""
配置读写。config.json 用 tasks.<任务名>.* 命名空间存放各任务自己的配置，
顶层只放跨任务共享项（窗口、输入后端、拟人化参数）。这样以后加新任务不会互相干扰。
"""

import os
import sys
import json
import copy
from pathlib import Path

# 数据根目录：config.json / templates / captures 都存这里。
#   - 源码运行：= 项目根（本文件向上三级 mhxy/core/config.py -> 项目根）。
#   - 打包成 exe（PyInstaller，sys.frozen=True）：= exe 所在目录。
#     绝不能用 __file__——onefile 模式下它在临时解压目录 %TEMP%\_MEIxxxx，
#     退出即清空，标定的配置和模板会全部丢失。改用 sys.executable 的所在目录，
#     于是配置/模板/截图都生成在 exe 同级，持久且集中在一个文件夹。
if getattr(sys, "frozen", False):
    DATA_ROOT = Path(sys.executable).resolve().parent
else:
    DATA_ROOT = Path(__file__).resolve().parents[2]

PROJECT_ROOT = DATA_ROOT          # 兼容别名：vision.py / gui/app.py 仍按此拼相对路径
CONFIG_PATH = DATA_ROOT / "config.json"
TEMPLATES_DIR = DATA_ROOT / "templates"
CAPTURES_DIR = DATA_ROOT / "captures"


DEFAULT_CONFIG = {
    # ---- 跨任务共享 ----
    "window_title": "梦幻西游",          # 游戏窗口标题关键字（模糊匹配）
    "input_backend": "sendinput",        # sendinput(底层+拟人化, 推荐) / pyautogui / pydirectinput
    "window_offset": [0, 0],             # 整体点击偏移修正 [dx, dy]
    "hotkey_toggle": "F5",               # 全局快捷键：开始/停止 秒装备（鼠标失控时随时叫停）
    "appearance": "dark",                # 界面外观：dark(夜间) / light(白天)，侧栏可切换

    # ---- 目标窗口选择（基础特性，跨任务共享）----
    #   所有任务都基于它确定「操作哪个号」：单开=选 1 个窗口，多开=选多个号轮流操作。
    #   窗口身份用「屏幕位置序号」(左→右，见 window.locate_all 排序)——三个号标题相同、HWND 重启会变，
    #   按摆放位置认号最稳。检测区(listing/scene)留空即「整窗检测」，无需框大区域。
    "targets": {
        "multi": False,            # False=单开(操作1个号) / True=多开(轮流操作多个号)
        "single_index": 0,         # 单开：选中窗口的序号(左→右,从0起)；越界自动回退0
        "multi_indices": [],       # 多开：选中的序号列表；空=检测到的全部
        "max_windows": 3,          # 多开最多同时操作几个号(0=不限)
        "switch_delay_sec": 0.15,  # 号与号之间切换的停顿(秒,带抖动)，别太机械
        "base_size": None          # 标定时记录的窗口尺寸[w,h]；「还原尺寸」按钮把被拉大的号拉回它。None=尚未标定
    },

    "humanize": {
        "speed": 2.0,             # 整体速度倍率：>1 更快(按比例缩短鼠标移动/按键的拟人化延迟)，<1 更慢更稳【标准抢货档】
        "snipe_speed": 5.0,       # 命中后「下单那一下」的额外速度倍率：只在抢的瞬间生效，越大越快越抢得到(也越不像人)【标准抢货档】
        "click_radius": 4,        # 落点随机半径(像素)
        "px_per_step": 12,        # 鼠标移动每步像素，越大步数越少→越快(但越不平滑)
        "interval_jitter": 0.4,   # 各种间隔的随机抖动比例
        "idle_chance": 0.0,       # 每轮“走神”停顿概率(抢货想快就调到 0)【标准抢货档：关闭走神】
        "idle_min_sec": 1.5,
        "idle_max_sec": 5.0
    },

    # ---- 游戏快捷键：脚本按「动作名」调用的语义映射（ctx.send_hotkey("open_bag")，键名列表）----
    #   ✅ 2026-06-22 已按用户提供的《时空》游戏内【快捷键预览截图】逐格核对/订正（图为权威来源）。
    #     ⚠ 之前那份文字情报有误：F8/O 实际是【未绑定】，活动其实是 Alt+C、聊天其实是 Alt+X。
    #     某项留空 [] 表示该入口没快捷键（个别任务会据此降级处理）。
    #     注：同一物理键在「功能页/战斗页」可能是不同功能，下面取的是【功能页(非战斗)】含义——
    #     脚本导航基本都在非战斗态发起。完整实测全表见顶层 game_hotkeys。
    "hotkeys": {
        "close_panel": ["esc"],          # 关闭面板/复位（Esc 游戏内未绑定，但通用关面板/退栈）
        "open_bag": ["alt", "e"],        # 包裹
        "open_task": ["alt", "y"],       # 任务（订正：旧种子 alt+q 错——时空 alt+q 是召唤灵/默认随机法术）
        "open_activity": ["alt", "c"],   # 活动（订正：实测 Alt+C；F8 其实未绑定，旧情报误把 F8 当活动）
        "open_character": ["alt", "w"],  # 人物
        "open_skill": ["alt", "s"],      # 技能（仅功能页；战斗页 alt+s 是默认法术）
        "open_summon": ["alt", "q"],     # 召唤灵（功能页）
        "open_team": ["alt", "t"],       # 队伍（功能页；战斗页 alt+t 是保护）
        "open_friend": ["alt", "f"],     # 好友
        "open_rank": ["alt", "r"],       # 排行（功能页；战斗页 alt+r 是召唤）
        "open_map": ["alt", "m"],        # 大地图
        "open_minimap": ["tab"],         # 小地图（功能页 Tab）
        "open_system": ["alt", "j"],     # 系统
        "open_mount": ["alt", "k"],      # 坐骑
        "open_guide": ["alt", "h"],      # 指引
        "open_shop": ["alt", "a"],       # 商城（功能页；战斗页 alt+a 是攻击）
        "open_welfare": ["alt", "d"],    # 福利（功能页；战斗页 alt+d 是防御）
        "open_strengthen": ["alt", "v"], # 强化
        "open_guild": ["alt", "b"],      # 帮派
        "open_home": ["alt", "n"],       # 家园
        "open_helper": ["alt", "z"],     # 助战（功能页；战斗页 alt+z 是特技）
        "open_chat": ["alt", "x"],       # 聊天（订正：实测 Alt+X；O 其实未绑定）
        "hide_ui": ["alt", "p"],         # 隐藏界面（功能页）
        "afk": ["alt", "g"],             # 挂机（功能页；战斗页 alt+g 是捕捉）
        "boss_key": ["alt", "l"],        # 老板键
        "pin_window": ["alt", "u"]       # 置顶客户端
    },

    # ---- 《时空》游戏内快捷键【实测全表】（用户 2026-06-22 截图核对，作为今后操控游戏的权威依据）----
    #   只读参考表：脚本实际调用走上面的语义 hotkeys，新增任务/战斗逻辑时来这查键位。
    #   只收录「已绑定」项（无绑定不列，故 F8/O/P/M(战斗) 等未绑定键不出现）。
    #   同一物理键在两页可能是不同功能，故分开记录；用 物理键 -> 功能 表示。
    "game_hotkeys": {
        "field": {                       # 功能页（非战斗）
            "f7": "横竖屏切换", "tab": "小地图",
            "alt+q": "召唤灵", "alt+w": "人物", "alt+e": "包裹", "alt+r": "排行",
            "alt+t": "队伍", "alt+y": "任务", "alt+u": "置顶客户端", "alt+p": "隐藏界面",
            "alt+a": "商城", "alt+s": "技能", "alt+d": "福利", "alt+f": "好友",
            "alt+g": "挂机", "alt+h": "指引", "alt+j": "系统", "alt+k": "坐骑", "alt+l": "老板键",
            "alt+z": "助战", "alt+x": "聊天", "alt+c": "活动", "alt+v": "强化",
            "alt+b": "帮派", "alt+n": "家园", "alt+m": "大地图"
        },
        "battle": {                      # 战斗页
            "f7": "横竖屏切换",
            "alt+q": "默认随机法术", "alt+w": "法术", "alt+e": "道具", "alt+r": "召唤",
            "alt+t": "保护", "alt+y": "任务", "alt+u": "置顶客户端",
            "alt+a": "攻击", "alt+s": "默认法术", "alt+d": "防御", "alt+f": "好友",
            "alt+g": "捕捉", "alt+h": "指引", "alt+j": "系统", "alt+k": "坐骑", "alt+l": "老板键",
            "alt+z": "特技", "alt+x": "聊天", "alt+c": "法宝", "alt+v": "强化",
            "alt+b": "帮派", "alt+n": "家园"
        }
    },

    # ---- 各任务独立配置 ----
    "tasks": {
        "sniper": {
            "dry_run": True,             # true=演练只识别不下单
            "loop": {
                "refresh_interval_sec": 0.2,    # 两轮「进货架查看」之间的间隔（带抖动），别太机械【标准抢货档】
                "shelf_load_wait_sec": 1.2,     # 等货架加载的「最长」等待（自适应：画面静止即提前结束，这是上限/超时）
                "shelf_load_min_sec": 0.15,     # 等货架加载的「最短」等待（再快也至少等这么久，给画面起步时间）【标准抢货档】
                "match_threshold": 0.85,
                "after_buy_cooldown_sec": 2.0
            },
            "regions": {                 # 由标定向导写入，相对游戏窗口左上角 [x,y,w,h]
                "listing": None,         # 货架/列表识别区域
                "category_button": None, # 左侧侧边栏的商品类别（如「奇珍异宝」）
                "product_entry": None,   # 右侧信息框里要进的那个商品条目
                "buy_button": None,
                "confirm_button": None
            },
            "watchlist": []              # [{name, template, max_price}]
        },

        # ---- 刷副本·宝图（一次性两阶段状态机；游戏自带自动战斗全托管，脚本只导航+监控+关键点击）----
        "treasure_map": {
            "dry_run": True,             # true=演练：只识别+打日志，不发快捷键/不点关键操作/不真用图
            "skip_collect": False,       # true=已有宝图：跳过阶段A(开活动领宝图任务)，直接开背包挖包裹里的藏宝图
            "loop": {
                "time_limit_min": 30,        # 时间上限（分钟）安全网，0=不限；主终止是「背包挖空」
                "match_threshold": 0.85,     # 标志模板匹配阈值
                "tick_interval_sec": 0.6,    # 每次「截图→判状态」的节拍（带抖动）
                "still_min_sec": 0.3,        # 帧差判静止：最短先等
                "still_wait_sec": 2.0,       # 帧差判静止：单次最长等/超时
                "still_diff": 8.0,           # 收集/挖宝判「人物静止」的整屏帧差阈值：低于此算静止。
                                             #   太小→待机动画/周围走动/特效让永远判不到静止→误超时；
                                             #   运行日志会实时打印真实帧差，照着设到静止<阈值<走动即可。
                "collect_idle_sec": 4.0,     # 收集阶段：人物连续静止这么久且非战斗非对话→判定收集完成
                "activity_timeout_sec": 30,  # 开活动→找到宝图入口的超时
                "dialog_timeout_sec": 30,    # 等 NPC 对话框出现的超时
                "collect_timeout_sec": 600,  # 整个收集阶段上限（自动战斗可能很久，给足）
                "dig_timeout_sec": 120,      # 单张挖宝（含战斗）超时
                "scroll_step": -3,           # 每次滚轮格数（负=向下翻）
                "scroll_max_tries": 8,       # 滑动找目标最多翻几屏，超了仍没找到→兜底
                "scroll_settle_sec": 0.35,   # 每滚一屏后等画面落定再重找的间隔（带抖动）。
                                             #   滚轮查找会在【同一个号】上一气呵成跑完（找到/翻完才轮转下个号），
                                             #   故这里要自等画面静止，别太小（否则截到滚动动画中途、漏识别）
                "activity_columns": 2,       # 活动列表每排几张卡片：找「参加」只在条目所属那一列内，
                                             #   避免两张卡片一排时扫到右邻卡片、点错右边的「参加」
                "max_stuck_recover": 3       # 连续卡死多少次就主动停
            },
            "regions": {                 # 相对游戏窗口 [x,y,w,h]，标定向导写入
                "scene": None,           # 主识别区（整窗或大半屏，所有 flag 都在这里找）
                "activity_list": None,   # 活动列表区域（滚轮在此找宝图任务条目）
                "bag_list": None         # 背包列表区域（滚轮在此找藏宝图）
            },
            "templates": {               # 状态标志模板路径（标定向导裁图写入，tm_ 前缀）
                "flag_treasure_entry": None, # 活动列表里「宝图任务」条目
                "flag_join": None,           # 「宝图任务」那一行右侧的「参加」按钮（按行匹配点它）
                "flag_tingting": None,       # 对话框「听听无妨」选项
                "flag_battle": None,         # 战斗界面独有标志（监控用，避免误判卡死）
                "flag_next_map": None,       # 挖完弹出的「下一张使用」按钮
                "treasure_item": None        # 背包里藏宝图道具图标（双击用图靠它定位）
            }
        },

        # ---- 运镖（一次性循环押镖状态机；游戏自带自动寻路+自动战斗，脚本只导航+监控+关键点击）----
        "escort": {
            "dry_run": True,             # true=演练：只识别+打日志，不发快捷键/不点关键操作
            "loop": {
                "time_limit_min": 30,        # 时间上限（分钟）安全网，0=不限；主终止是「对话框不再弹出」
                "match_threshold": 0.85,     # 标志模板匹配阈值
                "tick_interval_sec": 0.5,    # 多开轮转节拍：所有号各推进一步后的间隔（带抖动）
                "max_escorts": 3,            # 押镖次数：做满即停（与「对话框不再弹出」互为保险）
                "done_idle_sec": 6.0,        # 已是最后一趟、「运镖中」标志消失且无新对话框，持续这么久→判定全部结束
                "dialog_timeout_sec": 60,    # 点「参加」后等首个「押送普通镖银」对话框的超时
                "confirm_timeout_sec": 10,   # 点「押送普通镖银」后等「确认」按钮的超时（超时容错继续）
                "escort_timeout_sec": 600,   # 单趟运镖（含自动战斗）超时，超了按本批结束处理
                "still_min_sec": 0.3,        # 帧差判静止：最短先等
                "still_wait_sec": 2.0,       # 帧差判静止：单次最长等/超时
                "scroll_step": -3,           # 每次滚轮格数（负=向下翻）
                "scroll_max_tries": 8,       # 滑动找「运镖」最多翻几屏
                "scroll_settle_sec": 0.35,   # 每滚一屏后等画面落定再重找的间隔（带抖动）；
                                             #   滚轮查找在同一个号上一气呵成跑完，故需自等画面静止
                "activity_columns": 2,       # 活动列表每排几张卡片：找「参加」只在条目所属那一列内，
                                             #   避免两张卡片一排时扫到右邻卡片、点错右边的「参加」
                "max_stuck_recover": 3       # 连续卡死多少次就主动停
            },
            "regions": {                 # 相对游戏窗口 [x,y,w,h]，标定向导写入
                "scene": None,           # 主识别区（整窗或大半屏，所有 flag 都在这里找）
                "activity_list": None    # 活动列表区域（滚轮在此找运镖条目）
            },
            "templates": {               # 状态标志模板路径（标定向导裁图写入，tm_ 前缀）
                "escort_entry": None,    # 活动列表里「运镖」条目
                "escort_join": None,     # 「运镖」那一行右侧的「参加」按钮（按行匹配点它）
                "escort_silver": None,   # 对话框「押送普通镖银」按钮
                "escort_confirm": None,  # 点押送后再弹出的「确认」按钮
                "escort_ongoing": None,  # 运镖途中常驻的「运镖中」标志（在=还在运镖、不停）
                "escort_battle": None    # 战斗界面独有标志（监控用，避免误判结束）
            }
        },

        # ---- 秘境降妖（一次性状态机；游戏自带自动战斗，脚本只导航+监控+关键点击）----
        "secret_realm": {
            "dry_run": True,             # true=演练：只识别+打日志，不发快捷键/不点关键操作
            "loop": {
                "time_limit_min": 30,        # 时间上限（分钟）安全网，0=不限；每轮主终止是 失败/离开 或 时长判超时
                "match_threshold": 0.85,     # 标志模板匹配阈值
                "max_runs": 1,               # 每个号连跑几轮秘境（每轮=开活动→挑战→直到 失败/超时离开）
                "tick_interval_sec": 0.5,    # 多开轮转节拍：所有号各推进一步后的间隔（带抖动）
                "dialog_timeout_sec": 30,    # 点「参加」后等「秘境降妖」对话框出现的超时
                "dungeon_select_wait_sec": 6,  # 等「选择副本-进入」出现的短超时；没出现=本次无需选副本，跳过
                "step_timeout_sec": 20,      # 确定/继续挑战/挑战/离开 等每步按钮出现的超时（容错继续）
                "battle_timeout_sec": 1800,  # 单轮秘境「超时判定」时长：挂够这么久仍没结束就视为超时、点离开（按真实关卡时限调）
                "dungeon_enter_box": [0.0, 0.5, 0.55, 1.0],  # 「进入」按钮限定的左下角比例框 [x0,y0,x1,y1]（0~1）
                                             #   同款「进入」靠位置区分：只在 scene 这个左下角比例框里找
                "still_min_sec": 0.3,        # 帧差判静止：最短先等
                "still_wait_sec": 2.0,       # 帧差判静止：单次最长等/超时
                "scroll_step": -3,           # 每次滚轮格数（负=向下翻）
                "scroll_max_tries": 8,       # 滑动找卡片最多翻几屏
                "scroll_settle_sec": 0.35,   # 每滚一屏后等画面落定再重找的间隔（带抖动）；
                                             #   滚轮查找在同一个号上一气呵成跑完，故需自等画面静止
                "activity_columns": 2,       # 活动列表每排几张卡片：找「参加」只在条目所属那一列内，
                                             #   避免两张卡片一排时扫到右邻卡片、点错右边的「参加」
                "max_stuck_recover": 3       # 连续卡死多少次就主动停
            },
            "regions": {                 # 相对游戏窗口 [x,y,w,h]，标定向导写入
                "scene": None,           # 主识别区（整窗或大半屏，所有 flag 都在这里找）
                "activity_list": None    # 活动列表区域（滚轮在此找秘境降妖卡片）
            },
            "templates": {               # 状态标志模板路径（标定向导裁图写入，tm_ 前缀）
                "sr_entry": None,            # 活动列表里要点「参加」的那张卡片
                "sr_join": None,             # 那张卡片右侧的「参加」按钮（按行匹配点它）
                "sr_select": None,           # 对话框里「秘境降妖」选项
                "sr_dungeon_enter": None,    # 「选择副本」界面左下角的「进入」按钮（可选）
                "sr_confirm": None,          # 「确定」按钮
                "sr_continue": None,         # 「继续挑战」按钮
                "sr_challenge": None,        # 「挑战」按钮（点它开始自动战斗）
                "sr_enter_battle": None,     # 难度关卡的「进入战斗」按钮（监控期一出现就点）
                "sr_leave": None,            # 「离开」按钮（失败/超时/结束后点它退出秘境）
                "sr_fail": None,             # 「失败」标志（可选，判定该退出）
                "sr_battle": None            # 战斗界面独有标志（可选，仅日志诊断）
            }
        },

        # ---- 组队（全局共享资产；不是可运行任务，只存「组队」用到的标定+参数）----
        #   组队是跨窗口握手：队长建队→队员申请→队长接受→双方关窗。多个任务（刷副本/师门/帮派…）都会复用。
        #   故标定的模板/区域放这个共享命名空间 tasks.teaming，与具体任务解耦；
        #   每个任务自己的角色参数（谁当队长等）放各任务自己的块（如 tasks.dungeon）。
        "teaming": {
            "loop": {
                "match_threshold": 0.85,     # 标志模板匹配阈值
                "tick_interval_sec": 0.5,    # 多号轮转节拍：所有号各推进一步后的间隔（带抖动）
                "form_timeout_sec": 180,     # 组队整体超时（安全网）：到点仍没成形就降级结束
                "create_timeout_sec": 15,    # 队长等「创建队伍/申请」按钮出现的超时
                "accept_timeout_sec": 90,    # 队长等申请、点够人数的超时（每接受到一人会刷新计时）
                "find_leader_timeout_sec": 60,  # 队员在好友列表找队长ID的总超时
                "apply_timeout_sec": 15,     # 队员等「申请入队」按钮出现的超时
                "scroll_step": -3,           # 好友列表每次滚轮格数（负=向下翻）
                "scroll_max_tries": 10,      # 好友列表最多翻几屏找队长
                "scroll_settle_sec": 0.35,   # 每滚一屏后等画面落定再重找的间隔（带抖动）
                "arrow_band_w": 0,           # 找箭头的横向范围：从名字右边缘往右多宽；0=扫到该区域右缘(整行右半)
                "arrow_offset_x": 28,        # 箭头没匹配到时的兜底：点「名字右边缘 + 这么多像素」处
                "max_stuck_recover": 3       # 连续卡死多少次就放弃该号
            },
            "regions": {                 # 相对游戏窗口 [x,y,w,h]，标定向导写入
                "team_panel": None,      # 队伍面板区（创建队伍/申请标签页/同意 都在这片找）
                "friend_list": None      # 好友列表区（滚轮在此翻找队长ID）
            },
            "templates": {               # 组队全局模板（标定向导裁图写入，tm_ 前缀）
                "team_create": None,         # 「创建队伍」按钮
                "team_apply": None,          # 「申请」标签页（点它切到队员入队申请列表）
                "team_accept": None,         # 「同意」按钮（切到申请页后，队员申请那行右侧，见即点）
                "team_apply_join": None,     # 队员点队长右侧箭头后弹出的「申请入队」按钮
                "team_arrow": None,          # 好友列表里队长ID右侧的箭头按钮（在命中右侧小范围内找，可选）
                "leader_id": None            # 队长ID（队员据此在好友列表定位队长）
            }
        },

        # ---- 刷副本（第一版只做组队：1 大号带 N 小号自动组队，组队成功即结束）----
        #   组队的标定/参数走共享的 tasks.teaming；这里只放本任务的角色参数。
        "dungeon": {
            "dry_run": True,             # true=演练：只识别+打日志，不发快捷键/不点
            "captain_index": 0,          # 队长是「第几号」：所选多开窗口列表里的序号（0 起）；其余号自动当队员
            "loop": {
                "time_limit_min": 0          # 时间上限(分钟)安全网，0=不限
            }
        },

        # ---- 日常一条龙（只做串联：把下面 steps 里勾选的任务按顺序依次跑完）----
        #   完全沿用各子任务自身的流程/标定/演练实战/多开单开设置，本块只存「跑哪些、按什么顺序」。
        #   steps 是【有序】列表，每项 {task, enabled}；界面可勾选 + 上下调序。
        #   秒装备不在候选内（无限抢货、不会自己跑完，会卡死整条龙）。
        "daily": {
            "steps": [
                {"task": "treasure_map", "enabled": True},
                {"task": "escort", "enabled": True},
                {"task": "secret_realm", "enabled": True}
            ],
            "loop": {
                "time_limit_min": 0          # 整条龙的时间上限(分钟)安全网，0=不限；正常按各子任务自身条件跑完
            }
        }
    }
}


def _deep_merge(base, new):
    """把 new 合并进 base 的深拷贝并返回；用于补全旧配置缺失字段。"""
    out = copy.deepcopy(base)
    for k, v in (new or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_config():
    if not CONFIG_PATH.exists():
        return copy.deepcopy(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            user_cfg = json.load(f)
    except (json.JSONDecodeError, OSError):
        return copy.deepcopy(DEFAULT_CONFIG)
    return _deep_merge(DEFAULT_CONFIG, user_cfg)


def save_config(cfg):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def task_config(cfg, task_name):
    """取某任务的配置块，缺失则用默认补。"""
    default = DEFAULT_CONFIG["tasks"].get(task_name, {})
    return _deep_merge(default, cfg.get("tasks", {}).get(task_name, {}))


def set_task_config(cfg, task_name, task_cfg):
    cfg.setdefault("tasks", {})[task_name] = task_cfg
    return cfg
