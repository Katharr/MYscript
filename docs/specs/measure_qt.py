"""Spec11 性能复测；复用 Spec00 的独立进程、工作集和汇总方法，不改旧基线。

python docs/specs/measure_qt.py
python docs/specs/measure_qt.py --legacy-three  # 同配置重采 Tk 三窗口对照
"""

import argparse
import ctypes
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import statistics
import sys
import tempfile
import time

import measure_baseline as baseline

ROOT = baseline.ROOT


def peak_working_set_mb():
    class Counters(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("faults", ctypes.c_ulong),
                    ("peak", ctypes.c_size_t), ("current", ctypes.c_size_t),
                    ("paged_peak", ctypes.c_size_t), ("paged", ctypes.c_size_t),
                    ("nonpaged_peak", ctypes.c_size_t), ("nonpaged", ctypes.c_size_t),
                    ("pagefile", ctypes.c_size_t), ("pagefile_peak", ctypes.c_size_t)]
    kernel = ctypes.WinDLL("kernel32")
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    psapi = ctypes.WinDLL("psapi")
    psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong]
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        raise OSError("GetProcessMemoryInfo 失败")
    return round(counters.peak / 1024**2, 2)


def percentile95(values):
    return sorted(values)[math.ceil(len(values) * .95) - 1]


def child(mode, result_path):
    started = time.perf_counter()
    from mhxy.core import config as cfg_mod
    cfg_mod.save_config = lambda *a, **k: None
    cfg_mod.snapshot_config = lambda: None
    result = {"mode": mode, "status": "ok", "actions": {}}
    # 父进程检查场景；子进程从未加载业务库开始计时，后台仍枚举真实窗口。
    from start import _set_dpi_aware_early
    _set_dpi_aware_early()
    from PySide6.QtCore import QEventLoop, QSettings, QTimer
    from mhxy.qtui.main import get_application
    from mhxy.qtui.window import MainWindow
    app = get_application(["qt-performance"])

    def wait(seconds=.03):
        loop = QEventLoop()
        QTimer.singleShot(round(seconds * 1000), loop.quit)
        loop.exec()

    stall = {"last": time.perf_counter(), "max": 0}

    def pulse():
        now = time.perf_counter()
        stall["max"] = max(stall["max"], (now - stall["last"] - .005) * 1000)
        stall["last"] = now

    heartbeat = QTimer()
    heartbeat.setInterval(5)
    heartbeat.timeout.connect(pulse)
    heartbeat.start()

    def record(name, fn):
        begin = time.perf_counter()
        fn()
        result["actions"][name] = {"duration_ms": round((time.perf_counter() - begin) * 1000, 2),
                                   "memory_mb": baseline._working_set_mb()}

    window = None
    try:
        with tempfile.TemporaryDirectory(prefix="mhxy_qt_perf_") as directory:
            settings = QSettings(str(Path(directory) / "ui.ini"), QSettings.Format.IniFormat)
            window = MainWindow(settings=settings)
            window.show()
            wait(.08)
            result["ready_at"] = time.perf_counter()
            result["actions"]["cold_start_to_operable"] = {
                "duration_ms": round((result["ready_at"] - started) * 1000, 2),
                "memory_mb": baseline._working_set_mb()}
            # 与旧脚本一样从 general 页开始；Qt 默认角色页的计时另见 cold_start。
            window.show_page("general")
            wait()
            record("first_page_open", lambda: (window.show_page("sniper"), wait()))
            page_opens = {}
            for key, _ in window.NAV:
                begin = time.perf_counter()
                window.show_page(key)
                app.processEvents()
                page_opens[key] = round((time.perf_counter() - begin) * 1000, 3)
            result["page_open_raw_ms"] = page_opens
            wait(.05)
            keys = ["general", "sniper", "treasure_map", "escort", "secret_realm",
                    "dungeon", "freeclick", "settings", "about"]
            latencies = []

            def repeat():
                for _ in range(5):
                    for key in keys:
                        begin = time.perf_counter()
                        window.show_page(key)
                        app.processEvents()
                        latencies.append(round((time.perf_counter() - begin) * 1000, 3))

            record("repeat_page_switch_5_cycles", repeat)
            result["switch_raw_ms"] = latencies
            result["switch_p95_ms"] = percentile95(latencies)
            result["expand_full_interface"] = "not_applicable: Qt 启动即完整界面，没有 compact"
            window.collapse_to_float()
            wait()
            record("restore_from_floating", lambda: (window.restore_from_float(), wait()))
            wait()
            result["stall_max_ms"] = round(stall["max"], 2)
            result["peak_working_set_mb"] = peak_working_set_mb()
            result["window_count"] = len(window.window_states)
            if result["window_count"] != (3 if mode == "three-game" else 0):
                raise RuntimeError("测量期间真实窗口数量发生变化")
            window.close()
            window = None
    except Exception as exc:
        result.update(status="error", error=f"{type(exc).__name__}: {exc}")
    finally:
        heartbeat.stop()
        if window is not None:
            window.close()
        result_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return 0 if result["status"] == "ok" else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--child", choices=("no-game", "three-game"))
    parser.add_argument("--result", type=Path)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--legacy-three", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "docs/specs/qt-raw-20261016.json")
    args = parser.parse_args()
    if args.child:
        if os.environ.get("MHXY_PERF_LEGACY") == "1":
            code = baseline._child(args.child, args.result)
            result = json.loads(args.result.read_text(encoding="utf-8"))
            result["peak_working_set_mb"] = peak_working_set_mb()
            args.result.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
            return code
        return child(args.child, args.result)
    from mhxy.core import config as cfg_mod, window as win_mod
    cfg = cfg_mod.load_config()
    win_mod.set_game_process(cfg.get("window_process"))
    wins = win_mod.locate_all(cfg.get("window_title", "梦幻西游"), cfg.get("window_offset", [0, 0]),
                              include_minimized=True)
    payload = {"generated_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
               "python": sys.version, "platform": platform.platform(),
               "engine": "Tk" if args.legacy_three else "Qt",
               "config_sha256": hashlib.sha256(cfg_mod.CONFIG_PATH.read_bytes()).hexdigest(),
               "actual_window_count": len(wins), "samples": {}, "summary": {}}
    modes = ["three-game"] if args.legacy_three else ["no-game", "three-game"]
    baseline.SCRIPT = Path(__file__).resolve()
    if args.legacy_three:
        os.environ["MHXY_PERF_LEGACY"] = "1"
    for mode in modes:
        expected = 3 if mode == "three-game" else 0
        if len(wins) != expected:
            payload["samples"][mode] = {"status": "unavailable", "reason": f"实测 {len(wins)} 窗，需要 {expected} 窗"}
            continue
        rows = [baseline._run_one(mode) for _ in range(args.runs + 1)]
        payload["warmup"] = rows[0]
        payload["samples"][mode] = rows[1:]
        if not all(row["status"] == "ok" for row in rows):
            raise RuntimeError(rows)
        payload["summary"][mode] = baseline._aggregate(rows[1:])
        for name in ("peak_working_set_mb",) if args.legacy_three else ("switch_p95_ms", "peak_working_set_mb"):
            values = [row[name] for row in rows[1:]]
            payload["summary"][mode][name] = {"raw": values, "median": statistics.median(values), "max": max(values)}
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
