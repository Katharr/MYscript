# -*- coding: utf-8 -*-
"""Windows 启动器发现与启动。

这里只处理本机路径和 ShellExecuteW，不理解任何游戏 UI，也不尝试规避 UAC。由已提升
权限的本工具启动子进程时，Windows 通常会让子进程继承同一完整性级别；如果启动器仍显示
自己的 UAC/SmartScreen，调用方只能等待用户确认。
"""

import ctypes
import ctypes.wintypes
import os
import subprocess
from pathlib import Path


_SHELL = ctypes.windll.shell32
_SHELL.ShellExecuteW.restype = ctypes.wintypes.HINSTANCE


def normalize_path(path):
    """清理用户输入的可执行文件路径；无效时返回空字符串。"""
    raw = os.path.expandvars(os.path.expanduser(str(path or "").strip().strip('"')))
    if not raw:
        return ""
    try:
        item = Path(raw)
        if not item.is_file() or item.suffix.lower() not in {".exe", ".lnk"}:
            return ""
        return str(item.resolve())
    except OSError:
        return ""


def launch(path, args=None, cwd=None):
    """通过 ShellExecuteW 启动本机文件，成功返回 ``(True, "")``。

    ShellExecute 支持 exe 与 lnk，返回值 <= 32 时是 Windows 错误码。调用此函数不会自动
    点击或关闭 UAC；安全桌面出现时，状态机负责停留在等待状态。
    """
    path = normalize_path(path)
    if not path:
        return False, "启动器路径不存在或不是文件"
    params = subprocess.list2cmdline([str(x) for x in (args or []) if str(x).strip()])
    try:
        result = int(_SHELL.ShellExecuteW(None, "open", path, params or None,
                                          cwd or os.path.dirname(path), 1))
    except Exception as exc:
        return False, "启动器启动失败：%s" % exc
    if result <= 32:
        return False, "Windows 无法启动该文件（ShellExecute 错误 %d）" % result
    return True, ""


LAUNCHER_EXE = "MyPCLauncher_x64r.exe"


def _append_launcher(found, seen, candidate, limit):
    """仅接受官方启动器同名 exe；返回是否已达到条数上限。"""
    try:
        item = Path(candidate)
        if item.name.lower() != LAUNCHER_EXE.lower() or not item.is_file():
            return False
        value = str(item.resolve())
    except OSError:
        return False
    if value not in seen:
        seen.add(value)
        found.append(value)
    return len(found) >= limit


def _running_game_install_roots():
    """从当前可见游戏外壳反推安装根的候选祖先目录。"""
    roots = []
    try:
        from . import window as win_mod
        for desktop_win in win_mod.gw.getAllWindows():
            if "梦幻西游" not in (desktop_win.title or ""):
                continue
            image = win_mod.proc_image_path(desktop_win._hWnd)
            if not image:
                continue
            current = Path(image).parent
            for _ in range(7):
                roots.append(current)
                if current.parent == current:
                    break
                current = current.parent
    except Exception:
        pass
    return roots


def discover_candidates(limit=8):
    """自动检测官方 ``MyPCLauncher_x64r.exe``，绝不返回其它游戏进程。

    先从已运行游戏窗口反推安装目录，再检查常见安装根及其两层子目录。找不到时让用户使用
    “浏览...”选择，不进行全盘递归或模糊文件名猜测。
    """
    limit = max(1, int(limit))
    found, seen = [], set()

    # 正在运行游戏时，这一条最准确：外壳路径向上逐级检查同目录的官方启动器。
    roots = _running_game_install_roots()
    for env in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA", "APPDATA"):
        value = os.environ.get(env)
        if value:
            roots.append(Path(value))

    for root in roots:
        if _append_launcher(found, seen, root / LAUNCHER_EXE, limit):
            return found
        try:
            for child in root.glob("*"):
                if not child.is_dir():
                    continue
                if _append_launcher(found, seen, child / LAUNCHER_EXE, limit):
                    return found
                for grandchild in child.glob("*"):
                    if grandchild.is_dir() and _append_launcher(found, seen, grandchild / LAUNCHER_EXE, limit):
                        return found
        except OSError:
            continue
    return found
