# -*- coding: utf-8 -*-
"""
游戏窗口与屏幕截图。封装“找窗口 / 取窗口矩形 / 截图 / 窗口内坐标换算”，
让上层任务不用关心 mss / pygetwindow 细节。
"""

import os
import re
import math
import time
import ctypes
import ctypes.wintypes
import threading

import numpy as np
import cv2
import mss
import pygetwindow as gw

from . import vision


# 句柄相关的 user32 函数：必须显式声明 restype/argtypes 为 HWND(=void*)，
# 否则 64 位 Python 下默认 c_int 会把窗口句柄截断，比较/传参全错。
_user32 = ctypes.windll.user32
_user32.GetForegroundWindow.restype = ctypes.wintypes.HWND
_user32.SetForegroundWindow.argtypes = [ctypes.wintypes.HWND]
_user32.SetForegroundWindow.restype = ctypes.wintypes.BOOL
_user32.BringWindowToTop.argtypes = [ctypes.wintypes.HWND]
_user32.SetActiveWindow.argtypes = [ctypes.wintypes.HWND]
_user32.ShowWindow.argtypes = [ctypes.wintypes.HWND, ctypes.c_int]
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
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


# ---- SendInput（仅用于新外壳窗口的「拖边缩放」，见 GameWindow.resize_to）----
_user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
_user32.SetCursorPos.restype = ctypes.wintypes.BOOL
_user32.GetCursorPos.argtypes = [ctypes.POINTER(ctypes.wintypes.POINT)]
_user32.GetCursorPos.restype = ctypes.wintypes.BOOL
_user32.SendInput.argtypes = [ctypes.wintypes.UINT, ctypes.c_void_p, ctypes.c_int]
_user32.SendInput.restype = ctypes.wintypes.UINT

_INPUT_MOUSE = 0
_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.wintypes.LONG), ("dy", ctypes.wintypes.LONG),
                ("mouseData", ctypes.wintypes.DWORD), ("dwFlags", ctypes.wintypes.DWORD),
                ("time", ctypes.wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.wintypes.DWORD), ("mi", _MOUSEINPUT)]


def _mouse_button(down):
    inp = _INPUT(type=_INPUT_MOUSE,
                 mi=_MOUSEINPUT(0, 0, 0, _MOUSEEVENTF_LEFTDOWN if down else _MOUSEEVENTF_LEFTUP, 0, None))
    _user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))


# ---- 游戏窗口识别：按「进程 exe 名」过滤（标题会和别的窗口撞，进程名才稳）----
#   背景（踩坑）：原先只按标题子串 "梦幻西游" 匹配，结果【终端/编辑器等标题里恰好含这几个字的窗口】
#   会被误认成游戏窗口去点击（实测把 Windows Terminal 当成了「号1」，因为它的标签名含「梦幻西游」）。
#   游戏窗口类名是【随机串】（每个窗口都不同，无法白名单），但进程 exe 名稳定，故据此过滤最可靠。
#
#   2026-09 客户端大更新后架构变化（脚本「识别不到窗口」的根因）：可见顶层窗口改由【标签外壳】
#   进程 MyTabCtrl_x64r.exe 持有（类名 __my_tabctrl_winclass_<随机>，标题仍是「梦幻西游：时空」，
#   每个游戏号一个顶层窗口）；真正的游戏进程 MyGame_x64r.exe 退居渲染子进程，只剩 1×1 不可见的
#   GDI/IME 隐藏窗口，pygetwindow 枚举不到可用矩形。故默认两个进程名都认（新外壳 + 旧客户端，
#   兼容回退与多版本）。若以后 exe 再改名，改 config 顶层 window_process 即可（空串=退回纯标题匹配）。
DEFAULT_GAME_PROCESS_SPEC = "MyTabCtrl_x64r.exe,MyGame_x64r.exe"
_GAME_PROCESSES = {"mytabctrl_x64r.exe", "mygame_x64r.exe"}
# window_process 里多个名字的分隔符：中英文逗号/分号、竖线、空白
_SPLIT_RE = re.compile(r"[,，;；|\s]+")


