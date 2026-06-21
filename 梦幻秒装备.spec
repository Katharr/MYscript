# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 打包配置。产出单文件、无黑窗、双击请求管理员的 dist\梦幻秒装备.exe。

为什么这么配（每条都对应一个会让 exe 跑不起来的坑）：
  - collect_all('customtkinter')：customtkinter 自带主题 json + 图片资源，
    默认收集不全，onefile 启动会报缺资源直接崩 —— 必须显式整包收集。
  - hiddenimports 里点名 cv2 / mss / pygetwindow / PIL 及任务模块：
    这些有的是动态 import / 插件注册，PyInstaller 静态分析可能漏，兜底加上。
  - 不打包 templates/ captures/ config.json：它们要在「运行时」生成在 exe 同级
    （见 mhxy/core/config.py 的 DATA_ROOT），打进 exe 反而会和临时目录逻辑打架。
  - uac_admin=True：把「请求管理员」写进 exe manifest，双击即弹 UAC，
    与游戏同为管理员，SendInput 才不会被 UIPI 丢弃（项目硬约束）。
  - console=False：GUI 程序，不要黑窗。
"""

from PyInstaller.utils.hooks import collect_all, collect_submodules

# customtkinter 整包（数据/子模块/隐藏导入）一次收齐
ctk_datas, ctk_binaries, ctk_hidden = collect_all("customtkinter")

hiddenimports = ctk_hidden + [
    "cv2",
    "numpy",
    "mss",
    "pygetwindow",
    "pyautogui",
    "PIL",
    "PIL.Image",
    "PIL.ImageTk",
    # 任务在 mhxy/tasks/__init__.py 里 import 注册，点名兜底
    "mhxy.tasks.sniper",
] + collect_submodules("mhxy")

a = Analysis(
    ["start.py"],
    pathex=[],
    binaries=ctk_binaries,
    datas=ctk_datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="梦幻秒装备",
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
    icon=None,            # 暂无图标，可后续放 .ico 再填
)
