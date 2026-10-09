# -*- coding: utf-8 -*-
"""测试包护栏：整个测试会话都不许碰真实 config.json。

背景（已踩坑）：曾有测试忘记 mock `cfg_mod.save_config`，直接把用户 config.json 覆盖成测试数据，
用户的标定坐标与角色档案被清空、当时无处可恢复。故在导入测试包时就把 `core.config.CONFIG_PATH`
改指到临时目录，并将快照/拒写留档目录一同隔离：即使某个测试忘了 mock，写坏的
也只是临时文件，不会在真实资产目录留下测试数据。

注意：本护栏只在 `tests` 被当作包导入时生效（如 `python -m unittest discover -t .` 或
`python -m unittest tests.test_xxx`）。若用 `python -m unittest discover -s tests`，unittest 会把
tests 目录当顶层目录、不导入本模块——那种跑法请自行给测试加 mock（见 test_inventory_items_dialog）。
此外 `core.config.save_config` 仍保留世代快照与原子写护栏。
"""

import atexit
import shutil
import tempfile
from pathlib import Path

from mhxy.core import config as _config

_TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="mhxy_tests_"))
_config.CONFIG_PATH = _TEST_DATA_DIR / "config.json"
_config.CONFIG_BACKUP_DIR = _TEST_DATA_DIR / "config_backups"
atexit.register(shutil.rmtree, _TEST_DATA_DIR, ignore_errors=True)
