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
import importlib.util

BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)
sys.path.insert(0, BASE)

# 运行时依赖的 import 名（注意 Pillow 的 import 名是 PIL）。
_DEP_MODULES = ["cv2", "mss", "numpy", "pyautogui", "pygetwindow", "customtkinter", "PIL"]

# 提权/去黑窗最多发起一次。一旦发起（无论成功还是被拒），就通过环境变量把这个标记
# 传给子进程，子进程据此不再尝试提权——否则被拒后重启的进程会反复弹 UAC、甚至死循环。
_ELEVATED_FLAG = "MHXY_ELEVATED"


def _is_admin():
    """当前进程是否拥有管理员权限。"""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _pythonw_path():
    """同目录下的 pythonw.exe（无控制台解释器）路径，不存在返回 None。"""
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return pyw if os.path.exists(pyw) else None


def _elevate(executable):
    """以管理员权限用 executable 重启自己。成功发起返回 True（本进程应立即退出）。
    在发起前先打提权标记进环境，子进程据此不再重复提权。"""
    os.environ[_ELEVATED_FLAG] = "1"
    try:
        params = '"{}"'.format(os.path.abspath(__file__))
        # ShellExecuteW + "runas" 触发 UAC；返回值 >32 表示成功发起。
        r = ctypes.windll.shell32.ShellExecuteW(None, "runas", executable, params, BASE, 1)
        return int(r) > 32
    except Exception:
        return False


def _deps_present():
    """廉价探测依赖是否齐全：只用 find_spec 定位模块，**不执行/不加载**它们
    （cv2/numpy/customtkinter 的真正开销在 import 时加载原生 DLL，这里完全避开）。
    全部能定位才返回 True。"""
    try:
        for m in _DEP_MODULES:
            if importlib.util.find_spec(m) is None:
                return False
        return True
    except (ImportError, ValueError):
        return False


def ensure_deps():
    """依赖缺失时联网安装。注意：探测用 _deps_present()（廉价），这里只负责装。"""
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


