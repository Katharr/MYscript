# -*- coding: utf-8 -*-
"""
一键打包脚本。运行：python 打包工具\build.py（或双击 打包工具\打包.bat）

会调用 PyInstaller 按「梦幻秒装备.spec」打包，产出单文件 exe，
并整理出可直接分发的交付文件夹（干净平铺三件套）：
    发布\梦幻秒装备_v<版本>\
        梦幻秒装备.exe      <- 单文件 exe（是文件，不是文件夹）
        config.json         <- 项目真实配置（保留全部标定，已链接 templates；dry_run 统一置演练）
        templates/          <- 随包模板图（标定/识别要用，整目录拷过去）

为什么这么组织路径（每条都对应一个会让打包失败/产物错位的坑）：
  - 本脚本现在住在「打包工具\」子目录里，但项目根才是 PyInstaller 的工作目录。
    故 PROJECT_ROOT = 本文件父目录的父目录；spec / dist / build / 发布 全部锚定到 PROJECT_ROOT，
    且用 cwd=PROJECT_ROOT 调 PyInstaller，让 spec 里 start.py 的相对路径成立。
  - 整理交付目录时「先 rmtree 整个目录、再重建」：根除老 bug ——
    若 交付目录\梦幻秒装备.exe 这个名字上次残留成了一个「文件夹」，shutil.copy2 会把 exe
    塞进那个同名文件夹里形成套娃。推倒重建保证 dest 永远是干净的文件路径。

数据里只有 config.json + templates 随包；captures（命中截图）运行时才生成在 exe 同级。
"""

import os
import sys
import shutil
import subprocess

# 控制台多为 GBK，print 中文/符号可能抛 UnicodeEncodeError。强制 UTF-8 输出兜底。
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# 本文件在 <项目根>\打包工具\build.py —— 项目根是父目录的父目录
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)

SPEC = os.path.join(SCRIPT_DIR, "梦幻秒装备.spec")
DIST = os.path.join(PROJECT_ROOT, "dist")
BUILD = os.path.join(PROJECT_ROOT, "build")
TEMPLATES_SRC = os.path.join(PROJECT_ROOT, "templates")
EXE_NAME = "梦幻秒装备.exe"


def _release_dir():
    """交付目录名从 mhxy.__version__ 派生（如 2.2.0 -> 发布\梦幻秒装备_v2.2），防止版本漂移。"""
    ver = "2.0"
    try:
        if PROJECT_ROOT not in sys.path:
            sys.path.insert(0, PROJECT_ROOT)
        import mhxy
        parts = mhxy.__version__.split(".")
        ver = ".".join(parts[:2]) if len(parts) >= 2 else mhxy.__version__
    except Exception as e:
        print(f"  ! 读 mhxy.__version__ 失败，交付目录退回 v{ver}：{e}")
    return os.path.join(PROJECT_ROOT, "发布", f"梦幻秒装备_v{ver}")


