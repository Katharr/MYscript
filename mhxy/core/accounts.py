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
import hashlib
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

# 标签条中只含姓名的相对区域：不含头像和关闭按钮，指纹不会被图标动画干扰。
_TAB_NAME_ROI = (0.055, 0.006, 0.245, 0.072)
_FINGERPRINT_SIZE = (56, 16)
_OCR_MIN_CONFIDENCE = 0.55
_OCR_MIN_MATCH = 0.84
_OCR_MIN_MARGIN = 0.12
_OCR_RETRY_SEC = 10.0
_IDENTITY_MAX_AGE_SEC = 1800.0
_DATA_DIR_TTL = 20.0

_lock = threading.RLock()
_cache = {"order": []}
_identity = {}                       # hwnd -> {fingerprint, pending, role_id, retry_at, seen_at}
_launch_sessions = {}                 # hwnd -> {role_id, profile_id, bound_at}; 仅本次一键启动会话
_data_dir = {"path": "", "at": 0.0}
_game_dir = ""                       # config.game_dir 覆盖（空 = 自动探测）
_ocr_lock = threading.Lock()
_ocr_infer_lock = threading.Lock()   # ONNX 推理串行，绝不让多窗口同时抢满 CPU
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
            _identity.clear()       # 名册来源变了，旧 role_id 不可再复用
            _cache["order"] = []


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


def data_dir(wins=None, ttl=_DATA_DIR_TTL):
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


def bind_launch_session(win, role_id, profile_id=None):
    """记录本次一键启动已核验的 ``窗口 -> 角色`` 绑定。

    只有启动任务在角色名模板点击成功、且顶部标签 OCR 与本机名册二次确认后才调用本函数。
    该映射只是运行期诊断/编排数据，显示身份仍以顶部标签 OCR 为权威。
    """
    hwnd = _hwnd(win)
    if not hwnd or not role_id:
        return False
    with _lock:
        _launch_sessions[hwnd] = {"role_id": str(role_id), "profile_id": profile_id,
                                  "bound_at": time.time()}
    return True


def launch_session(win):
    """返回本次启动会话绑定，未绑定或窗口无效时返回 ``None``。"""
    hwnd = _hwnd(win)
    with _lock:
        item = _launch_sessions.get(hwnd)
        return dict(item) if item else None


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


def locate_roster_name(image_bgr, names, role_id=None, expected_name=None):
    """在角色列表截图中 OCR 定位指定角色，返回 ``(cx, cy, score)`` 或 ``None``。

    常规路径由 ``role_id`` 从本机名册取目标姓名；首次运行尚未发现 LocalData 时，可传
    ``expected_name`` 手填精确姓名兜底。坐标相对传入图片左上角。
    """
    rec = (names or {}).get(str(role_id)) if role_id else None
    wanted = _normalize_name((rec or {}).get("name") or expected_name)
    if image_bgr is None or not wanted:
        return None
    engine = _get_ocr_engine()
    if engine is None:
        return None
    try:
        result, _elapsed = engine(image_bgr)
    except Exception:
        return None
    best = None
    for item in result or []:
        try:
            box, text, confidence = item[0], str(item[1] or ""), float(item[2])
        except (IndexError, TypeError, ValueError):
            continue
        if confidence < _OCR_MIN_CONFIDENCE:
            continue
        got = _normalize_name(text)
        if len(got) < 2:
            continue
        similarity = difflib.SequenceMatcher(None, wanted, got).ratio()
        if wanted in got:
            similarity = max(similarity, len(wanted) / max(len(got), 1))
        if similarity < _OCR_MIN_MATCH:
            continue
        try:
            points = list(box)
            cx = int(round(sum(float(p[0]) for p in points) / len(points)))
            cy = int(round(sum(float(p[1]) for p in points) / len(points)))
        except (TypeError, ValueError, ZeroDivisionError, IndexError):
            continue
        score = 0.7 * similarity + 0.3 * confidence
        if best is None or score > best[2]:
            best = (cx, cy, score)
    return best


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
            _ocr_engine = RapidOCR(text_score=_OCR_MIN_CONFIDENCE, print_verbose=False,
                                   intra_op_num_threads=1, inter_op_num_threads=1)
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


def _tab_name_rect(win):
    """返回顶部姓名条的屏幕绝对矩形。只截这一小块，绝不先抓整窗。"""
    rect = win.rect()
    if rect is None:
        return None
    try:
        left, top, w, h = (int(v) for v in rect)
        x0 = max(0, int(round(w * _TAB_NAME_ROI[0])))
        y0 = max(0, int(round(h * _TAB_NAME_ROI[1])))
        x1 = min(w, int(round(w * _TAB_NAME_ROI[2])))
        y1 = min(h, int(round(h * _TAB_NAME_ROI[3])))
        if x1 - x0 < 20 or y1 - y0 < 12:
            return None
        return [left + x0, top + y0, x1 - x0, y1 - y0]
    except (TypeError, ValueError):
        return None


def _tab_name_roi(win):
    """直接截取当前窗口的姓名小区域，典型大小约 100×40px。"""
    rect = _tab_name_rect(win)
    if rect is None:
        return None
    try:
        return win_mod.grab(rect)
    except Exception:
        return None


