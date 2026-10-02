# -*- coding: utf-8 -*-
"""秒装备备用命令行标定；GUI 使用自己的标定对话框。"""
import time
import cv2

from ..core import window as win_mod
from ..core import vision
from ..core import config as cfg_mod
from ..tasks.sniper import SniperTask

TASK = "sniper"


def _capture_window(cfg):
    wins = win_mod.resolve_targets(cfg.get("window_title", "梦幻西游"),
                                   cfg.get("window_offset", [0, 0]), cfg.get("targets", {}))
    if not wins or not wins[0].activate():
        print("  × 未找到选中窗口或无法切到前台。")
        return None, None
    win = wins[0]
    time.sleep(0.2)
    return win_mod.grab(win.rect()), win


def _select_roi(img, title):
    print(f"  → 框选「{title}」，回车确认，c 取消。")
    x, y, w, h = cv2.selectROI(f"selectROI - {title}", img, showCrosshair=True)
    cv2.destroyAllWindows()
    cv2.waitKey(1)
    if not w or not h:
        return None
    return [int(x), int(y), int(w), int(h)]


def calibrate_regions(cfg, tc):
    for item in SniperTask.CALIBRATION['regions']:
        key, name, desc = item[:3]
        input(f"请准备好「{name}」所在画面，回车截图：")
        img, _ = _capture_window(cfg)
        if img is None:
            return
        roi = _select_roi(img, desc)
        if roi is not None:
            tc['regions'][key] = roi


def calibrate_template(cfg, tc, key):
    item = next(it for it in SniperTask.CALIBRATION['templates'] if it[0] == key)
    input(f"请准备好「{item[1]}」所在画面，回车截图：")
    img, _ = _capture_window(cfg)
    if img is None:
        return
    roi = _select_roi(img, item[2])
    if roi is None:
        return
    x, y, w, h = roi
    rel = f"templates/tm_{key}.png"
    if vision.save_image(rel, img[y:y+h, x:x+w]):
        tc.setdefault('templates', {})[key] = rel
        print("  √ 模板已记录。")


def show(tc):
    print(f"演练：{tc.get('dry_run', True)}；每窗口购买数量：{tc.get('target_count', 1)}")
    print('区域：', tc['regions'])
    print('模板：', tc['templates'])


def main():
    win_mod.set_dpi_aware()
    cfg = cfg_mod.load_config()
    tc = cfg_mod.task_config(cfg, TASK)
    while True:
        print("===== 秒装备 · 标定 =====\n1. 标定区域与购买按钮\n2. 商品模板\n3. 成功提示模板\n4. 查看配置\n0. 保存退出")
        choice = input('请选择：').strip()
        if choice == '1':
            calibrate_regions(cfg, tc)
        elif choice == '2':
            calibrate_template(cfg, tc, 'sniper_product')
        elif choice == '3':
            calibrate_template(cfg, tc, 'sniper_success')
        elif choice == '4':
            show(tc)
        elif choice == '0':
            cfg_mod.set_task_config(cfg, TASK, tc)
            cfg_mod.save_config(cfg)
            break


if __name__ == '__main__':
    main()
