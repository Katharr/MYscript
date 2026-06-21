# -*- coding: utf-8 -*-
"""
一键入口。双击「启动.bat」会调用它：
  1. 首次自动装依赖（这一步需要控制台看进度）；
  2. 依赖就绪后，用 pythonw 无窗口方式重启自己，原控制台随即关闭——
     于是只剩图形界面，没有任何黑色命令行窗口残留。
"""

import os
import sys
import ctypes
import subprocess

BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)
sys.path.insert(0, BASE)


def _is_admin():
    """当前进程是否拥有管理员权限。"""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _elevate():
    """以管理员权限重启自己。成功发起返回 True（本进程应立即退出）。"""
    try:
        params = '"{}"'.format(os.path.abspath(__file__))
        # ShellExecuteW + "runas" 触发 UAC；返回值 >32 表示成功发起。
        r = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, BASE, 1)
        return int(r) > 32
    except Exception:
        return False


def ensure_deps():
    try:
        import cv2, mss, numpy, pyautogui, pygetwindow, customtkinter, PIL  # noqa: F401
        return True
    except ImportError:
        print("首次运行，正在安装依赖，请稍候（只需这一次）……\n")
        ret = subprocess.call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
        if ret != 0:
            print("\n依赖安装失败：请确认已联网、Python 安装正常。")
            return False
        return True


def _has_console():
    """当前进程是否带控制台（python.exe 带、pythonw.exe 不带）。"""
    return os.path.basename(sys.executable).lower() == "python.exe"


def _relaunch_windowless():
    """用 pythonw 无窗口重启自己。成功返回 True。"""
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(pyw):
        return False
    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    try:
        subprocess.Popen([pyw, os.path.abspath(__file__)],
                         creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
                         close_fds=True)
        return True
    except Exception:
        return False


def main():
    # 关键修复：游戏客户端多以管理员权限运行。脚本若是普通权限，
    # 当游戏窗口（高完整性级别）处于前台时，Windows 会因 UIPI 静默丢弃我们的
    # SendInput —— 表现为「焦点在脚本上鼠标能动，一切到游戏就不动、点击无效」。
    # 故先把自己提权到管理员，与游戏同级，鼠标注入才能落到游戏窗口上。
    if not _is_admin():
        if _elevate():
            return  # 已发起管理员实例，本普通权限进程退出。
        # 提权被用户拒绝：仍以普通权限启动（界面可用），但切到游戏后多半点不动，提示之。
        print("⚠ 未获得管理员权限：切换到游戏窗口后鼠标可能无法移动/点击。\n"
              "  建议右键『启动.bat』→『以管理员身份运行』，或在 UAC 弹窗点『是』。\n")

    if not ensure_deps():
        input("\n按回车退出……")
        return

    # 依赖已就绪：若仍带控制台，则用 pythonw 重启以甩掉黑窗，本进程随即退出。
    if _has_console() and _relaunch_windowless():
        return

    from mhxy.core import window as win_mod
    win_mod.set_dpi_aware()
    from mhxy.gui.app import main as gui_main
    gui_main()


if __name__ == "__main__":
    main()
