# -*- coding: utf-8 -*-
"""
角色名识别（纯文件读取，零 OCR）。把「号1/号2/号3」换成真实角色名，如「王昭君_（83）」。

## 为什么不在窗口 API 里做（实测全灭，别再回头试）
2026-09 客户端，本机实测「窗口 → 角色名」在 Windows 侧【一条都走不通】：
  · GetWindowText：顶层标题恒为「梦幻西游：时空」，角色名是画在标签页上的像素；
  · 子窗口枚举：shell / game 整个进程树里没有任何带文字的窗口；
  · UI Automation / MSAA：连提权后都是空树（标签是自绘控件，没实现无障碍 provider）；
  · 进程命令行：`MyGame_x64r.exe __MYTABCTRL_TAG___0_0_0_38_653_490_0.637695_3292_1_2_...`
    只有外壳 PID 和窗口几何，没有账号/角色；
  · 枚举进程句柄：客户端只把日志文件开着，LocalData\\<role_id>\\ 下的文件全是空闲的，绑不上。
结论：显示层没有可读路径。别再从这边想办法（尤其别上 OCR——用户明确不要）。

## 真正能读的：客户端自己写的两个纯文本文件
登录时客户端会把角色信息落盘（都是 UTF-8、无需 OCR、加锁打开也读得到）：
  1) <安装目录>\\LocalData\\last_server_info —— JSON，`cName` 就是角色名，
     另有 role_id / hostname(服务器名) / hostid(服号)。
     ⚠ 全局「最后一次登录」，多开时各客户端互相覆盖，只能拿到最后一个。
  2) <安装目录>\\LocalData\\UserDefault.xml —— 节点 <XyqPocket_LoginInfo_<hostid>_<role_id>>，
     值形如 `时空区:6423:万里江山:429843638:1790259155:6:王昭君_:83:2:0`，即
     `大区:hostid:服务器:role_id:登录时间戳:?:角色名:等级:?:?`。这是【本机角色名册】。
     客户端运行期间会周期性重写这个文件，故读的时候可能撞上写入，read 带重试。

## 窗口 ↔ 角色 怎么对上（关键，也是唯一的启发式）
名册只有 role_id → 角色名，没有窗口身份。靠的是**登录时间戳精确到秒**：
实测窗口的游戏进程启动 22:12:20、该角色 login_ts = 1790259155 = 22:12:35，差 15 秒（登录耗时）。
故链路是：窗口的 shell 进程 → 它的 MyGame_x64r 子进程 → GetProcessTimes 启动时刻，
再和名册里每个角色的 login_ts 做 1:1 最优配对（|Δ-15s| 最小者胜，Δ 必须在 [0,180] 内）。

⚠ 已知局限（用户知情并选择不做缩略图兜底）：几个号**同一秒**登录会分不清，
   该窗口退回「号N」。此时看游戏窗口左上角标签页即可人工分辨。

## 安装目录怎么找
从「窗口所属进程的 exe 路径」往上找含 LocalData 的祖先目录，故换盘/多版本都不用配置；
config.game_dir 只是手动兜底覆盖（走 set_game_dir，与 window.set_game_process 同一套路）。
"""

import ctypes
import ctypes.wintypes
import json
import os
import re
import threading
import time

from . import window as win_mod


_GAME_EXE = "mygame_x64r.exe"        # 真正跑游戏的渲染子进程（见 core/window 模块头）
_LOGIN_RE = re.compile(r"<(XyqPocket_LoginInfo_[^>]+)>([^<]*)</\1>")

_TYPICAL_LOGIN = 15.0                # 实测登录耗时约 15s：在候选里挑「最像正常登录」的那个
_LOGIN_WINDOW = 180.0                # Δ 超过这个秒数就不认为该角色属于这个窗口
_TTL = 20.0                          # 绑定结果缓存时长：GUI 会反复问，别每次都枚举进程

_lock = threading.RLock()
_cache = {"key": None, "at": 0.0, "labels": {}, "order": []}
_data_dir = {"path": "", "at": 0.0}
_game_dir = ""                       # config.game_dir 覆盖（空 = 自动探测）


