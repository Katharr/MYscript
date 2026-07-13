---
description: 在 MYscript 项目中添加一个全新任务模块。创建 Task 子类、注册到 __init__.py、添加 GUI 页面和导航。
---

# Add New Task

在 MYscript 项目中添加一个新功能（任务）。按照架构约定，需要修改 3-4 个文件。

## 工作目录

`/Users/diaoff/code/vibe/MYscript`

## 步骤

### 1. 创建任务模块 `mhxy/tasks/$NAME.py`

参考现有任务（如 `taohaiqu.py` 或 `secret_realm.py`）的结构：

```python
# -*- coding: utf-8 -*-
"""$NAME 任务。"""

import time
from ..core import vision
from ..core import window as win_mod
from ..core import rotation
from ..core import scan
from .base import Task, register


@register
class $NameTask(Task):
    name = "$NAME"
    title = "$TITLE"
    description = "$DESCRIPTION"
    CHAINS_PER_WINDOW = True   # 是否可做「日常一条龙·每窗口独立链」
    _FLAG_KEYS = [...]         # 标定模板键列表

    CALIBRATION = {
        "regions": [...],
        "templates": [...],
        "watchlist": False,
    }

    def preflight(self, ctx):
        # 检查标定/模板/快捷键是否就绪
        problems = []
        return (len(problems) == 0), problems

    def run(self, ctx):
        # 主流程（后台线程，勤查 ctx.should_stop()）
        ...
```

**关键约束：**
- 任务在后台线程跑，通过 `ctx.log(msg, level)` 输出
- 循环里勤查 `ctx.should_stop()`
- config 用 `tasks.<name>.*` 命名空间
- 多开轮转用 `base.Task._make_rotation()`
- 实现 `make_chain_driver()` 方法支持一条龙（每日任务链）

### 2. 注册到 `mhxy/tasks/__init__.py`

在文件末尾添加：

```python
from . import $NAME  # noqa: F401,E402
```

### 3. 添加 GUI 页面（新文件或追加到 `mhxy/gui/app.py`）

在 `mhxy/gui/app.py` 中添加一个新的 Page 类：

```python
class $NamePage(ctk.CTkFrame):
    TASK_NAME = "$NAME"
    LOG_SOURCE = "$TITLE"

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.fonts = app.fonts
        self.runner = None
        ...
```

**日志输出约束**：页面内用统一转发
```python
self.app.log_line(msg, level, getattr(self, "LOG_SOURCE", None))
```

### 4. 添加导航

在 `App.NAV` 列表中添加：

```python
("$NAME", "$NAV_LABEL"),
```

如果任务可运行（有 runner/pump），同时加入 `App.RUNNABLE_KEYS`。

如果任务有握手页面（需要 `update_game_pill` 方法），仿照 `SniperPage` 实现。

## 参考

- 现有 9 个任务模块见 `mhxy/tasks/`，注册表见 `mhxy/tasks/__init__.py`
- GUI 页面参考 `SniperPage`（L179）或 `EscortPage`（L746）
- 多开轮转参考 `base.Task._make_rotation()` 和 `mhxy/core/rotation.py`
- 通用「翻列表/翻包裹」能力见 `mhxy/core/scan.py`
- 详见 `CLAUDE.md`「任务模块约定」和「加新任务示例」