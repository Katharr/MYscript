# -*- coding: utf-8 -*-
"""轻量的游戏窗口证据与规则层。

设计原则：
1. 不依赖重库：不 import cv2 / numpy / mss / mhxy.core.window；默认枚举器只在调用时
   lazy import pygetwindow。
2. 依赖方向单向：本模块只提供底层证据与规则，window.py、accounts.py 在其上层。
3. 可注入、可测：窗口枚举器和进程快照函数均可传入，测试不需要访问真实桌面。
4. 只给证据，不做动作：本模块不会绑定、激活或点击任何窗口。
"""

import ctypes
import ctypes.wintypes
import os
import re
from dataclasses import dataclass


DEFAULT_GAME_PROCESS_SPEC = "MyTabCtrl_x64r.exe,MyGame_x64r.exe"
_LAUNCHER_PROCESS = "mypclauncher_x64r.exe"
_SPLIT_RE = re.compile(r"[,，;；|\s]+")
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_TH32CS_SNAPPROCESS = 0x00000002
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


@dataclass(frozen=True)
class WinInfo:
    """一条顶层窗口证据。"""

    hwnd: int
    title: str
    left: int
    top: int
    width: int
    height: int
    minimized: bool
    exe: str
    pid: int
    native: object = None


@dataclass(frozen=True)
class ProbeResult:
    """游戏窗口与相关进程的快照证据。"""

    visible: tuple[WinInfo, ...]
    minimized: tuple[WinInfo, ...]
    running: bool
    launcher_running: bool

    @property
    def windows(self):
        """所有命中游戏规则的窗口，包含最小化窗口。"""
        return self.visible + self.minimized

    @property
    def count(self):
        """所有命中游戏规则的窗口数量，包含最小化窗口。"""
        return len(self.windows)

    @property
    def any(self):
        """是否存在至少一个命中游戏规则的窗口。"""
        return bool(self.windows)


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", ctypes.wintypes.DWORD),
                ("cntUsage", ctypes.wintypes.DWORD),
                ("th32ProcessID", ctypes.wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                ("th32ModuleID", ctypes.wintypes.DWORD),
                ("cntThreads", ctypes.wintypes.DWORD),
                ("th32ParentProcessID", ctypes.wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", ctypes.wintypes.DWORD),
                ("szExeFile", ctypes.wintypes.WCHAR * 260)]


_user32 = ctypes.windll.user32
_user32.GetWindowThreadProcessId.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.LPDWORD]
_user32.GetWindowThreadProcessId.restype = ctypes.wintypes.DWORD

_kernel32 = ctypes.windll.kernel32
_kernel32.OpenProcess.argtypes = [ctypes.wintypes.DWORD, ctypes.wintypes.BOOL, ctypes.wintypes.DWORD]
_kernel32.OpenProcess.restype = ctypes.wintypes.HANDLE
_kernel32.QueryFullProcessImageNameW.argtypes = [
    ctypes.wintypes.HANDLE, ctypes.wintypes.DWORD,
    ctypes.wintypes.LPWSTR, ctypes.POINTER(ctypes.wintypes.DWORD)]
