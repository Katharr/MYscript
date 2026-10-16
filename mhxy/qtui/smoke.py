"""显式启动诊断：实际创建全部 Qt 页，不启动任务、不写配置和用户窗口偏好。"""

import json
from pathlib import Path
import sys
import tempfile
import time

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QMainWindow

from ..core import config as cfg_mod
from .window import MainWindow


def run_smoke(app, report_path):
    from start import _is_admin

    started = time.monotonic()
    report = {"status": "error", "frozen": bool(getattr(sys, "frozen", False)),
              "admin": _is_admin(), "data_root": str(cfg_mod.DATA_ROOT)}
    previous_save = cfg_mod.save_config

    def reject_write(*args, **kwargs):
        raise RuntimeError("启动诊断禁止写配置")

    def finish(error=None):
        try:
            if error:
                raise RuntimeError(error)
            assert window.isVisible() and window.current_page == "role_window"
            assert len([w for w in app.topLevelWidgets() if isinstance(w, QMainWindow)]) == 1
            for key, _ in window.NAV:
                window.show_page(key)
            assert not {"tkinter", "customtkinter", "mhxy.gui.app"}.intersection(sys.modules)
            from ..core import accounts
            if accounts._get_ocr_engine() is None:
                raise RuntimeError("OCR 资源不可用：" + accounts.ocr_status())
            report.update(status="ok", windows=len(window.window_states), pages=list(window.pages),
                          ocr_ready=True,
                          config_exists=cfg_mod.CONFIG_PATH.is_file(),
                          templates_exists=cfg_mod.TEMPLATES_DIR.is_dir(),
                          dry_run={k: v.get("dry_run", True) for k, v in window.cfg["tasks"].items()
                                   if isinstance(v, dict)})
        except Exception as exc:
            report["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            window.close()
            Path(report_path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            app.exit(0 if report["status"] == "ok" else 1)

    def check_scan():
        if window._scanning:
            if time.monotonic() - started > 15:
                finish("窗口枚举超过 15 秒")
            else:
                QTimer.singleShot(20, check_scan)
        else:
            finish()

    with tempfile.TemporaryDirectory(prefix="mhxy_qt_smoke_") as directory:
        cfg_mod.save_config = reject_write
        try:
            settings = QSettings(str(Path(directory) / "ui.ini"), QSettings.Format.IniFormat)
            window = MainWindow(settings=settings)
            window.scan_timer.stop()
            window.show()
            QTimer.singleShot(100, check_scan)
            return app.exec()
        finally:
            cfg_mod.save_config = previous_save
