"""默认源码/冻结入口、提权分支和发布安全配置的回归。"""

import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import start
from mhxy.core import config as cfg_mod

ROOT = Path(__file__).resolve().parents[1]


class QtDefaultStartTests(unittest.TestCase):
    def test_real_default_entry_with_no_game_and_three_windows(self):
        script = """
import importlib.abc
import json
from pathlib import Path
import sys
class NoTk(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in ('tkinter', 'customtkinter', '_tkinter') or fullname.startswith('mhxy.gui'):
            raise ImportError('旧 UI 不应进入默认启动: ' + fullname)
sys.meta_path.insert(0, NoTk())
from mhxy.core import config as cfg
from mhxy.core import accounts
# 此用例隔离 Qt 启动路径；真实 OCR 模型在发布包诊断中初始化验证。
accounts._get_ocr_engine = lambda: object()
directory, count, mode = Path(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
cfg.CONFIG_PATH = directory / 'config.json'
from mhxy.qtui import window
from mhxy.qtui.role_strip import WindowState
window.scan_windows = lambda cfg: [WindowState(i+1, f'号{i+1}') for i in range(count)]
import start
calls = []
start._deps_present = lambda: True
start._is_admin = lambda: mode == 'admin'
start._elevate = lambda exe: calls.append(exe) or False
start._has_console = lambda: False
start._report_fatal = lambda *args: sys.exit(9)
if mode == 'frozen':
    sys.frozen = True
sys.argv = ['start.py', '--smoke-report', str(directory / 'report.json')]
assert start.main() == 0
assert len(calls) == (1 if mode == 'denied' else 0)
report = json.loads((directory / 'report.json').read_text(encoding='utf-8'))
assert report['status'] == 'ok', report
assert report['windows'] == count, report
assert len(report['pages']) == 12, report
assert all(value for key, value in report['dry_run'].items() if key != 'teaming'), report
assert report['dry_run']['teaming'] == cfg.DEFAULT_CONFIG['tasks']['teaming']['dry_run']
assert not cfg.CONFIG_PATH.exists()
"""
        for count, mode in ((0, "denied"), (3, "admin"), (0, "frozen")):
            with self.subTest(count=count, mode=mode), tempfile.TemporaryDirectory() as directory:
                env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
                env.pop(start._ELEVATED_FLAG, None)
                proc = subprocess.run([sys.executable, "-c", script, directory, str(count), mode],
                                      cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_admin_launch_checks_dependencies_and_uses_windowless_relaunch(self):
        with patch.object(start, "_deps_present", return_value=True), \
             patch.object(start, "_is_admin", return_value=True), \
             patch.object(start, "_elevate") as elevate, \
             patch.object(start, "_has_console", return_value=True), \
             patch.object(start, "_relaunch_windowless", return_value=True) as relaunch, \
             patch.object(start, "_launch_gui") as gui:
            start.main()
        elevate.assert_not_called()
        relaunch.assert_called_once_with()
        gui.assert_not_called()

    def test_frozen_skips_source_only_steps(self):
        with patch.object(sys, "frozen", True, create=True), \
             patch.object(start, "_deps_present", side_effect=AssertionError), \
             patch.object(start, "_elevate", side_effect=AssertionError), \
             patch.object(start, "_relaunch_windowless", side_effect=AssertionError), \
             patch.object(start, "_launch_gui", return_value=7):
            self.assertEqual(start.main(), 7)

    def test_elevation_marker_prevents_another_uac_attempt(self):
        with patch.dict(os.environ, {start._ELEVATED_FLAG: "1"}), \
             patch.object(start, "_deps_present", return_value=True), \
             patch.object(start, "_is_admin", return_value=False), \
             patch.object(start, "_elevate", side_effect=AssertionError), \
             patch.object(start, "_has_console", return_value=False), \
             patch.object(start, "_launch_gui", return_value=0) as gui:
            self.assertEqual(start.main(), 0)
        gui.assert_called_once_with()

    def test_missing_dependencies_cancel_does_not_start_ui(self):
        with patch.object(start, "_deps_present", return_value=False), \
             patch.object(start, "_is_admin", return_value=True), \
             patch("builtins.input", side_effect=["n", ""]), \
             patch.object(start, "_launch_gui") as gui, \
             patch.object(start.subprocess, "call") as install:
            start.main()
        gui.assert_not_called()
        install.assert_not_called()

    def test_fatal_startup_returns_failure_and_restores_exception_hook(self):
        original = sys.excepthook
        with patch("mhxy.qtui.main.main", side_effect=RuntimeError("startup failed")), \
             patch.object(start, "_report_fatal") as report:
            self.assertEqual(start._launch_gui(), 1)
        report.assert_called_once_with("启动 Qt 主窗口")
        self.assertIs(sys.excepthook, original)

    def test_fatal_log_is_written_in_data_directory(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(start, "BASE", directory), \
             patch.object(start.ctypes.windll.user32, "MessageBoxW"):
            try:
                raise RuntimeError("startup diagnostic")
            except RuntimeError:
                start._report_fatal("Qt")
            log = (Path(directory) / "startup_error.log").read_text(encoding="utf-8")
            self.assertIn("阶段：Qt", log)
            self.assertIn("RuntimeError: startup diagnostic", log)

    def test_elevation_and_pythonw_preserve_arguments(self):
        args = ["start.py", "--smoke-report", r"C:\dir with spaces\report.json"]
        with patch.object(sys, "argv", args), patch.object(start.os.path, "exists", return_value=True), \
             patch.object(start.subprocess, "Popen") as popen:
            self.assertTrue(start._relaunch_windowless())
            self.assertEqual(popen.call_args.args[0][2:], args[1:])
        with patch.object(sys, "argv", args), patch.dict(os.environ), \
             patch.object(start.ctypes.windll.shell32, "ShellExecuteW", return_value=33) as execute:
            self.assertTrue(start._elevate(sys.executable))
            self.assertEqual(execute.call_args.args[3], subprocess.list2cmdline([str(ROOT / "start.py"), *args[1:]]))

    def test_runtime_dependency_probe_requires_qt_and_not_tk(self):
        with patch.object(start.importlib.util, "find_spec", side_effect=lambda name: None if name == "PySide6" else object()):
            self.assertEqual(start._missing_dep_modules(), ["PySide6"])
        self.assertNotIn("customtkinter", start._DEP_MODULES)


class QtReleaseConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("mhxy_release_builder", ROOT / "打包工具/build.py")
        cls.builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.builder)

    def test_release_forces_every_task_to_dry_run_without_changing_source(self):
        cfg = copy.deepcopy(cfg_mod.DEFAULT_CONFIG)
        cfg["tasks"]["sniper"].update(dry_run=False, templates={"sniper_product": "templates/example.png"})
        cfg["tasks"].update(extra={"dry_run": 0}, missing={"regions": {"sample": [1, 2, 3, 4]}})
        before = copy.deepcopy(cfg)
        with tempfile.TemporaryDirectory() as directory, patch.object(cfg_mod, "load_config", return_value=cfg):
            path = Path(directory) / "config.json"
            self.assertTrue(self.builder._write_packaged_config(path))
            published = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(cfg, before)
        for name, tc in published["tasks"].items():
            self.assertIs(tc["dry_run"], True, name)
            self.assertEqual({k: v for k, v in tc.items() if k != "dry_run"},
                             {k: v for k, v in before["tasks"][name].items() if k != "dry_run"})

    def test_release_config_write_failure_is_reported(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(cfg_mod, "load_config", return_value=copy.deepcopy(cfg_mod.DEFAULT_CONFIG)):
            self.assertFalse(self.builder._write_packaged_config(Path(directory)))

    def test_build_refuses_missing_runtime_before_removing_old_artifacts(self):
        with patch.object(start, "_missing_dep_modules", return_value=["rapidocr_onnxruntime"]), \
             patch.object(self.builder.shutil, "rmtree") as remove, \
             patch.object(self.builder.subprocess, "call") as build:
            self.assertEqual(self.builder.run(), 1)
        remove.assert_not_called()
        build.assert_not_called()
