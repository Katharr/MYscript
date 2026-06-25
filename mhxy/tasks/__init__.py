# -*- coding: utf-8 -*-
"""
任务层。每个功能=一个 Task 子类（见 base.py），在这里注册后即可被 GUI 自动列出。
以后加“自动刷副本/师门/抓鬼”，新建一个文件、写个 Task 子类、在下面 import 注册即可。
"""

from .base import Task, register, get_task, all_tasks  # noqa: F401

# —— 在此注册所有任务（import 即触发注册）——
from . import sniper         # noqa: F401,E402
from . import treasure_map   # noqa: F401,E402
from . import escort         # noqa: F401,E402
from . import secret_realm   # noqa: F401,E402
from . import dungeon        # noqa: F401,E402
from . import taohaiqu       # noqa: F401,E402
from . import organize_bag   # noqa: F401,E402
from . import daily          # noqa: F401,E402
