# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 打包配置。产出单文件、无黑窗、双击请求管理员的 dist\梦幻小助手.exe。

为什么这么配（每条都对应一个会让 exe 跑不起来的坑）：
  - PySide6 QtWidgets 的官方 hook 收集 Qt DLL 和平台插件，不收集无关 Qt 模块。
  - hiddenimports 里点名 cv2 / mss / pygetwindow / PIL 及任务模块：
    这些有的是动态 import / 插件注册，PyInstaller 静态分析可能漏，兜底加上。
  - 不打包 templates/ captures/ config.json：它们要在「运行时」生成在 exe 同级
    （见 mhxy/core/config.py 的 DATA_ROOT），打进 exe 反而会和临时目录逻辑打架。
  - uac_admin=True：把「请求管理员」写进 exe manifest，双击即弹 UAC，
    与游戏同为管理员，SendInput 才不会被 UIPI 丢弃（项目硬约束）。
  - console=False：GUI 程序，不要黑窗。
"""

import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# 本 spec 住在 <项目根>\打包工具\ 下。PyInstaller 执行 spec 时注入 SPECPATH=本文件所在目录，
# 故项目根 = 它的父目录。入口与搜索路径都用绝对路径锚定，避免依赖调用时的工作目录。
PROJECT_ROOT = os.path.dirname(SPECPATH)
ENTRY = os.path.join(PROJECT_ROOT, "start.py")

hiddenimports = [
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "cv2",
    "numpy",
    "mss",
    "pygetwindow",
    "pyautogui",
    "PIL",
    "PIL.Image",
    # 任务在 mhxy/tasks/__init__.py 里 import 注册，点名兜底
    "mhxy.tasks.sniper",
] + collect_submodules("mhxy.qtui") + collect_submodules("mhxy.tasks")

a = Analysis(
    [ENTRY],
    pathex=[PROJECT_ROOT],
    binaries=[],
    # OCR 代码是惰性导入，但默认 YAML 和三个 ONNX 模型也必须随程序收集。
    datas=collect_data_files("rapidocr_onnxruntime") + [
        (os.path.join(PROJECT_ROOT, "mhxy", "assets", "app.ico"), "mhxy/assets"),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["mhxy.gui", "customtkinter", "tkinter", "_tkinter", "PIL.ImageTk",
              "PyQt5", "PyQt6", "PySide2", "PySide6.QtQml", "PySide6.QtQuick"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="梦幻小助手",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,        # GUI，无黑窗
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    uac_admin=True,       # 双击即请求管理员（与游戏同级，SendInput 才生效）
    icon=os.path.join(PROJECT_ROOT, "mhxy", "assets", "app.ico"),
)