# ----------------------------------------------------------------------
# Win32：进程表（父子关系）+ 进程启动时刻
# ----------------------------------------------------------------------
_TH32CS_SNAPPROCESS = 0x00000002
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_EPOCH_DELTA = 11644473600.0         # FILETIME(1601) → Unix epoch(1970) 的秒差


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


_k32 = ctypes.windll.kernel32
_k32.CreateToolhelp32Snapshot.restype = ctypes.wintypes.HANDLE
_k32.CreateToolhelp32Snapshot.argtypes = [ctypes.wintypes.DWORD, ctypes.wintypes.DWORD]
_k32.Process32FirstW.restype = ctypes.wintypes.BOOL
_k32.Process32FirstW.argtypes = [ctypes.wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
_k32.Process32NextW.restype = ctypes.wintypes.BOOL
_k32.Process32NextW.argtypes = [ctypes.wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
_k32.OpenProcess.restype = ctypes.wintypes.HANDLE
_k32.OpenProcess.argtypes = [ctypes.wintypes.DWORD, ctypes.wintypes.BOOL, ctypes.wintypes.DWORD]
_k32.GetProcessTimes.restype = ctypes.wintypes.BOOL
_k32.GetProcessTimes.argtypes = [ctypes.wintypes.HANDLE,
                                 ctypes.POINTER(ctypes.wintypes.FILETIME),
                                 ctypes.POINTER(ctypes.wintypes.FILETIME),
                                 ctypes.POINTER(ctypes.wintypes.FILETIME),
                                 ctypes.POINTER(ctypes.wintypes.FILETIME)]
_k32.CloseHandle.restype = ctypes.wintypes.BOOL
_k32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]


def _proc_table():
    """系统进程快照 → [{pid, ppid, exe(小写)}]。CreateToolhelp32Snapshot 不需要管理员。"""
    out = []
    try:
        snap = _k32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
        if not snap or snap == ctypes.wintypes.HANDLE(-1).value:
            return out
        try:
            entry = _PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
            ok = _k32.Process32FirstW(snap, ctypes.byref(entry))
            while ok:
                out.append({"pid": int(entry.th32ProcessID),
                            "ppid": int(entry.th32ParentProcessID),
                            "exe": (entry.szExeFile or "").lower()})
                ok = _k32.Process32NextW(snap, ctypes.byref(entry))
        finally:
            _k32.CloseHandle(snap)
    except Exception:
        pass
    return out


def _start_epoch(pid):
    """进程启动时刻（Unix epoch 秒）。取不到返回 None。

    只用 PROCESS_QUERY_LIMITED_INFORMATION：低完整性进程也能查高完整性（游戏）进程，
    故脚本没提权时也能算——但 start.py 本来就会提权，两条路都通。"""
    try:
        h = _k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not h:
            return None
        try:
            c = ctypes.wintypes.FILETIME()
            e = ctypes.wintypes.FILETIME()
            k = ctypes.wintypes.FILETIME()
            u = ctypes.wintypes.FILETIME()
            if not _k32.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e),
                                        ctypes.byref(k), ctypes.byref(u)):
                return None
            ticks = (c.dwHighDateTime << 32) | c.dwLowDateTime
            return ticks / 1e7 - _EPOCH_DELTA
        finally:
            _k32.CloseHandle(h)
    except Exception:
        return None


# ----------------------------------------------------------------------
# 读客户端文件
# ----------------------------------------------------------------------
def set_game_dir(path):
    """配置游戏安装目录（来自 config.game_dir）。空/None = 自动从窗口进程 exe 反推。
    与 window.set_game_process 同一套路：模块级生效，TaskContext/App 初始化时各设一次。"""
    global _game_dir
    with _lock:
        new = (path or "").strip()
        if new != _game_dir:
            _game_dir = new
            _data_dir["path"] = ""
            _data_dir["at"] = 0.0


def _hwnd(win):
    try:
        return int(win._win._hWnd)
    except Exception:
        return 0


