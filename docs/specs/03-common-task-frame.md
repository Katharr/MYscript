# Spec 03：共享任务页框架、日志和运行状态

## 目标

让所有 Qt 任务页共用一套页头、开始/停止、窗口选择、模式状态和日志入口。

## 修改范围

- 新增 `mhxy/qtui/task_page.py`、`mhxy/qtui/log_panel.py`。
- `mhxy/core/runtime.py`：可选增加 `drain_logs(max_events=None)`，默认行为保持原样。
- 不改各任务的业务类和页面旧实现。

## 实现

- `TaskPage` 只提供 UI 复用：读取配置、调用既有 `TaskRunner/RuntimeService`、显示状态。
- `start` 和 `preflight` 整体放后台线程；Qt 主线程只处理 signal、模型和控件。
- 日志每次最多处理固定条数，后续通过 timer 继续；隐藏页不刷新。
- 停止按钮始终可见；状态使用“待机/运行/停止中/已完成/失败/超时/部分完成”。
- 日志默认折叠为底部面板，支持清空，不改变现有日志级别和来源标签。

## 验收

- 假 runner 测试能显示运行、停止和结束状态。
- 连续产生日志不会阻塞按钮和页面切换。
- `RuntimeService` 旧测试全部通过。
- 不出现后台线程直接操作 Qt 控件。
