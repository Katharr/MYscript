# -*- coding: utf-8 -*-
"""Spec 00 的可重复 UI 基线测量工具。

默认模式会为每个样本启动一个独立 GUI 进程，避免上一次测量的 Tk 状态影响下一次。
脚本只读配置；子进程把配置快照/写盘入口替换为空操作，测量期间不会修改 config.json。

用法（项目根目录）：
    python docs/specs/measure_baseline.py --output docs/specs/baseline-YYYYMMDD.json

脚本不伪造三窗口数据：--windows 3 需要测量时确实存在三个目标窗口，否则返回
"unavailable"，文档生成器会把该场景保留为未测并给出人工操作步骤。
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = Path(__file__).resolve()
READY_TIMEOUT = 45.0
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _working_set_mb() -> float | None:
    """返回当前进程工作集（Windows），无法读取时返回 None。"""
    try:
        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        psapi = ctypes.WinDLL("psapi.dll")
        kernel32 = ctypes.WinDLL("kernel32.dll")
        get_current = kernel32.GetCurrentProcess
        get_current.restype = ctypes.c_void_p
        get_info = psapi.GetProcessMemoryInfo
        get_info.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong]
        get_info.restype = ctypes.c_int
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        if not get_info(get_current(), ctypes.byref(counters), counters.cb):
            return None
        return round(counters.WorkingSetSize / (1024 * 1024), 2)
    except Exception:
        return None


def _wait(root, seconds: float = 0.03) -> None:
    """让 Tk 完成几何计算和一小段事件处理。"""
    deadline = time.perf_counter() + seconds
    while time.perf_counter() < deadline:
        root.update_idletasks()
        root.update()


def _child(mode: str, result_path: Path) -> int:
    """在独立进程内执行一轮测量。"""
    started = time.perf_counter()
    # App.__init__ 会留配置世代快照；基线是只读实验，必须在导入 App 前禁用写盘。
    import mhxy.core.config as cfg_mod

    cfg_mod.snapshot_config = lambda: None
    cfg_mod.save_config = lambda *args, **kwargs: None

    result: dict[str, object] = {
        "mode": mode,
        "started_at": started,
        "actions": {},
        "status": "ok",
    }
    if mode == "three-game":
        # 只接受真实规则命中的目标窗口；没有三个窗口时明确返回未测，绝不拿
        # 无窗口数据冒充三窗口数据。
        try:
            from mhxy.core import window as win_mod
            cfg = cfg_mod.load_config()
            found = win_mod.locate_all(cfg.get("window_title", "梦幻西游"), include_minimized=False)
            if len(found) != 3:
                result["status"] = "unavailable"
                result["error"] = f"检测到 {len(found)} 个目标窗口，需要恰好 3 个"
                result_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
                return 0
        except Exception as exc:
            result["status"] = "unavailable"
            result["error"] = f"无法枚举目标窗口: {type(exc).__name__}: {exc}"
            result_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
            return 0

    from mhxy.gui.app import App
    stall = {"last": time.perf_counter(), "max_ms": 0.0}
    pulse_alive = {"value": True}

    def pulse() -> None:
        now = time.perf_counter()
        gap = now - stall["last"] - 0.005
        if gap > stall["max_ms"] / 1000:
            stall["max_ms"] = max(0.0, gap * 1000)
        stall["last"] = now
        if pulse_alive["value"]:
            try:
                root.after(5, pulse)
            except Exception:
                pass

    def record(name: str, fn) -> None:
        begin = time.perf_counter()
        fn()
        elapsed = (time.perf_counter() - begin) * 1000
        result["actions"][name] = {
            "duration_ms": round(elapsed, 2),
            "memory_mb": _working_set_mb(),
        }

    root = None
    compact = None
    try:
        root = App(compact=False)
        root.after(5, pulse)
        _wait(root, 0.08)
        result["ready_at"] = time.perf_counter()
        result["ready_ms_from_process_start"] = round((time.perf_counter() - started) * 1000, 2)
        result["actions"]["cold_start_to_operable"] = {
            "duration_ms": result["ready_ms_from_process_start"],
            "memory_mb": _working_set_mb(),
        }

        record("first_page_open", lambda: (root._show("sniper"), _wait(root)))

        # 预热所有页面后，再测纯切页；页面本身的首次构造归入 first_page/展开测量。
        for key in root.PAGE_CLASSES:
            root._show(key)
        _wait(root, 0.05)
        switch_keys = ["general", "sniper", "treasure_map", "escort", "secret_realm",
                       "dungeon", "freeclick", "settings", "about"]

        def repeat_switch() -> None:
            for _ in range(5):
                for key in switch_keys:
                    root._show(key)
                    root.update_idletasks()

        record("repeat_page_switch_5_cycles", repeat_switch)

        # compact -> full 是「展开完整界面」的真实入口；单独建小窗，避免污染前面动作。
        root.destroy()
        root = None
        compact = App(compact=True)
        _wait(compact, 0.05)
        root = compact
        record("expand_full_interface", lambda: (root.enter_full(), _wait(root, 0.05)))

        # 测量从悬浮日志窗恢复主界面的耗时；save=False 避免写入配置。
        root.collapse_to_float(auto=False)
        _wait(root, 0.03)
        record("restore_from_floating", lambda: (root.close_float_log(save=False), _wait(root)))
        result["stall_max_ms"] = round(stall["max_ms"], 2)
    except Exception as exc:
        result["status"] = "error"
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        pulse_alive["value"] = False
        try:
            if root is not None:
                root.destroy()
        except Exception:
            pass
        try:
            result_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
    return 0 if result.get("status") == "ok" else 1


def _run_one(mode: str) -> dict[str, object]:
    with tempfile.NamedTemporaryFile(prefix="mhxy-baseline-", suffix=".json", delete=False) as f:
        result_path = Path(f.name)
    cmd = [sys.executable, str(SCRIPT), "--child", mode, "--result", str(result_path)]
    launched = time.perf_counter()
    proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    try:
        proc.wait(timeout=READY_TIMEOUT)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    launch_to_exit = (time.perf_counter() - launched) * 1000
    try:
        data = json.loads(result_path.read_text(encoding="utf-8"))
    except Exception as exc:
        data = {"mode": mode, "status": "error", "error": f"没有收到测量结果: {exc}"}
    finally:
        try:
            result_path.unlink()
        except OSError:
            pass
    data["process_wall_ms"] = round(launch_to_exit, 2)
    if data.get("status") == "ok" and isinstance(data.get("ready_at"), (int, float)):
        # Windows 下 perf_counter 使用系统单调时钟，父子进程可直接相减；这项
        # 包含进程创建、Python 启动、导入与 App 首次事件循环。
        cold_ms = round((float(data["ready_at"]) - launched) * 1000, 2)
        data["process_start_to_operable_ms"] = cold_ms
        try:
            data["actions"]["cold_start_to_operable"]["duration_ms"] = cold_ms
        except Exception:
            pass
    if proc.returncode not in (0, None) and data.get("status") == "ok":
        data["status"] = "error"
        data["error"] = f"测量进程退出码 {proc.returncode}"
    return data


def _aggregate(rows: list[dict[str, object]]) -> dict[str, object]:
    actions = set()
    for row in rows:
        actions.update((row.get("actions") or {}).keys())
    out: dict[str, object] = {}
    for action in sorted(actions):
        values = [float(row["actions"][action]["duration_ms"])
                  for row in rows if action in row.get("actions", {})]
        out[action] = {
            "raw_ms": [round(v, 2) for v in values],
            "median_ms": round(statistics.median(values), 2) if values else None,
            "max_ms": round(max(values), 2) if values else None,
        }
    stalls = [float(row["stall_max_ms"]) for row in rows if row.get("stall_max_ms") is not None]
    out["main_thread_stall"] = {
        "raw_ms": [round(v, 2) for v in stalls],
        "median_ms": round(statistics.median(stalls), 2) if stalls else None,
        "max_ms": round(max(stalls), 2) if stalls else None,
    }
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--child", choices=("no-game", "three-game"))
    parser.add_argument("--result", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "docs/specs/baseline-raw-20261015.json")
    parser.add_argument("--runs", type=int, default=5)
    args = parser.parse_args()
    if args.child:
        if args.result is None:
            raise SystemExit("--child 需要 --result")
        return _child(args.child, args.result)

    rows: dict[str, list[dict[str, object]]] = {"no-game": [], "three-game": []}
    for mode in rows:
        for _ in range(max(1, args.runs + 1)):  # 第一轮是预热，后五轮用于统计
            row = _run_one(mode)
            if row.get("status") == "ok":
                rows[mode].append(row)
            if len(rows[mode]) >= args.runs + 1:
                break
        # 严格要求五次有效样本；三窗口缺少真实窗口时保留未测状态，不补数字。
        if len(rows[mode]) > args.runs:
            rows[mode] = rows[mode][1:]
        else:
            rows[mode] = []

    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "python": sys.version,
        "root": str(ROOT),
        "samples": rows,
        "summary": {mode: _aggregate(values) for mode, values in rows.items()},
        "notes": {
            "three-game": "需要真实的三个目标游戏窗口；测量进程不会伪造窗口。",
            "memory": "每个动作结束时的当前进程工作集（MB）。",
            "stall": "5ms Tk after 心跳观察到的最大超额间隔。",
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
