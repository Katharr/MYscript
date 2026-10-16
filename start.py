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

BASE = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
os.chdir(BASE)
sys.path.insert(0, BASE)

# 运行时依赖的 import 名（注意 Pillow 的 import 名是 PIL）。
_DEP_MODULES = ["cv2", "mss", "numpy", "pyautogui", "pygetwindow", "PySide6", "PIL",
                "rapidocr_onnxruntime"]

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
        params = subprocess.list2cmdline([os.path.abspath(__file__), *sys.argv[1:]])
        # ShellExecuteW + "runas" 触发 UAC；返回值 >32 表示成功发起。
        r = ctypes.windll.shell32.ShellExecuteW(None, "runas", executable, params, BASE, 1)
        return int(r) > 32
    except Exception:
        return False


def _missing_dep_modules():
    """返回当前 Python 环境中找不到的依赖 import 名，探测过程不加载原生库。"""
    try:
        return [m for m in _DEP_MODULES if importlib.util.find_spec(m) is None]
    except (ImportError, ValueError):
        # Python 环境本身异常时按依赖不完整处理，交给 pip 给出明确错误。
        return list(_DEP_MODULES)


def _deps_present():
    """廉价探测依赖是否齐全，不执行/加载任何实际依赖模块。"""
    return not _missing_dep_modules()


def ensure_deps():
    """征得用户同意后，安装当前 Python 环境缺失的 requirements 依赖。"""
    missing = _missing_dep_modules()
    print("检测到以下依赖未安装：{}".format(", ".join(missing) if missing else "无法确认"))
    while True:
        answer = input("是否联网安装/更新所需依赖？[y/n]: ").strip().lower()
        if answer in ("y", "yes"):
            break
        if answer in ("n", "no"):
            print("已取消安装，程序未启动。")
            return False
        print("请输入 y 或 n。")

    print("\n正在安装所需依赖，请稍候……\n")
    ret = subprocess.call([
        sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-r", "requirements.txt"
    ])
    if ret != 0:
        print("\n依赖安装失败：请确认网络连接、pip 与 Python 环境正常。")
        return False
    if not _deps_present():
        print("\n依赖安装完成，但仍有模块无法加载：{}".format(", ".join(_missing_dep_modules())))
        return False
    print("\n依赖已就绪。")
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
        subprocess.Popen([pyw, os.path.abspath(__file__), *sys.argv[1:]],
                         creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
                         close_fds=True)
        return True
    except Exception:
        return False


def _set_dpi_aware_early():
    """在 Qt 和截图库加载前设置进程 DPI 感知，保持物理坐标规则。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _report_fatal(stage, exc_info=None):
    """把启动期未捕获异常写进 startup_error.log 并弹窗提示。

    pythonw.exe **没有控制台**：启动过程中任何异常都会让进程静默消失（用户只看到
    「闪一下就没了、毫无反应」），根本拿不到 traceback，排障只能靠猜。所以启动阶段
    必须自己把异常落盘 + 弹窗，否则这类问题无法定位。
    """
    import traceback
    tb = "".join(traceback.format_exception(*exc_info)) if exc_info else traceback.format_exc()
    try:
        with open(os.path.join(BASE, "startup_error.log"), "w", encoding="utf-8") as f:
            f.write("阶段：{}\n\n{}".format(stage, tb))
    except Exception:
        pass
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            None,
            "启动失败（阶段：{}）。\n\n详细错误已写入程序目录下的 startup_error.log：\n{}\n\n{}".format(
                stage, os.path.join(BASE, "startup_error.log"), tb[-700:]),
            "梦幻小助手 — 启动失败", 0x10)
    except Exception:
        pass


def _launch_gui():
    """只启动 Qt；导入、构窗和回调失败均落盘，pythonw 下也可定位。"""
    _set_dpi_aware_early()
    previous_hook = sys.excepthook

    def report_callback(exc_type, exc, tb):
        _report_fatal("Qt 回调", (exc_type, exc, tb))
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            app.exit(1)

    try:
        sys.excepthook = report_callback
        from mhxy.qtui.main import main as qt_main
        return qt_main()
    except Exception:
        _report_fatal("启动 Qt 主窗口")
        return 1
    finally:
        sys.excepthook = previous_hook


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
                print("未获得管理员权限：切换到游戏窗口后鼠标可能无法移动/点击。\n"
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
            print("未获得管理员权限：切换到游戏窗口后鼠标可能无法移动/点击。\n"
                  "  建议右键『启动.bat』→『以管理员身份运行』，或在 UAC 弹窗点『是』。\n")

        # 仍带控制台（如提权被拒、或本就以管理员+console 启动）：用 pythonw 去黑窗。
        if _has_console() and _relaunch_windowless():
            return

    return _launch_gui()


if __name__ == "__main__":
    raise SystemExit(main())
