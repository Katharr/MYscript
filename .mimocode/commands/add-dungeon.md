---
description: 在 MYscript 项目中添加一个新副本任务。只需写 is_dungeon=True 的 Task，GUI 自动列进下拉。
---

# Add New Dungeon

在 MYscript 项目中添加一个新副本。副本自动出现在「刷副本」页的「选择副本」下拉中，无需改 GUI。

## 工作目录

`/Users/diaoff/code/vibe/MYscript`

## 步骤

### 1. 创建副本模块 `mhxy/tasks/$NAME.py`

参考 `taohaiqu.py` 的结构：

```python
# -*- coding: utf-8 -*-
"""$NAME 副本任务。"""

import time
from ..core import vision
from ..core import window as win_mod
from ..core import scan
from ..core.teaming import TeamFormation
from .base import Task, register


@register
class $NameTask(Task):
    name = "$NAME"
    title = "$TITLE"
    description = "$DESCRIPTION"
    is_dungeon = True        # ★ 关键：标记为副本，自动出现在下拉
    _FLAG_KEYS = [...]

    CALIBRATION = {
        "regions": [...],
        "templates": [...],
        "watchlist": False,
    }

    def preflight(self, ctx):
        problems = []
        return (len(problems) == 0), problems

    def run(self, ctx):
        # 主流程：队长跑完整剧情
        ...
```

**关键约定：**
- `is_dungeon = True` 让 `base.dungeon_tasks()` 自动发现它
- `preflight` 中可用 `skip_team` 判断是否已自行组好队
- 副本配置存在 `tasks.<name>.*` 命名空间，不共享
- `tasks.dungeon.selected` 存当前选中的副本名

### 2. 注册到 `mhxy/tasks/__init__.py`

在文件末尾添加：

```python
from . import $NAME  # noqa: F401,E402
```

### 3. 无需改 GUI

`dungeon_tasks()` 自动把它列进「刷副本」页的下拉选择框。

## 副本中枢约定

- 选谁跑谁存在 `tasks.dungeon.selected`
- 每个副本自己的标定/队长/演练/已组队开关都在该副本自己的命名空间 `tasks.<name>.*`
- 中枢页通过 `task_config(selected)` 代理读写

## 参考

- `taohaiqu.py` 是最完整的副本参考
- 组队共享能力见 `mhxy/core/teaming.TeamFormation`
- 解散队伍见 `mhxy/core/teaming.run_disband()`
- 详见 `CLAUDE.md`「副本中枢」和「加新副本只需写个 is_dungeon = True 的 Task」