def _parse_process_names(name):
    """把 config.window_process 解析成「小写 exe basename 集合」。
    接受：字符串（单个名，或用逗号/分号/竖线/空白分隔的多个名）、字符串列表/元组/集合。
    空串/None/空集合 → None（表示不按进程过滤，退回纯标题匹配，与旧行为一致）。"""
    if name is None:
        return None
    if isinstance(name, (list, tuple, set)):
        items = [str(x) for x in name]
    else:
        items = _SPLIT_RE.split(str(name))
    out = {x.strip().lower() for x in items if x.strip()}
    return out or None


def set_game_process(name):
    """配置「只认这些 exe 进程的窗口」（支持多个，见 _parse_process_names）。来自 config.window_process。
    传空/None=不按进程过滤（退回纯标题匹配，与旧行为一致）。进程名比对大小写不敏感。"""
    global _GAME_PROCESSES
    _GAME_PROCESSES = _parse_process_names(name)


def _proc_basename(hwnd):
    """返回 hwnd 所属进程的 exe basename（小写）。取不到返回 ""。"""
    try:
        pid = ctypes.wintypes.DWORD()
        _user32.GetWindowThreadProcessId(int(hwnd), ctypes.byref(pid))
        hp = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not hp:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(512)
            sz = ctypes.wintypes.DWORD(512)
            if not _kernel32.QueryFullProcessImageNameW(hp, 0, buf, ctypes.byref(sz)):
                return ""
            return os.path.basename(buf.value or "").lower()
        finally:
            _kernel32.CloseHandle(hp)
    except Exception:
        return ""


def _match_basic(w, title_substr):
    """游戏窗口基本判定：标题含关键字 + 尺寸够大 +（若配置了 _GAME_PROCESSES）所属进程在白名单内。
    不在此查「最小化」——locate() 允许最小化窗口当候选并排后面，locate_all() 自行另外排除。"""
    try:
        if title_substr not in (w.title or ""):
            return False
        if w.width <= 100 or w.height <= 100:
            return False
        hwnd = w._hWnd
    except Exception:
        return False
    if _GAME_PROCESSES and _proc_basename(hwnd) not in _GAME_PROCESSES:
        return False
    return True


def _force_foreground(hwnd, tries=3):
    """把 hwnd 强制切到前台并校验。成功返回 True。

    为什么不直接用 pygetwindow.activate()：它只是 `SetForegroundWindow(hwnd)`，而 Windows 的
    『防焦点抢占』会在前台属于别的窗口/进程时【拒绝】这次调用(返回0)——多开轮转里这极常见，
    结果目标号没真正到前台，随后的点击落在后台号上被吞/点歪（曾导致秘境点「挑战」点到聊天）。
    这里用业界通行的解法：先 ShowWindow+BringWindowToTop，再 AttachThreadInput 把本线程附到
    当前前台线程后 SetForegroundWindow（绕过抢占锁），最后用 GetForegroundWindow 校验、失败重试。"""
    if not hwnd:
        return False
    hwnd = int(hwnd)
    SW_SHOW = 5
    kernel32 = ctypes.windll.kernel32
    cur_tid = kernel32.GetCurrentThreadId()
    for _ in range(max(1, tries)):
        try:
            if int(_user32.GetForegroundWindow() or 0) == hwnd:
                return True
        except Exception:
            pass
        try:
            _user32.ShowWindow(hwnd, SW_SHOW)
            _user32.BringWindowToTop(hwnd)
            fg = _user32.GetForegroundWindow()
            fg_tid = _user32.GetWindowThreadProcessId(fg, None) if fg else 0
            tgt_tid = _user32.GetWindowThreadProcessId(hwnd, None)
            attached = []
            for tid in (fg_tid, tgt_tid):
                if tid and tid != cur_tid:
                    _user32.AttachThreadInput(cur_tid, tid, True)
                    attached.append(tid)
            _user32.SetForegroundWindow(hwnd)
            _user32.SetActiveWindow(hwnd)
            for tid in attached:
                _user32.AttachThreadInput(cur_tid, tid, False)
        except Exception:
            pass
        time.sleep(0.12)
    try:
        return int(_user32.GetForegroundWindow() or 0) == hwnd
    except Exception:
        return False