def _read_text(path, retries=4):
    """读文本（客户端可能正在重写 UserDefault.xml，故带重试）。失败返回 ""。"""
    for i in range(max(1, retries)):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except OSError:
            time.sleep(0.04 * (i + 1))
    return ""


def _read_json(path, retries=4):
    txt = _read_text(path, retries)
    if not txt:
        return None
    try:
        return json.loads(txt)
    except ValueError:
        return None


def _data_dir_from_wins(wins):
    """从窗口所属进程的 exe 路径往上找含 LocalData 的祖先目录。
    外壳在 <安装>\\Engine\\Binaries\\Win64\\ 下，往上 3~4 层即安装根，故搜 6 层足够。"""
    for w in wins:
        exe = win_mod.proc_image_path(_hwnd(w))
        if not exe:
            continue
        d = os.path.dirname(exe)
        for _ in range(6):
            cand = os.path.join(d, "LocalData")
            if os.path.isdir(cand):
                return cand
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent
    return ""


def data_dir(wins=None, ttl=_TTL):
    """游戏 LocalData 目录（找不到返回 ""）。先 config.game_dir，再从窗口进程 exe 反推；结果带缓存。"""
    now = time.time()
    with _lock:
        if _data_dir["path"] and now - _data_dir["at"] < ttl:
            return _data_dir["path"]
    path = ""
    if _game_dir:
        base = _game_dir
        cand = base if os.path.basename(base).lower() == "localdata" else os.path.join(base, "LocalData")
        if os.path.isdir(cand):
            path = cand
    if not path and wins:
        path = _data_dir_from_wins(wins)
    with _lock:
        _data_dir["path"] = path
        _data_dir["at"] = now
    return path


def roster(wins=None):
    """本机角色名册：{role_id: {role_id, name, level, server, hostid, login_ts}}。

    主源是 UserDefault.xml 的 XyqPocket_LoginInfo_* 节点（含精确登录时间戳）；
    last_server_info 只作兜底补全（新号首次登录时 XML 里可能还没它，且它没有时间戳/等级）。"""
    d = data_dir(wins)
    out = {}
    if not d:
        return out
    for m in _LOGIN_RE.finditer(_read_text(os.path.join(d, "UserDefault.xml"))):
        parts = m.group(2).split(":")
        if len(parts) < 8:
            continue
        rid = parts[3]
        try:
            ts = int(parts[4])
        except ValueError:
            ts = 0
        out[rid] = {"role_id": rid, "hostid": parts[1], "server": parts[2],
                    "login_ts": ts, "name": parts[6].strip(), "level": parts[7].strip()}
    info = _read_json(os.path.join(d, "last_server_info"))
    if isinstance(info, dict) and info.get("role_id"):
        rid = str(info["role_id"])
        rec = out.setdefault(rid, {"role_id": rid, "login_ts": 0, "name": "", "level": ""})
        if info.get("cName"):
            rec["name"] = str(info["cName"]).strip()
        if info.get("hostname"):
            rec.setdefault("server", str(info["hostname"]))
        if info.get("hostid"):
            rec.setdefault("hostid", str(info["hostid"]))
        rec.setdefault("level", "")
    return out


# ----------------------------------------------------------------------
# 显示名与「窗口 ↔ 角色」绑定
# ----------------------------------------------------------------------
def display_name(rec):
    """名册条目 → 显示名「角色名（等级）」。没有名字返回 ""。"""
    name = (rec.get("name") or "").strip()
    if not name:
        return ""
    level = str(rec.get("level") or "").strip()
    return "%s（%s）" % (name, level) if level else name


def fallback_label(index):
    """认不出角色名时的兜底显示（与旧行为一致）。"""
    return "号%d" % (index + 1)


def _window_pid(hwnd):
    try:
        p = ctypes.wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(int(hwnd), ctypes.byref(p))
        return int(p.value)
    except Exception:
        return 0


