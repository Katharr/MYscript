# Spec 08：迁移刷副本页

## 目标

迁移副本页，保留副本选择、队长、已组队和跑完解散语义。

## 修改范围

- 新增 `mhxy/qtui/dungeon_page.py`、必要的队长 ID 选择控件。
- 复用 `tasks/dungeon.py`、`core.teaming`、`leader_history.py`。
- 不改副本任务和组队流程。

## 实现

- 副本选择从现有注册表读取。
- 队长显示使用副本命名空间已有 `captain_index`；通用组队配置仍独立。
- “已组队”“跑完解散队伍”原字段和默认值不变。
- 队长 ID 库显示当前项和历史项，写入现有历史结构。

## 验收

- 未选窗口、缺组队标定、已组队三种状态提示准确。
- 运行、停止、解散和队长切换行为与旧测试一致。
- `test_dungeon_outcome.py`、`test_leader_history.py` 和全量测试通过。