def is_admin():
    """本进程是否有管理员权限。新客户端（requireAdministrator 外壳）高 IL 运行，
    非管理员脚本对其窗口的 SetWindowPos/拖拽缩放/SendInput 都会被 UIPI 静默拒绝。"""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def set_dpi_aware():
    """让脚本按真实像素工作，避免 Win 缩放(125%/150%)导致坐标错位。进程级，调一次即可。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


class GameWindow:
    """对一个游戏窗口的封装。"""

    def __init__(self, title_substr, offset=(0, 0)):
        self.title_substr = title_substr
        self.offset = tuple(offset)
        self._win = None

    # ---- 查找与激活 ----
    def locate(self):
        """按标题关键字找窗口，找到返回 True。"""
        candidates = []
        for w in gw.getAllWindows():
            if _match_basic(w, self.title_substr):
                candidates.append(w)
        if not candidates:
            self._win = None
            return False
        candidates.sort(key=lambda x: (not x.isMinimized, x.width * x.height), reverse=True)
        self._win = candidates[0]
        return True

    @property
    def found(self):
        return self._win is not None

    @property
    def title(self):
        return self._win.title if self._win else ""

    def bind(self, win):
        """直接绑定一个已找到的窗口对象（多开枚举用），返回 self。
        绑定后 rect()/activate() 等都作用在这个固定窗口上，不再自动选最大。"""
        self._win = win
        return self

    def rect(self):
        """[left, top, width, height]（屏幕绝对坐标，已叠加 offset）。未定位/窗口已关返回 None。"""
        if not self._win:
            return None
        try:
            return [self._win.left + self.offset[0], self._win.top + self.offset[1],
                    self._win.width, self._win.height]
        except Exception:
            # 绑定的窗口被关闭后，访问 .left/.width 会抛异常（win32 句柄失效）。
            return None

    def activate(self):
        """把本窗口切到前台并【校验确实成功】，成功返回 True、失败返回 False。

        关键：多开时若没真正切到前台就点击，点击会落在后台号上被吞/点歪。故这里用 _force_foreground
        （AttachThreadInput 绕过焦点抢占锁 + GetForegroundWindow 校验 + 重试），调用方据返回值决定是否点击。"""
        if not self._win:
            return False
        try:
            if self._win.isMinimized:
                self._win.restore()
                time.sleep(0.15)
        except Exception:
            pass
        try:
            hwnd = self._win._hWnd
        except Exception:
            hwnd = None
        if not hwnd:
            # 拿不到句柄时退回 pygetwindow 的 activate（尽力而为）
            try:
                self._win.activate()
                time.sleep(0.2)
                return True
            except Exception:
                return False
        ok = _force_foreground(hwnd)
        time.sleep(0.15 if ok else 0.05)
        return ok

    # 新标签外壳（MyTabCtrl_x64r.exe）把缩放热区画在客户区【内侧】：实测右下角
    # (right-14..right-2, bottom-14..bottom-2) 命中 HTBOTTOMRIGHT，边中点内侧 0~6px 命中
    # HTLEFT/HTTOP 等。取 -8 稳在双向热区里。
    _GRIP_INSET = 8

    def _drag_resize(self, w, h):
        """用「真人拖拽右下角缩放热区」把窗口调到 [w,h]，闭环按实测尺寸纠偏。

        背景：2026-09 更新后的标签外壳对 SetWindowPos 改尺寸【表面成功实则忽略】（elevated 同 IL
        也一样），只认交互拖拽；缩放热区藏在客户区内缘（NCHITTEST 实测右下 right/bottom 内
        2~14px 命中 HTBOTTOMRIGHT）；且外壳【锁死纵横比】（单拖一条边也会按比例联动另一维，
        实测比例约 1.23）——只允许沿比例线整体缩放。
        故纠偏必须沿比例线【同向】推进：每轮按几何平均缩放比 s=sqrt((w/cw)*(h/ch)) 把热区拖到
        对应位置（不能宽、高各自独立纠偏，否则两维来回拔河，在锁比例的窗口上永远收敛不了）。
        基准尺寸若在新外壳上标定（本就在比例线上）可精确命中；旧客户端留下的离线基准只会收敛到
        最近可达尺寸并返回 False（调用方据此提示重新标定）。
        前提：本进程与游戏同完整性级别（start.py 已自动 UAC 提权），否则输入会被 UIPI 丢弃。
        成功(两维误差≤4px)返回 True；窗口失效/达不到（离线基准、超出屏幕）返回 False。"""
        if not self._win:
            return False
        if self.rect() is None:
            return False
        # 缩放拖拽必须在前台窗口上进行（非前台首击可能只激活不进拖拽）
        if not self.activate():
            return False
        try:
            vx = _user32.GetSystemMetrics(76)          # SM_XVIRTUALSCREEN
            vy = _user32.GetSystemMetrics(77)
            vw = _user32.GetSystemMetrics(78)          # SM_CXVIRTUALSCREEN
            vh = _user32.GetSystemMetrics(79)
            inset = self._GRIP_INSET

            def place_cursor(x, y):
                x = min(max(int(x), vx + 1), vx + vw - 2)
                y = min(max(int(y), vy + 1), vy + vh - 2)
                _user32.SetCursorPos(x, y)

            rr = self.rect()
            place_cursor(rr[0] + rr[2] - inset, rr[1] + rr[3] - inset)
            time.sleep(0.15)
            _mouse_button(True)
            time.sleep(0.12)
            last_s = None
            stuck = 0
            try:
                for _ in range(40):
                    rr = self.rect()
                    if rr is None:
                        return False
                    cw, ch = rr[2], rr[3]
                    if abs(cw - w) <= 4 and abs(ch - h) <= 4:
                        return True
                    # 几何平均缩放比：沿锁死的比例线同向推进，避免两维拔河
                    s = ((w / cw) * (h / ch)) ** 0.5
                    s = min(max(s, 0.90), 1.10)         # 单步最多 ±10%，拟人且防过冲
                    if last_s is not None and abs(s - last_s) < 0.002:
                        stuck += 1
                        if stuck >= 3:                 # 收敛到比例线上最近点仍不达标=基准离线
                            return False
                    else:
                        stuck = 0
                    last_s = s
                    place_cursor(rr[0] + cw * s - inset, rr[1] + ch * s - inset)
                    time.sleep(0.05)
            finally:
                _mouse_button(False)
            time.sleep(0.2)
            rr = self.rect()
            return rr is not None and abs(rr[2] - w) <= 4 and abs(rr[3] - h) <= 4
        except Exception:
            try:
                _mouse_button(False)
            except Exception:
                pass
            return False

    def resize_to(self, w, h, move_to=None):
        """把窗口尺寸还原到 [w, h]（可选 move_to=(left,top) 一并复位位置）。
        成功(尺寸误差≤4px)返回 True；否则返回 False（窗口失效/不支持自由缩放/档位吸附不到）。

        两条路径：先试程序化 SetWindowPos（旧客户端/普通窗口直接生效，最便宜）；
        实测无效（新标签外壳会假装成功并忽略）再退回真人式拖边缩放 _drag_resize。"""
        if not self._win:
            return False
        w, h = int(w), int(h)
        try:
            if self._win.isMinimized:
                self._win.restore()
                time.sleep(0.15)

            def reached():
                rr = self.rect()
                return rr is not None and abs(rr[2] - w) <= 4 and abs(rr[3] - h) <= 4

            # 路径一：程序化缩放（最多试两次，间隔读真实矩形，忽略「假成功」）
            try:
                for _ in range(2):
                    self._win.resizeTo(w, h)
                    if move_to is not None:
                        self._win.moveTo(int(move_to[0]), int(move_to[1]))
                    time.sleep(0.15)
                    if reached():
                        return True
            except Exception:
                pass

            # 路径二：新标签外壳——拖缩放热区（闭环纠偏）
            if self._drag_resize(w, h):
                if move_to is not None:
                    try:
                        self._win.moveTo(int(move_to[0]), int(move_to[1]))
                    except Exception:
                        pass
                return True
            return False
        except Exception:
            return False

    # ---- 坐标换算 ----
    def region_to_screen_rect(self, region):
        """窗口内 [x,y,w,h] -> 屏幕绝对 [left,top,w,h]。"""
        r = self.rect()
        if r is None or not region:
            return None
        return [r[0] + region[0], r[1] + region[1], region[2], region[3]]

    def region_center_screen(self, region):
        """窗口内 [x,y,w,h] 的中心点 -> 屏幕绝对 (x,y)。"""
        sr = self.region_to_screen_rect(region)
        if sr is None:
            return None
        return (sr[0] + sr[2] // 2, sr[1] + sr[3] // 2)


# ---- 多窗口枚举与目标选择（多开/选择窗口基础特性）----
def locate_all(title_substr, offset=(0, 0), max_n=0):
    """枚举所有标题含 title_substr、非最小化的窗口，按屏幕位置排序后各包一个 GameWindow 返回。

    用于「选择窗口/多开」：用户把多个号并排摆在桌面上，这里把它们稳定地认成 号1/号2/号3…
    排序规则：先按上边缘分行（每 120px 一带），同一行内按左边缘左→右——和肉眼「从左到右数」一致。
    max_n>0 时最多取前 max_n 个。找不到返回空列表。
    """
    found = []
    for w in gw.getAllWindows():
        try:
            if _match_basic(w, title_substr) and not w.isMinimized:
                found.append(w)
        except Exception:
            continue
    found.sort(key=lambda x: (int(x.top) // 120, int(x.left)))
    if max_n and max_n > 0:
        found = found[:max_n]
    return [GameWindow(title_substr, offset).bind(w) for w in found]


def resolve_targets(title_substr, offset, targets):
    """按 targets 配置从 locate_all 结果里选出要操作的窗口列表（纯函数，供任务与 GUI 共用）。

    targets 结构见 config.DEFAULT_CONFIG["targets"]：
      - 单开(multi=False)：返回 [第 single_index 个窗口]（序号越界自动回退 0）。
      - 多开(multi=True) ：按 multi_indices 选子集（空=全部），再按 max_windows 截断。
    找不到任何窗口返回 []。
    """
    targets = targets or {}
    wins = locate_all(title_substr, offset)
    if not wins:
        return []
    if targets.get("multi"):
        idxs = targets.get("multi_indices") or list(range(len(wins)))
        sel = [wins[i] for i in idxs if 0 <= i < len(wins)]
        if not sel:                       # 选中的序号全失效 → 兜底用全部
            sel = wins
        cap = targets.get("max_windows", 0)
        if cap and cap > 0:
            sel = sel[:cap]
        return sel
    i = targets.get("single_index", 0)
    if not (isinstance(i, int) and 0 <= i < len(wins)):
        i = 0
    return [wins[i]]


def window_at_point(title_substr, offset, x, y):
    """返回屏幕坐标 (x,y) 落在其内的游戏窗口（标定时按「框在哪个号上」定位参照窗口，不必激活）。
    多个窗口重叠都含该点时，优先当前前台窗口，否则取面积最小（最贴合）的那个。找不到返回 None。"""
    cands = []
    for w in locate_all(title_substr, offset):
        r = w.rect()
        if r and r[0] <= x <= r[0] + r[2] and r[1] <= y <= r[1] + r[3]:
            cands.append((w, r))
    if not cands:
        return None
    try:
        fg = int(_user32.GetForegroundWindow() or 0)
    except Exception:
        fg = 0
    if fg:
        for w, _r in cands:
            try:
                if int(w._win._hWnd) == fg:
                    return w
            except Exception:
                pass
    cands.sort(key=lambda wr: wr[1][2] * wr[1][3])   # 面积最小=最贴合
    return cands[0][0]


def restore_targets_size(title_substr, offset, targets, base_size):
    """把当前选中的目标窗口（单开1个/多开多个）逐个还原到 base_size=[w,h]。

    复用 resolve_targets 选窗，保证和任务实际操作的是同一批号。操作每个号前先 activate()
    切前台再 resize。返回 (ok_count, total, actual_sizes)：
      - ok_count : 成功还原(尺寸误差≤4px)的号数
      - total    : 选中的号数
      - actual_sizes : 各号 resize 后的实际 [w,h]（窗口失效为 None），供上层判断是否真生效。
    base_size 非法(空/非两元素)时返回 (0, 0, [])。
    """
    if not base_size or len(base_size) < 2:
        return (0, 0, [])
    w, h = int(base_size[0]), int(base_size[1])
    wins = resolve_targets(title_substr, offset, targets)
    ok = 0
    actual = []
    for win in wins:
        win.activate()
        success = win.resize_to(w, h)
        r = win.rect()
        actual.append([r[2], r[3]] if r else None)
        if success:
            ok += 1
    return (ok, len(wins), actual)


# ---- 截图 ----
# mss 用 GDI，srcdc 等句柄存在「线程本地」里：在 A 线程建的实例不能在 B 线程用，
# 否则报 'object has no attribute srcdc'。任务跑在后台线程，故每个线程各持一份。
_tls = threading.local()


def _get_sct():
    sct = getattr(_tls, "sct", None)
    if sct is None:
        sct = mss.mss()
        _tls.sct = sct
    return sct


def grab(rect):
    """截取屏幕矩形 [left, top, w, h]，返回 OpenCV BGR 图像。"""
    left, top, w, h = rect
    raw = _get_sct().grab({"left": int(left), "top": int(top),
                           "width": int(w), "height": int(h)})
    img = np.array(raw)  # BGRA
    return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)


# ----------------------------------------------------------------------
# 跨分辨率识别（方案二）：截图在内存里预缩放回「标定基准尺度」+ 坐标反算
# ----------------------------------------------------------------------
# 为什么在这层做：识别前把画面缩回标定时的尺度，模板就能照原样匹配（详见 vision.scaled_threshold 的
# 实测依据）。用户拍板的铁律是【绝不动游戏窗口本身】（也绝不改窗口尺寸来迁就识别），
# 所以这里只在内存里的 numpy 数组上缩放，一个游戏像素都不动。
#
# ⚠ 最容易出 bug 的点（识别对了却点歪）：缩放后的命中坐标是【缩放后画面】的坐标系，
#   必须乘 1/scale 换回当前屏幕坐标。约定：一律走 ScaledScene.to_screen()/
#   ScaledScene.match_local()，别在任务里自己乘。
SIZE_TOL = 4          # 与标定尺寸相差 ≤4px 视为同尺寸（窗口外壳会吸附到档位），不缩放


def _resize(img, w, h):
    """按目标宽高缩放（缩小时 INTER_AREA、放大时 INTER_LINEAR，与 list_row._scaled 一致）。"""
    interp = cv2.INTER_AREA if (w * h) < (img.shape[1] * img.shape[0]) else cv2.INTER_LINEAR
    return cv2.resize(img, (max(6, int(w)), max(6, int(h))), interpolation=interp)


class ScaledScene:
    """一次截图的「可归一化」包装：懒缩放 + 屏幕坐标反算。任务侧只需：

        sc = win_mod.grab_scene(rect, calib_size)     # calib_size 见 core/calib_profiles.active_size
        m = sc.match_local(tpl, threshold)            # 在【缩放后画面】里匹配（返回缩放后坐标）
        if m: mouse.click(*sc.to_screen(m[0], m[1]))  # 一键换回当前屏幕坐标

    约定（务必遵守，否则会点歪）：
    - match_local 返回的坐标属于【缩放后画面】，只允许经 to_screen() 使用；
    - sc.img / sc.local_rect 也是缩放后坐标系（配 sc.scale）；
    - calib_size=None 或与窗口尺寸一致（±4px）时 scale=1.0、img 就是原图、坐标原样，
      与「直接 grab() + vision.match()」逐字节等价（零回归）。
    """

    __slots__ = ("_rect", "_calib", "_img", "_norm", "_scale")

    def __init__(self, rect, calib_size=None, img=None):
        self._rect = [int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3])]
        self._calib = [int(calib_size[0]), int(calib_size[1])] if calib_size and len(calib_size) >= 2 else None
        self._img = img
        self._norm = None          # 缩放后的缓存图（懒算一次，别每次访问重采样）
        self._scale = self._compute_scale()

    # ---- 基本属性 ----
    @property
    def screen_rect(self):
        """截图时用的屏幕矩形 [left,top,w,h]（当前尺寸，未缩放）。"""
        return list(self._rect)

    @property
    def scale(self):
        """「标定基准尺寸 ÷ 当前窗口尺寸」的缩放比（几何平均，抗单维误差）。
        =1.0 表示不需要归一化（尺寸一致或没标定过）。"""
        return self._scale

    @property
    def normalized(self):
        """是否真的做了预缩放（scale 明显偏离 1）。"""
        return abs(self._scale - 1.0) >= 0.02

    # ---- 图像（懒加载：第一次访问才截图/缩放，只用坐标不算的场景不付截图代价）----
    def _compute_scale(self):
        if not self._calib:
            return 1.0
        w, h, bw, bh = self._rect[2], self._rect[3], self._calib[0], self._calib[1]
        if w <= 0 or h <= 0 or bw <= 0 or bh <= 0:
            return 1.0
        if abs(w - bw) <= SIZE_TOL and abs(h - bh) <= SIZE_TOL:
            return 1.0
        return max(0.5, min(2.0, math.sqrt((bw / w) * (bh / h))))

    @property
    def img(self):
        """要拿去匹配的画面：需要归一化时是【预缩放回基准尺度】的图，否则就是原始截图。
        缩放结果缓存一次（同一张场景常被多个模板反复匹配，别每次访问都重采样）。"""
        if self._img is None:
            self._img = grab(self._rect)
        if not self.normalized:
            return self._img
        if self._norm is None:
            self._norm = _resize(self._img, self._rect[2] * self._scale, self._rect[3] * self._scale)
        return self._norm

    @property
    def local_rect(self):
        """缩放后画面在原屏幕矩形内的比例矩形：[x, y, w, h]（x/y 恒为 0，尺寸按 scale）。
        仅供绘制/调试；坐标换算一律用 to_screen()。"""
        if not self.normalized:
            return [0, 0, self._rect[2], self._rect[3]]
        return [0, 0, int(round(self._rect[2] * self._scale)), int(round(self._rect[3] * self._scale))]

    # ---- 坐标换算（唯一出口）----
    def to_screen(self, x, y):
        """把【缩放后画面】里的点换回当前屏幕绝对坐标。"""
        if x is None or y is None:
            return None
        s = self._scale or 1.0
        return (self._rect[0] + int(round(x / s)), self._rect[1] + int(round(y / s)))

    def to_screen_box(self, box):
        """把缩放后坐标系里的 [x,y,w,h] 换回屏幕绝对 [left,top,w,h]。"""
        if not box:
            return None
        s = self._scale or 1.0
        return [self._rect[0] + int(round(box[0] / s)), self._rect[1] + int(round(box[1] / s)),
                int(round(box[2] / s)), int(round(box[3] / s))]

    # ---- 匹配（阈值按 scale 动态放宽，见 vision.scaled_threshold）----
    def match(self, template_bgr, threshold):
        """在【缩放后画面】里匹配模板；命中返回缩放后坐标 (cx, cy, score)，否则 None。
        ⚠ 拿去点击前必须 to_screen()。"""
        return vision.match_scaled(self.img, template_bgr, threshold, self._scale)


def grab_scene(rect, calib_size=None):
    """截取屏幕矩形，返回可归一化坐标的 ScaledScene（识别 + 点击坐标一次搞定，见 ScaledScene 约定）。
    calib_size 传「激活尺寸组的尺寸」（core/calib_profiles.active_size）；None=不归一化（同旧行为）。"""
    return ScaledScene(rect, calib_size)