def run():
    if not os.path.exists(SPEC):
        print("找不到 梦幻秒装备.spec，无法打包。期望路径：", SPEC)
        return 1

    # 清掉上次产物，避免旧文件混入
    for d in (DIST, BUILD):
        if os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)

    print("开始打包（PyInstaller）……这一步可能要一两分钟。\n")
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
           "--distpath", DIST, "--workpath", BUILD, SPEC]
    ret = subprocess.call(cmd, cwd=PROJECT_ROOT)
    if ret != 0:
        print("\n打包失败（PyInstaller 返回非 0）。请看上面的报错。")
        return ret

    exe_path = os.path.join(DIST, EXE_NAME)
    if not os.path.exists(exe_path):
        print("\n打包进程结束但没找到 exe：", exe_path)
        return 1

    release_dir = _release_dir()

    # —— 整理交付目录：先推倒重建，保证干净平铺、根除同名文件夹套娃 ——
    if os.path.exists(release_dir):
        shutil.rmtree(release_dir, ignore_errors=True)
    os.makedirs(release_dir, exist_ok=True)

    # 1) exe（dest 此刻必为干净文件路径，copy2 不会塞进文件夹）
    dest_exe = os.path.join(release_dir, EXE_NAME)
    shutil.copy2(exe_path, dest_exe)

    # 2) 发布配置 config.json（以项目真实配置为准，保留全部标定→templates 才真正链接到功能）
    _write_packaged_config(os.path.join(release_dir, "config.json"))

    # 3) templates\ 整目录随包（标定/识别要用）
    dest_templates = os.path.join(release_dir, "templates")
    if os.path.isdir(TEMPLATES_SRC):
        shutil.copytree(TEMPLATES_SRC, dest_templates)
        n = sum(len(fs) for _, _, fs in os.walk(dest_templates))
        print(f"  [OK] 已随包 templates（{n} 个文件）：{dest_templates}")
    else:
        print(f"  ! 没找到 templates 源目录，跳过：{TEMPLATES_SRC}")

    size_mb = os.path.getsize(dest_exe) / 1024 / 1024
    print("\n[OK] 打包完成！")
    print(f"   exe        : {dest_exe}  ({size_mb:.1f} MB)")
    print(f"   交付文件夹 : {release_dir}")
    print(f"\n把整个『{os.path.basename(release_dir)}』文件夹发给用户即可（exe + config.json + templates\\）。")
    print("首次双击 exe（弹 UAC 点是）；同目录会自动生成 captures（命中截图）。")
    return 0


def _write_packaged_config(path):
    """在发布目录写 config.json。

    以「项目根真实 config.json」为准（经 load_config 并入 DEFAULT_CONFIG 缺省项），
    保留全部标定 —— regions 坐标 / 各任务 templates 的相对路径 / sniper.watchlist /
    organize_bag.items / teaming 队长ID 历史。只有这样，随包的 templates\\ 图片才真正
    「链接到软件功能」上（运行时 vision._abspath 把 'templates/xxx.png' 拼到 exe 同级解析）。
    —— 这正是上一版只写空 DEFAULT_CONFIG 导致「图片还在但标定全被重置」的根因，现已修。

    安全起见，按项目硬约束「交付默认演练」把所有任务的 dry_run 统一置 True
    （标定保留不动，用户进 GUI 一键开实战即可）。读不到真实配置时退回 DEFAULT_CONFIG。
    交付目录已被 rmtree 推倒重建，这里必是全新写入。"""
    import json
    import copy
    if PROJECT_ROOT not in sys.path:
        sys.path.insert(0, PROJECT_ROOT)
    try:
        from mhxy.core.config import load_config, DEFAULT_CONFIG
    except Exception as e:
        print(f"  ! 生成 config.json 失败（导入 config 出错）：{e}")
        return
    try:
        # build.py 非 frozen 运行 -> config.DATA_ROOT=项目根 -> load_config 读的就是项目根 config.json
        cfg = load_config()
    except Exception as e:
        print(f"  ! 读真实 config.json 失败，退回 DEFAULT_CONFIG：{e}")
        cfg = copy.deepcopy(DEFAULT_CONFIG)

    # 交付默认演练：把各任务 dry_run 强制为 True（只动这一项，标定原样保留）
    coerced = []
    for name, tcfg in (cfg.get("tasks") or {}).items():
        if isinstance(tcfg, dict) and tcfg.get("dry_run") is False:
            tcfg["dry_run"] = True
            coerced.append(name)

    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        print(f"  [OK] 已写入发布配置（保留标定，已链接 templates）：{path}")
        if coerced:
            print(f"       安全：以下任务 dry_run 已置演练（用户自行开实战）：{', '.join(coerced)}")
    except OSError as e:
        print(f"  ! 写 config.json 失败：{e}")


if __name__ == "__main__":
    sys.exit(run())