_kernel32.QueryFullProcessImageNameW.restype = ctypes.wintypes.BOOL
_kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
_kernel32.CloseHandle.restype = ctypes.wintypes.BOOL
_kernel32.CreateToolhelp32Snapshot.argtypes = [ctypes.wintypes.DWORD, ctypes.wintypes.DWORD]
_kernel32.CreateToolhelp32Snapshot.restype = ctypes.wintypes.HANDLE
_kernel32.Process32FirstW.argtypes = [ctypes.wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
_kernel32.Process32FirstW.restype = ctypes.wintypes.BOOL
_kernel32.Process32NextW.argtypes = [ctypes.wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
_kernel32.Process32NextW.restype = ctypes.wintypes.BOOL


def parse_process_names(spec):
    """解析进程配置为小写 exe basename 集合；空值返回 None。"""
    if spec is None:
        return None
    if isinstance(spec, (list, tuple, set, frozenset)):
        items = [str(item) for item in spec]
    else:
        items = _SPLIT_RE.split(str(spec))
    names = {item.strip().lower() for item in items if item.strip()}
    return frozenset(names) if names else None


def proc_image_path(hwnd):
    """返回 hwnd 所属进程的 exe 完整路径；取不到返回空字符串。

    仅使用 PROCESS_QUERY_LIMITED_INFORMATION，因此不需要提升权限也可查询高完整性进程。
    """
    try:
        pid = ctypes.wintypes.DWORD()
        _user32.GetWindowThreadProcessId(int(hwnd), ctypes.byref(pid))
        if not pid.value:
            return ""
        return proc_path_by_pid(pid.value)
    except Exception:
        return ""


def proc_path_by_pid(pid):
    """返回 PID 所属进程的 exe 完整路径；取不到返回空字符串。"""
    try:
        if not pid:
            return ""
        handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = ctypes.wintypes.DWORD(1024)
            if not _kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return ""
            return buf.value or ""
        finally:
            _kernel32.CloseHandle(handle)
    except Exception:
        return ""


def _proc_basename(hwnd):
    """返回 hwnd 所属进程的小写 exe basename；取不到返回空字符串。"""
    return os.path.basename(proc_image_path(hwnd)).lower()


def _value(win, name, alternate=None, default=None):
    try:
        return getattr(win, name)
    except Exception:
        if alternate is not None:
            try:
                return getattr(win, alternate)
            except Exception:
                pass
        return default


def _window_hwnd(win):
    return int(_value(win, "hwnd", "_hWnd", 0) or 0)


def _window_minimized(win):
    return bool(_value(win, "minimized", "isMinimized", False))


def _window_exe(win, hwnd):
    value = _value(win, "exe", default="")
    if value:
        return os.path.basename(str(value)).lower()
    return _proc_basename(hwnd)


def is_game_window(win, title_substr, process_names, allow_minimized=False):
    """按旧规则判断窗口：标题、尺寸和可选的进程白名单。

    标题会和终端、编辑器等窗口撞名，进程 exe 名才稳定；游戏窗口类名是随机串，无法白名单。
    最小化且 allow_minimized=True 时放宽尺寸门槛，避免系统报告的小矩形误滤真实窗口。
    """
    try:
        if title_substr not in (_value(win, "title", default="") or ""):
            return False
        minimized = _window_minimized(win)
        if not (allow_minimized and minimized):
            if int(_value(win, "width", default=0) or 0) <= 100:
                return False
            if int(_value(win, "height", default=0) or 0) <= 100:
                return False
        hwnd = _window_hwnd(win)
    except Exception:
        return False
    if process_names and _window_exe(win, hwnd) not in process_names:
        return False
    return True


def position_sort_key(win):
    """按行、再从左到右排列窗口的稳定排序键。"""
    return (int(_value(win, "top", default=0) or 0) // 120,
            int(_value(win, "left", default=0) or 0))


def list_processes():
    """返回系统进程快照 ``[{pid, ppid, exe(小写)}]``。"""
    result = []
    try:
        snapshot = _kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
        if not snapshot or snapshot == _INVALID_HANDLE_VALUE:
            return result
        try:
            entry = _PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
            ok = _kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
            while ok:
                result.append({"pid": int(entry.th32ProcessID),
                               "ppid": int(entry.th32ParentProcessID),
                               "exe": (entry.szExeFile or "").lower()})
                ok = _kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            _kernel32.CloseHandle(snapshot)
    except Exception:
        pass
    return result


def _default_enumerator():
    import pygetwindow as gw
    return gw.getAllWindows()


def _window_pid(win, hwnd):
    explicit = _value(win, "pid", default=0)
    if explicit:
        try:
            return int(explicit)
        except (TypeError, ValueError):
            pass
    try:
        pid = ctypes.wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value)
    except Exception:
        return 0


def _to_win_info(native, known_processes):
    hwnd = _window_hwnd(native)
    pid = _window_pid(native, hwnd)
    exe = _window_exe(native, hwnd)
    if not exe and pid:
        exe = known_processes.get(pid, "")
    return WinInfo(
        hwnd=hwnd,
        title=str(_value(native, "title", default="") or ""),
        left=int(_value(native, "left", default=0) or 0),
        top=int(_value(native, "top", default=0) or 0),
        width=int(_value(native, "width", default=0) or 0),
        height=int(_value(native, "height", default=0) or 0),
        minimized=_window_minimized(native),
        exe=exe,
        pid=pid,
        native=native,
    )


def list_game_windows(title_substr, process_spec, *, include_minimized=False,
                      enumerator=None, process_lister=None):
    """收集匹配游戏规则的窗口及进程证据，不执行任何窗口操作。

    枚举器或进程快照任一失败都会降级为对应的空证据，另一类证据仍会照常返回。
    """
    process_names = parse_process_names(process_spec)
    process_lister = process_lister or list_processes
    try:
        processes = list(process_lister() or [])
    except Exception:
        processes = []

    process_by_pid = {}
    process_exes = set()
    for process in processes:
        try:
            pid = int(process.get("pid") or 0)
            exe = os.path.basename(str(process.get("exe") or "")).lower()
            if pid and exe:
                process_by_pid[pid] = exe
            if exe:
                process_exes.add(exe)
        except Exception:
            continue

    enumerator = enumerator or _default_enumerator
    try:
        natives = list(enumerator() or [])
    except Exception:
        natives = []

    visible = []
    minimized = []
    for native in natives:
        try:
            info = _to_win_info(native, process_by_pid)
            if not is_game_window(info, title_substr, process_names,
                                  allow_minimized=include_minimized):
                continue
            if info.minimized:
                if include_minimized:
                    minimized.append(info)
            else:
                visible.append(info)
        except Exception:
            continue

    visible.sort(key=position_sort_key)
    minimized.sort(key=position_sort_key)
    return ProbeResult(
        visible=tuple(visible),
        minimized=tuple(minimized),
        running=bool(process_names and process_exes.intersection(process_names)),
        launcher_running=_LAUNCHER_PROCESS in process_exes,
    )