def _fingerprint(roi):
    """姓名区 → 抗缩放/颜色小变化的轻量指纹；只用于决定要不要再 OCR。"""
    if roi is None or roi.size == 0:
        return None
    try:
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(gray, _FINGERPRINT_SIZE, interpolation=cv2.INTER_AREA)
        _threshold, binary = cv2.threshold(small, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        return hashlib.blake2s(binary.tobytes(), digest_size=8).digest()
    except Exception:
        return None


def _prepare_ocr_roi(roi):
    """将已经很小的姓名区放大并加白边，提升中文小字 OCR 的检出率。"""
    if roi is None or roi.size == 0:
        return None
    try:
        enlarged = cv2.resize(roi, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        return cv2.copyMakeBorder(enlarged, 12, 12, 12, 12, cv2.BORDER_CONSTANT, value=(255, 255, 255))
    except Exception:
        return None


def _role_for_window(win, names, now):
    """按窗口独立缓存身份；指纹不变时零 OCR，变化后稳定一轮才重识别。"""
    hwnd = _hwnd(win)
    roi = _tab_name_roi(win)
    fingerprint = _fingerprint(roi)
    if not hwnd or fingerprint is None:
        return None

    with _lock:
        entry = _identity.get(hwnd)
        if entry is not None:
            entry["seen_at"] = now
            if fingerprint == entry.get("fingerprint") and entry.get("role_id"):
                return entry["role_id"]
            if fingerprint != entry.get("fingerprint"):
                # 首次看到新指纹先等待下一轮确认稳定，期间显示号N，杜绝旧名字串号。
                if entry.get("pending") != fingerprint:
                    entry["pending"] = fingerprint
                    entry["retry_at"] = 0.0
                    return None
                if now < float(entry.get("retry_at", 0.0)):
                    return None
            elif now < float(entry.get("retry_at", 0.0)):
                return None

    # 推理昂贵且会占 CPU：全局串行；入锁后重查，防止并发调用重复识别同一窗口。
    with _ocr_infer_lock:
        with _lock:
            entry = _identity.get(hwnd)
            if entry is not None and fingerprint == entry.get("fingerprint") and entry.get("role_id"):
                return entry["role_id"]
            if entry is not None and fingerprint != entry.get("fingerprint") \
                    and entry.get("pending") != fingerprint:
                return None
        rid = _ocr_role_id_without_lock(roi, names)
        with _lock:
            entry = _identity.setdefault(hwnd, {})
            entry["seen_at"] = now
            if rid:
                entry.update({"fingerprint": fingerprint, "pending": None, "role_id": rid, "retry_at": 0.0})
                return rid
            # 新指纹识别失败时不覆盖已确认身份，但本轮/重试前必须显示号N。
            entry["pending"] = fingerprint
            entry["retry_at"] = now + _OCR_RETRY_SEC
            return None


def _ocr_role_id_without_lock(roi, names):
    """_role_for_window 已持有推理锁时调用，避免同线程二次锁死。"""
    prepared = _prepare_ocr_roi(roi)
    if prepared is None:
        return None
    engine = _get_ocr_engine()
    if engine is None:
        return None
    try:
        result, _elapsed = engine(prepared)
    except Exception:
        return None
    return match_ocr_result(result, names)


def _compute(wins):
    """算出 {hwnd: 显示名}。每窗只在顶部姓名指纹变动时运行 OCR。"""
    labels = {}
    names = roster(wins)
    if not names:
        return labels
    now = time.time()
    for w in wins:
        rid = _role_for_window(w, names, now)
        hwnd = _hwnd(w)
        if rid and hwnd and rid in names:
            label = display_name(names[rid])
            if label:
                labels[hwnd] = label
    active_hwnds = {_hwnd(w) for w in wins}
    with _lock:
        stale = [h for h, entry in _identity.items()
                 if now - float(entry.get("seen_at", now)) > _IDENTITY_MAX_AGE_SEC]
        for hwnd in stale:
            _identity.pop(hwnd, None)
        # 一键启动绑定只属于本次存活窗口会话；窗口关闭或 HWND 改变后绝不复用旧绑定。
        for hwnd in [h for h in _launch_sessions if h not in active_hwnds]:
            _launch_sessions.pop(hwnd, None)
    return labels


def labels_for(wins):
    """按 wins 顺序返回显示名。常驻路径只抓姓名小图；指纹不变时不运行 OCR。"""
    wins = list(wins)
    order = [_hwnd(w) for w in wins]
    labels = _compute(wins)
    out = [labels.get(h) or fallback_label(i) for i, h in enumerate(order)]
    with _lock:
        _cache["order"] = list(out)
    return out


def prime_visible_labels(cfg):
    """主 GUI 显示前预热当前可见窗口身份，确保脚本本身不会遮住客户端标签条。"""
    cfg = cfg or {}
    win_mod.set_game_process(cfg.get("window_process") or win_mod.DEFAULT_GAME_PROCESS_SPEC)
    set_game_dir(cfg.get("game_dir"))
    wins = win_mod.locate_all(cfg.get("window_title", "梦幻西游"), cfg.get("window_offset", [0, 0]))
    return labels_for(wins)


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


def invalidate(clear_identity=False):
    """清掉界面顺序缓存；窗口选择变化默认保留按 HWND 登记的身份。

    `clear_identity=True` 只留给明确重置/切换游戏目录场景，避免选择窗口后重复 OCR。
    """
    with _lock:
        _cache["order"] = []
        _data_dir["path"] = ""
        _data_dir["at"] = 0.0
        if clear_identity:
            _identity.clear()
            _launch_sessions.clear()