def assign_roles(shell_pids, starts_by_shell, names):
    """【纯函数，可单测】给每个窗口挑一个角色，返回 {窗口下标: role_id}。

    shell_pids      : 按窗口顺序排的外壳进程 PID
    starts_by_shell : {外壳PID: [该外壳下 MyGame_x64r 子进程的启动时刻(epoch 秒)]}
    names           : roster() 的名册 {role_id: {...login_ts...}}

    规则：Δ = 角色登录时间戳 - 进程启动时刻，必须落在 [0, _LOGIN_WINDOW]；
    取 |Δ - _TYPICAL_LOGIN| 最小者，每个角色只能用一次（1:1）。
    ⚠ 并列最优时【放弃】该窗口（宁可退回「号N」也不猜错）：几个号同一秒登录时两个候选分数
    完全相同，此时猜中与否纯属运气，而显示一个错名字比显示「号2」更误导人。
    """
    used = set()
    out = {}
    for i, spid in enumerate(shell_pids):
        cands = []
        for started in starts_by_shell.get(spid, []):
            if not started:
                continue
            for rid, rec in names.items():
                if rid in used or not rec.get("login_ts"):
                    continue
                delta = rec["login_ts"] - started
                if 0 <= delta <= _LOGIN_WINDOW:
                    cands.append((abs(delta - _TYPICAL_LOGIN), rid))
        if not cands:
            continue
        cands.sort()
        if len(cands) > 1 and cands[0][0] == cands[1][0]:
            continue                      # 并列最优 = 分不清，放弃该窗口
        used.add(cands[0][1])
        out[i] = cands[0][1]
    return out


def _compute(wins):
    """算出 {hwnd: 显示名}。认不出的窗口不放进结果，由调用方退回「号N」。"""
    labels = {}
    names = roster(wins)
    if not names:
        return labels
    by_parent = {}
    for p in _proc_table():
        if p["exe"] == _GAME_EXE:
            by_parent.setdefault(p["ppid"], []).append(p["pid"])
    shell_pids = [_window_pid(_hwnd(w)) for w in wins]
    starts = {}
    for spid in set(shell_pids):
        starts[spid] = [s for s in (_start_epoch(g) for g in by_parent.get(spid, [])) if s]
    for i, rid in assign_roles(shell_pids, starts, names).items():
        label = display_name(names[rid])
        hwnd = _hwnd(wins[i])
        if label and hwnd:
            labels[hwnd] = label
    return labels


def labels_for(wins):
    """按 wins 的顺序返回显示名列表（与 window.locate_all 的「号N」序号严格同序）。
    认不出角色的位置退回「号N」。结果带缓存，避免 GUI 反复调用时重复枚举进程。"""
    wins = list(wins)
    order = [_hwnd(w) for w in wins]
    key = tuple(order)
    now = time.time()
    with _lock:
        if key == _cache["key"] and now - _cache["at"] < _TTL:
            return [_cache["labels"].get(h) or fallback_label(i) for i, h in enumerate(order)]
    labels = _compute(wins)
    with _lock:
        _cache["key"] = key
        _cache["at"] = time.time()
        _cache["labels"] = labels
        _cache["order"] = [labels.get(h) or fallback_label(i) for i, h in enumerate(order)]
    return [labels.get(h) or fallback_label(i) for i, h in enumerate(order)]


def cached_labels():
    """上次算出的显示名列表（同序）。从没算过就返回 []——给「不方便枚举窗口」的界面用。"""
    with _lock:
        return list(_cache["order"])


def cached_label(index):
    """按序号取缓存的显示名；没有就退回「号N」（纯读缓存，不做枚举，可安全在 UI 线程调）。"""
    order = cached_labels()
    if isinstance(index, int) and 0 <= index < len(order) and order[index]:
        return order[index]
    return fallback_label(index if isinstance(index, int) and index >= 0 else 0)


def invalidate():
    """清掉缓存（窗口选择变了、或游戏重开时调）。"""
    with _lock:
        _cache["key"] = None
        _cache["at"] = 0.0
        _cache["labels"] = {}
        _cache["order"] = []
        _data_dir["path"] = ""
        _data_dir["at"] = 0.0
