# Spec 01：建立 PySide6 主窗口壳

## 目标

增加可独立运行的 Qt 主窗口，但不切换默认启动入口。

## 修改范围

- `requirements.txt`：加入 `PySide6`。
- 新增 `mhxy/qtui/`：`main.py`、`window.py`、`theme.py`。
- 可新增 `tests/test_qt_shell.py`，只测无游戏窗口的 UI 壳。

## 实现

- `QApplication` 只创建一次。
- `QMainWindow` 使用左侧窄导航、中间内容区、底部状态条；先放占位页。
- 统一颜色、间距、字体和按钮尺寸；不要复制每个旧页面的样式。
- 页面切换用 `QStackedWidget`，当前页按需创建并缓存。
- 使用 `QSettings` 只保存窗口位置/大小时，仍不得写任务配置；任务配置继续走 `core.config.save_config`。

## 验收

- `python -m mhxy.qtui.main` 能打开、切换占位页、关闭。
- 同一进程只存在一个 `QApplication`，没有 Tk root。
- 无游戏窗口也能打开。
- `python -m unittest discover -t . -v` 通过。

## 不做

- 不导入旧 `mhxy.gui.app.App`。
- 不改 `start.py` 默认入口。
- 不实现角色、任务、日志功能。
