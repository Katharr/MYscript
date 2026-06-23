# -*- coding: utf-8 -*-
"""
一键打包脚本。运行：python build.py
会调用 PyInstaller 按「梦幻秒装备.spec」打包，产出单文件 exe，
并整理出可直接分发的交付文件夹：发布\梦幻秒装备_v2.1\梦幻秒装备.exe

数据（config.json / templates / captures）首次运行 exe 时会自动生成在 exe 同级，
所以交付文件夹里一开始只有那个 exe —— 用户标定后数据就集中长在这个文件夹里。
"""

import os
import sys
import shutil
import subprocess

# 控制台多为 GBK，print 中文/符号可能抛 UnicodeEncodeError。强制 UTF-8 输出兜底。
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
SPEC = os.path.join(BASE, "梦幻秒装备.spec")
DIST = os.path.join(BASE, "dist")
BUILD = os.path.join(BASE, "build")
EXE_NAME = "梦幻秒装备.exe"
RELEASE_DIR = os.path.join(BASE, "发布", "梦幻秒装备_v2.1")


def run():
    if not os.path.exists(SPEC):
        print("找不到 梦幻秒装备.spec，无法打包。")
        return 1

    # 清掉上次产物，避免旧文件混入
    for d in (DIST, BUILD):
        if os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)

    print("开始打包（PyInstaller）……这一步可能要一两分钟。\n")
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", SPEC]
    ret = subprocess.call(cmd, cwd=BASE)
    if ret != 0:
        print("\n打包失败（PyInstaller 返回非 0）。请看上面的报错。")
        return ret

    exe_path = os.path.join(DIST, EXE_NAME)
    if not os.path.exists(exe_path):
        print("\n打包进程结束但没找到 exe：", exe_path)
        return 1

    # 整理交付文件夹：发布\梦幻秒装备_v2.1\梦幻秒装备.exe
    os.makedirs(RELEASE_DIR, exist_ok=True)
    dest = os.path.join(RELEASE_DIR, EXE_NAME)
    shutil.copy2(exe_path, dest)

    # 随包附带一份「标准配置」：速度参数=代码默认(抢货标准档)，标定坐标与监控清单留空，
    # dry_run=演练(安全)。放在 exe 同级，首次运行即采用，用户只需标定 + 加装备。
    _write_standard_config(os.path.join(RELEASE_DIR, "config.json"))

    size_mb = os.path.getsize(dest) / 1024 / 1024
    print("\n[OK] 打包完成！")
    print(f"   exe        : {exe_path}  ({size_mb:.1f} MB)")
    print(f"   交付文件夹 : {RELEASE_DIR}")
    print("\n把整个『梦幻秒装备_v2.1』文件夹发给用户即可。")
    print("已附带标准配置 config.json（抢货标准速度档；标定坐标/监控清单留空，首次运行需标定+加装备）。")
    print("首次双击 exe（弹 UAC 点是），同目录还会自动生成 templates / captures。")
    return 0


def _write_standard_config(path):
    """在发布目录写一份标准 config.json：内容 = 代码里的 DEFAULT_CONFIG
    （抢货标准速度档；regions 全空、watchlist 空、dry_run=演练）。
    单一来源 = DEFAULT_CONFIG，改默认即同步，不会漂移。
    若发布目录已有 config.json（可能含用户标定）则跳过，避免覆盖。"""
    import json
    if os.path.exists(path):
        print(f"  · 发布目录已有 config.json，跳过覆盖：{path}")
        return
    if BASE not in sys.path:
        sys.path.insert(0, BASE)
    try:
        from mhxy.core.config import DEFAULT_CONFIG
    except Exception as e:
        print(f"  ! 生成 config.json 失败（导入 DEFAULT_CONFIG 出错）：{e}")
        return
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=2)
        print(f"  [OK] 已写入标准配置：{path}")
    except OSError as e:
        print(f"  ! 写 config.json 失败：{e}")


if __name__ == "__main__":
    sys.exit(run())
