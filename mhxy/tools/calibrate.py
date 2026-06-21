# -*- coding: utf-8 -*-
"""
标定向导（命令行交互，用 OpenCV 框选）。由 GUI 以子进程方式调用：
    python -m mhxy.tools.calibrate
也可单独运行。当前面向 sniper 任务，标定其 regions 与 watchlist。

为什么用子进程：OpenCV 的 highgui 窗口与 GUI 的 tk 主循环混在一个进程里容易冲突，
分进程最省心。
"""

import sys
import time

import cv2

from ..core import window as win_mod
from ..core import vision
from ..core import config as cfg_mod

TASK = "sniper"


def _capture_window(cfg):
    win = win_mod.GameWindow(cfg.get("window_title", "梦幻西游"),
                             cfg.get("window_offset", [0, 0]))
    if not win.locate():
        print(f"  × 没找到标题含「{cfg.get('window_title')}」的窗口，请先打开游戏。")
        return None, None
    win.activate()
    time.sleep(0.2)
    return win_mod.grab(win.rect()), win


def _select_roi(img, title):
    print(f"  → 在弹窗里拖框选「{title}」，框好按【回车】，不选按【c】取消。")
    r = cv2.selectROI(f"selectROI - {title} (Enter=OK, c=Cancel)", img, showCrosshair=True)
    cv2.destroyAllWindows()
    cv2.waitKey(1)
    x, y, w, h = r
    if w == 0 or h == 0:
        print("  · 已取消。")
        return None
    return [int(x), int(y), int(w), int(h)]


def calibrate_regions(cfg, tc):
    img, win = _capture_window(cfg)
    if img is None:
        return
    items = [
        ("listing", "物品列表区域（框大一点，把整列都包住）"),
        ("refresh_button", "刷新按钮"),
        ("buy_button", "购买按钮"),
        ("confirm_button", "确认购买按钮（没有就按 c 跳过）"),
    ]
    for key, desc in items:
        roi = _select_roi(img, desc)
        if roi is not None:
            tc["regions"][key] = roi
            print(f"  √ 已记录 {key} = {roi}")
    print("区域标定完成。\n")


def add_watch_item(cfg, tc):
    img, win = _capture_window(cfg)
    if img is None:
        return
    roi = _select_roi(img, "你想抢的装备（连图标带名字一起框最准，别框价格）")
    if roi is None:
        return
    x, y, w, h = roi
    crop = img[y:y + h, x:x + w]
    name = input("  起个名字(英文/拼音，无空格，如 wujibie_xianglian)：").strip() or f"item_{int(time.time())}"
    rel = f"templates/{name}.png"
    vision.save_image(rel, crop)
    price = input("  最高可接受价(回车=不限价，仅占位不参与判断)：").strip()
    max_price = None
    if price:
        try:
            max_price = int(price.replace(",", ""))
        except ValueError:
            print("  · 价格没看懂，按不限价。")
    tc["watchlist"].append({"name": name, "template": rel, "max_price": max_price})
    print(f"  √ 已加入：{name}（{rel}）\n")


def show(tc):
    print("\n----- 当前 sniper 配置 -----")
    print(f"演练模式 : {'开(安全)' if tc.get('dry_run', True) else '关(会真买)'}")
    print("区域     :")
    for k, v in tc["regions"].items():
        print(f"   {k:16s}: {v if v else '× 未标定'}")
    print(f"监控清单 : {len(tc['watchlist'])} 件")
    for it in tc["watchlist"]:
        p = "不限价" if it.get("max_price") is None else f"≤{it['max_price']}"
        print(f"   - {it['name']} ({p})")
    print("---------------------------\n")


def main():
    win_mod.set_dpi_aware()
    cfg = cfg_mod.load_config()
    tc = cfg_mod.task_config(cfg, TASK)

    while True:
        print("===== 秒装备 · 标定向导 =====")
        print(" 1. 标定按钮与区域（列表/刷新/购买/确认）")
        print(" 2. 添加一件要抢的装备")
        print(" 3. 查看当前配置")
        print(" 4. 清空监控清单")
        print(" 0. 保存并退出")
        c = input("请选择：").strip()
        if c == "1":
            calibrate_regions(cfg, tc)
        elif c == "2":
            add_watch_item(cfg, tc)
        elif c == "3":
            show(tc)
        elif c == "4":
            tc["watchlist"] = []
            print("已清空。\n")
        elif c == "0":
            cfg_mod.set_task_config(cfg, TASK, tc)
            cfg_mod.save_config(cfg)
            print("已保存。可关闭本窗口，回到主界面开始秒装备。")
            break
        else:
            print("请输入菜单数字。\n")


if __name__ == "__main__":
    main()
