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


def discover_candidates(limit=24):
    """返回可能的启动器路径，按常见安装位置和开始菜单快捷方式去重。

    自动发现只提供候选，GUI 不会静默改写用户当前选择。扫描故意保守，避免把所有磁盘递归
    搜一遍造成卡顿或误命中同名文件。
    """
    roots = []
    for env in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA", "APPDATA"):
        value = os.environ.get(env)
        if value:
            roots.append(Path(value))
    program_data = os.environ.get("ProgramData")
    if program_data:
        roots.append(Path(program_data) / "Microsoft" / "Windows" / "Start Menu" / "Programs")

    hints = ("梦幻", "mhxy", "xyq", "netease", "mygame", "launcher")
    exts = {".exe", ".lnk"}
    found = []
    seen = set()

    # 优先把当前已运行游戏窗口所属的程序路径作为候选。这里仅取路径，不操作进程。
    try:
        from . import window as win_mod
        for desktop_win in win_mod.gw.getAllWindows():
            if "梦幻西游" not in (desktop_win.title or ""):
                continue
            value = win_mod.proc_image_path(desktop_win._hWnd)
            if value and os.path.isfile(value) and value not in seen:
                seen.add(value)
                found.append(value)
    except Exception:
        pass
    if len(found) >= max(1, int(limit)):
        return found[:int(limit)]

    for root in roots:
        try:
            if not root.is_dir():
                continue
            # 控制深度：常见安装器/快捷方式目录足够，绝不全盘递归。
            for child in root.glob("*"):
                paths = [child]
                if child.is_dir():
                    try:
                        paths.extend(child.glob("*"))
                    except OSError:
                        pass
                for item in paths:
                    try:
                        if item.suffix.lower() not in exts:
                            continue
                        low = item.name.lower()
                        if not any(h in low for h in hints):
                            continue
                        value = str(item.resolve())
                    except OSError:
                        continue
                    if value not in seen:
                        seen.add(value)
                        found.append(value)
                        if len(found) >= max(1, int(limit)):
                            return found
        except OSError:
            continue
    return found
