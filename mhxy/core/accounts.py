# -*- coding: utf-8 -*-
"""
角色名识别：用客户端标签条的屏幕像素 OCR 读取【当前可见角色名】，再用客户端文件名册核验。

## 身份来源
`UserDefault.xml` 的 `XyqPocket_LoginInfo_*` 节点是本机角色名册，含 role_id / 角色名 / 等级；
`last_server_info` 只补全新登录角色。它们不再承担「窗口 → 角色」配对，因为一个 MyTabCtrl
外壳可同时挂多个 MyGame 渲染进程，按进程启动时间猜会把窗口标成错误角色。

真正用于窗口身份的是客户端最上方标签条：它把当前激活角色名画成像素。这里用本地 RapidOCR
读取这块固定区域，并且只接受能唯一匹配上述名册的结果。OCR 不确定、标签条被遮挡、或名字在
名册中重复时，一律退回「号N」，绝不猜测。整个过程只截屏和读取客户端文本文件，不读内存、不
注入、不抓包，也无需用户额外标定。

进程时间戳的辅助函数仍保留给历史诊断工具使用，但不再参与界面/任务的角色显示。

## 安装目录怎么找
从「窗口所属进程的 exe 路径」往上找含 LocalData 的祖先目录，故换盘/多版本都不用配置；
config.game_dir 只是手动兜底覆盖（走 set_game_dir，与 window.set_game_process 同一套路）。
"""

import ctypes
import ctypes.wintypes
import difflib
import json
import os
import re
import threading
import time

import cv2

from . import window as win_mod


_GAME_EXE = "mygame_x64r.exe"        # 真正跑游戏的渲染子进程（见 core/window 模块头）
_LOGIN_RE = re.compile(r"<(XyqPocket_LoginInfo_[^>]+)>([^<]*)</\1>")

_TYPICAL_LOGIN = 15.0                # 仅历史诊断：原时间戳配对的典型登录耗时
_LOGIN_WINDOW = 180.0                # 仅历史诊断：原时间戳配对的最大时间窗
_TTL = 4.0                           # 标签切换会变角色名，缓存不能太久

# 标签条相对窗口的固定身份区：避开右侧关闭按钮，只覆盖图标 + 当前角色名。
_TAB_ROI = (0.04, 0.005, 0.255, 0.075)
_OCR_MIN_CONFIDENCE = 0.55
_OCR_MIN_MATCH = 0.84
_OCR_MIN_MARGIN = 0.12

_lock = threading.RLock()
_cache = {"key": None, "at": 0.0, "labels": {}, "order": []}
_data_dir = {"path": "", "at": 0.0}
_game_dir = ""                       # config.game_dir 覆盖（空 = 自动探测）
_ocr_lock = threading.Lock()
_ocr_engine = None
_ocr_error = None


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


def _normalize_name(text):
    """去掉 OCR 常漏/常误识的分隔符，保留中英文与数字用于名册比对。"""
    return "".join(ch for ch in str(text or "").lower() if ch.isalnum())


def match_ocr_result(result, names):
    """OCR 行结果 + 客户端名册 → 唯一 role_id；不确定时返回 None。

    这是纯函数，刻意不接受 OCR 结果之外的猜测。角色名就是有限白名单，故即使 OCR 识出
    多余符号或漏掉末尾下划线，也能可靠纠正；两个候选太接近时必须放弃。
    """
    scored = {}
    for item in result or []:
        try:
            text, confidence = str(item[1] or ""), float(item[2])
        except (IndexError, TypeError, ValueError):
            continue
        if confidence < _OCR_MIN_CONFIDENCE:
            continue
        got = _normalize_name(text)
        if len(got) < 2:
            continue
        for rid, rec in names.items():
            wanted = _normalize_name(rec.get("name"))
            if len(wanted) < 2:
                continue
            similarity = difflib.SequenceMatcher(None, wanted, got).ratio()
            if wanted in got:
                similarity = max(similarity, len(wanted) / max(len(got), 1))
            if similarity < _OCR_MIN_MATCH:
                continue
            score = 0.7 * similarity + 0.3 * confidence
            scored[rid] = max(scored.get(rid, 0.0), score)
    if not scored:
        return None
    ranked = sorted(((score, rid) for rid, score in scored.items()), reverse=True)
    best_score, best_rid = ranked[0]
    if len(ranked) > 1 and best_score - ranked[1][0] < _OCR_MIN_MARGIN:
        return None
    return best_rid


def _get_ocr_engine():
    """按需初始化本地 OCR，避免启动 GUI 时加载 ONNX 模型。失败后安全降级到「号N」。"""
    global _ocr_engine, _ocr_error
    with _ocr_lock:
        if _ocr_engine is not None:
            return _ocr_engine
        if _ocr_error is not None:
            return None
        try:
            from rapidocr_onnxruntime import RapidOCR
            _ocr_engine = RapidOCR(text_score=_OCR_MIN_CONFIDENCE, print_verbose=False)
        except Exception as e:
            _ocr_error = str(e)
            return None
    return _ocr_engine


def ocr_status():
    """返回 OCR 可用状态，供诊断工具输出，不触发模型初始化。"""
    with _ocr_lock:
        if _ocr_engine is not None:
            return "ready"
        if _ocr_error:
            return "unavailable: %s" % _ocr_error
    return "not_loaded"


def _tab_name_roi(win):
    """截取客户端顶部标签里的图标和角色名，按当前窗口尺寸同比缩放后放大给 OCR。"""
    rect = win.rect()
    if rect is None:
        return None
    try:
        img = win_mod.grab(rect)
        h, w = img.shape[:2]
        x0 = max(0, int(round(w * _TAB_ROI[0])))
        y0 = max(0, int(round(h * _TAB_ROI[1])))
        x1 = min(w, int(round(w * _TAB_ROI[2])))
        y1 = min(h, int(round(h * _TAB_ROI[3])))
        if x1 - x0 < 20 or y1 - y0 < 12:
            return None
        roi = img[y0:y1, x0:x1]
        # 原标签字高约十余像素；三倍插值 + 白边显著提高中文小字的检出率。
        roi = cv2.resize(roi, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        return cv2.copyMakeBorder(roi, 12, 12, 12, 12, cv2.BORDER_CONSTANT, value=(255, 255, 255))
    except Exception:
        return None


def _ocr_role_id(win, names):
    engine = _get_ocr_engine()
    roi = _tab_name_roi(win)
    if engine is None or roi is None:
        return None
    try:
        result, _elapsed = engine(roi)
    except Exception:
        return None
    return match_ocr_result(result, names)


def _compute(wins):
    """算出 {hwnd: 显示名}。只采信当前窗口标签条 OCR 的唯一名册匹配。"""
    labels = {}
    names = roster(wins)
    if not names:
        return labels
    for w in wins:
        rid = _ocr_role_id(w, names)
        hwnd = _hwnd(w)
        if rid and hwnd:
            label = display_name(names[rid])
            if label:
                labels[hwnd] = label
    return labels


def labels_for(wins):
    """按 wins 的顺序返回显示名列表（与 window.locate_all 的「号N」序号严格同序）。
    认不出角色的位置退回「号N」。结果短暂缓存，避免 GUI 反复 OCR 同一个标签条。"""
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