def _set_dpi_aware_early():
    """进程级 DPI 感知，必须在创建任何窗口（含启动页）之前调。
    直接走 ctypes，不导入 mhxy.core.window——那个模块顶部会 import cv2/numpy/mss，
    一导入就把重库加载提前到启动页之前，正好是我们要用后台线程盖住的那 3 秒。
    与 window.set_dpi_aware() 行为一致（PER_MONITOR_AWARE，失败回退到 system-DPI-aware）。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _make_splash():
    """创建并显示一个轻量启动页（纯 tkinter，导入极快），返回相关句柄。
    不在这里跑 mainloop——调用方掌控它的生命周期，好让它盖住「导入+建窗口+建页面」整段。

    启动页是本进程第一个 Tk 根。等主窗口（另一个 ctk.CTk 根）要接管时，调用方会把
    tkinter._default_root 置 None，使主窗口成为默认根（字体/控件都挂到主窗口，避免双根串台）。"""
    import tkinter as tk
    from tkinter import ttk

    splash = tk.Tk()
    splash.overrideredirect(True)          # 无边框，像启动页
    try:
        splash.attributes("-topmost", True)
    except Exception:
        pass
    W, H = 380, 132
    sw, sh = splash.winfo_screenwidth(), splash.winfo_screenheight()
    splash.geometry(f"{W}x{H}+{(sw - W) // 2}+{(sh - H) // 2}")

    BG, FG, DIM, ACC, TROUGH, EDGE = "#13161c", "#e7eaf0", "#8a93a3", "#4f8cff", "#20252f", "#2a313d"
    outer = tk.Frame(splash, bg=BG, highlightbackground=EDGE, highlightthickness=1)
    outer.pack(fill="both", expand=True)
    tk.Label(outer, text="梦幻 · 时空 助手", bg=BG, fg=FG,
             font=("Microsoft YaHei UI", 15, "bold")).pack(pady=(26, 4))
    status = tk.Label(outer, text="正在加载图像识别库，请稍候…", bg=BG, fg=DIM,
                      font=("Microsoft YaHei UI", 10))
    status.pack()
    try:
        style = ttk.Style(splash)
        style.theme_use("default")
        style.configure("Splash.Horizontal.TProgressbar", troughcolor=TROUGH,
                        background=ACC, bordercolor=BG, lightcolor=ACC, darkcolor=ACC)
        pb = ttk.Progressbar(outer, mode="indeterminate", length=300,
                             style="Splash.Horizontal.TProgressbar")
    except Exception:
        pb = ttk.Progressbar(outer, mode="indeterminate", length=300)
    pb.pack(pady=18)
    pb.start(12)
    splash.update()                        # 立刻画出来，别等到 mainloop
    return {"root": splash, "status": status, "pb": pb}


def _launch_gui():
    """设 DPI → 启动页全程盖住「导入重库 + 建主窗口 + 预建全部页面」→ 一次性亮出就绪窗口。
    任一步异常都回退到无启动页的直接启动，保证一定能起来。"""
    import threading

    _set_dpi_aware_early()

    splash = None
    try:
        splash = _make_splash()
    except Exception:
        splash = None

    # —— 阶段一：后台线程预热重库；启动页进度条在主线程流畅转动 ——
    if splash is not None:
        done = threading.Event()

        def _warm():
            try:
                import mhxy.gui.app  # noqa: F401  —— 触发 cv2/numpy/customtkinter 一次性加载
            except Exception:
                pass               # 预热失败无妨：主线程随后会再 import 并暴露真实错误
            finally:
                done.set()

        threading.Thread(target=_warm, daemon=True).start()

        def _poll():
            if done.is_set():
                splash["root"].quit()       # 退出 mainloop，回到下面继续（启动页先不销毁）
            else:
                splash["root"].after(40, _poll)

        splash["root"].after(40, _poll)
        splash["root"].mainloop()           # 转动进度条直到重库就绪
        # 关键：创建主窗口（另一个 ctk Tk 根）之前，必须先销毁启动页这个根——
        # 两个 Tk 根并存会让 customtkinter 直接崩（之前「正在准备界面」后闪退就是这个）。
        try:
            splash["root"].destroy()
        except Exception:
            pass
        try:
            import tkinter as _tk
            _tk._default_root = None        # 复位默认根，让主窗口接管
        except Exception:
            pass

    # —— 阶段二：建主窗口；用主窗口自带的同根遮罩盖住「建全部页面」的过程，建完再撤遮罩 ——
    from mhxy.gui.app import App
    app = App()
    if splash is not None:
        # 有过启动页时，亮界面前把其余页面也建好（同根遮罩盖住），杜绝「窗口出现后再逐页卡」。
        try:
            app.reveal_with_overlay()
        except Exception:
            pass
    # 无启动页（回退）时：不预建，让窗口尽快出现，其余页面交给空闲预建在后台补。
    try:
        app.lift()
        app.focus_force()
    except Exception:
        pass
    app.mainloop()


def main():
    # 打包成 exe（PyInstaller，sys.frozen=True）时，下面这些「源码运行」专用步骤全不适用，
    # 必须短路掉：
    #   - 提权：已用 PyInstaller --uac-admin 把「请求管理员」写进 exe 的 manifest，双击即弹
    #           UAC，无需运行时 ShellExecuteW 重启（那套会把 .py 路径当参数传错）。
    #   - ensure_deps：依赖已打进 exe，import 必成功，绝不能再 pip install。
    #   - _relaunch_windowless：exe 用 --windowed 打包本就无黑窗，也没有 pythonw 可用。
    # 故 frozen 时直接走到 GUI。
    frozen = getattr(sys, "frozen", False)

    if not frozen:
        no_elevate = os.environ.get(_ELEVATED_FLAG) == "1"
        deps_ok = _deps_present()

        # —— 依赖缺失：需要可见控制台跑 pip（仅首次）——
        if not deps_ok:
            # 装依赖也建议在管理员下做，但若已发起过提权就不再弹。
            if not _is_admin() and not no_elevate:
                if _elevate(sys.executable):
                    return  # 用带控制台的 python.exe 提权重启，去装依赖。
                print("⚠ 未获得管理员权限：切换到游戏窗口后鼠标可能无法移动/点击。\n"
                      "  建议右键『启动.bat』→『以管理员身份运行』，或在 UAC 弹窗点『是』。\n")
            if not ensure_deps():
                input("\n按回车退出……")
                return
            deps_ok = True

        # —— 依赖齐全：提权 + 去黑窗，尽量一步到位 ——
        # 关键修复：游戏客户端多以管理员权限运行。脚本若是普通权限，当游戏窗口（高完整性级别）
        # 处于前台时，Windows 会因 UIPI 静默丢弃我们的 SendInput——表现为「焦点在脚本上鼠标能动，
        # 一切到游戏就不动、点击无效」。故先把自己提权到管理员，与游戏同级，注入才落得到游戏窗口。
        if not _is_admin() and not no_elevate:
            # 直接提权到 pythonw.exe：一次 UAC 就同时拿到「管理员 + 无控制台」，
            # 省掉「先用 python 提权、再 pythonw 去窗」那一整个中间进程及其重复的重库加载。
            exe = _pythonw_path() or sys.executable
            if _elevate(exe):
                return  # 已发起管理员实例，本进程退出。
            # 提权被拒：以普通权限继续（界面可用，但切到游戏多半点不动），下面再去黑窗。
            print("⚠ 未获得管理员权限：切换到游戏窗口后鼠标可能无法移动/点击。\n"
                  "  建议右键『启动.bat』→『以管理员身份运行』，或在 UAC 弹窗点『是』。\n")

        # 仍带控制台（如提权被拒、或本就以管理员+console 启动）：用 pythonw 去黑窗。
        if _has_console() and _relaunch_windowless():
            return

    _launch_gui()


if __name__ == "__main__":
    main()
