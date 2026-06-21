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
