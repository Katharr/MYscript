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

    # ---- 游戏快捷键（脚本导航/复位用，键名列表，如 ["alt","e"]）----
    #   ⚠ 这些是《梦幻西游》经典端游的键位种子值，时空(手游PC端)可能不同！
    #     用户须进游戏「系统设置-快捷键」核对后改这里。某项留空 [] 表示该入口没有快捷键，
    #     任务会降级为点击标定坐标（如 open_activity 空时点 regions.activity_button）。
    "hotkeys": {
        "close_panel": ["esc"],          # 关闭面板/复位
        "open_bag": ["alt", "e"],        # 打开背包（经典端游 Alt+E，待核对）
        "open_task": ["alt", "q"],       # 打开任务栏
        "open_activity": []              # 打开「活动」界面：键位未知，留空→点 activity_button 坐标
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
                "collect_idle_sec": 4.0,     # 收集阶段：人物连续静止这么久且非战斗非对话→判定收集完成
                "activity_timeout_sec": 30,  # 开活动→找到宝图入口的超时
                "dialog_timeout_sec": 30,    # 等 NPC 对话框出现的超时
                "collect_timeout_sec": 600,  # 整个收集阶段上限（自动战斗可能很久，给足）
                "dig_timeout_sec": 120,      # 单张挖宝（含战斗）超时
                "scroll_step": -3,           # 每次滚轮格数（负=向下翻）
                "scroll_max_tries": 8,       # 滑动找目标最多翻几屏，超了仍没找到→兜底
                "max_stuck_recover": 3       # 连续卡死多少次就主动停
            },
            "regions": {                 # 相对游戏窗口 [x,y,w,h]，标定向导写入
                "scene": None,           # 主识别区（整窗或大半屏，所有 flag 都在这里找）
                "activity_button": None, # 「活动」入口按钮（open_activity 无快捷键时点它）
                "activity_list": None,   # 活动列表区域（滚轮在此找宝图任务条目）
                "bag_list": None,        # 背包列表区域（滚轮在此找藏宝图）
                "blank_spot": None       # 安全空白处（卡死恢复时点这里关弹窗）
            },
            "templates": {               # 状态标志模板路径（标定向导裁图写入，tm_ 前缀）
                "flag_treasure_entry": None, # 活动列表里「宝图任务」条目
                "flag_tingting": None,       # 对话框「听听无妨」选项
                "flag_dialog": None,         # 对话框出现的标志（可选）
                "flag_battle": None,         # 战斗界面独有标志（监控用，避免误判卡死）
                "flag_next_map": None,       # 挖完弹出的「下一张使用」按钮
                "treasure_item": None        # 背包里藏宝图道具图标（双击用图靠它定位）
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
