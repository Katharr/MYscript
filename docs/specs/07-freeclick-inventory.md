# Spec 07：迁移自由点击和背包管理

## 目标

迁移自由点击页、物品管理对话框和通用页中的背包入口，保持资产事务安全。

## 修改范围

- 新增 `mhxy/qtui/freeclick_page.py`、`inventory_items_dialog.py`、`inventory_panel.py`。
- 复用 `core.inventory`、`core.inventory_maintenance`、现有配置字段和截图事务。
- 不改自由点击步骤数据格式和物品动作定义。

## 实现

- 自由点击步骤使用 model/view；编辑只更新对应行，不重建整表。
- 物品动作必须完整保留 `use/discard/shop_sell/stall_sell`，标签从 `ACTION_LABELS` 读取。
- 物品缩略图、截图和保存操作使用现有事务边界；失败可回滚并提示。
- 自动整理、演练/实战和多窗口选择仍调用现有入口。

## 验收

- 新增、编辑、排序、删除步骤可保存并重载。
- 四种物品动作均可配置；截图资产失败不会写入半成品配置。
- `test_freeclick_assets.py`、`test_inventory_*` 和全量测试通过。

## 不做

- 不把背包规则简化为三种动作。
- 不在 UI 层复制物品整理算法。
