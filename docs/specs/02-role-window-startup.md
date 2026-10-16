# Spec 02：角色与窗口页、顶部角色条、统一启动入口

## 目标

取消 compact 小窗；无论是否有游戏窗口，都直接打开 Qt 主窗口并显示“角色与窗口”页。

## 修改范围

- `mhxy/qtui/window.py`、新增 `mhxy/qtui/role_window_page.py`、`mhxy/qtui/role_strip.py`。
- `mhxy/gui/window_labels.py` 仅在确有兼容需要时适配，不复制身份识别逻辑。
- 只提供 Qt 入口给后续阶段调用；本阶段不改 `start.py`，不切换默认入口。
- 对应 `tests/test_startup_identity.py`、`tests/test_window_probe.py` 增加 Qt 入口测试。

## 实现

- 顶部每个窗口显示“状态灯＋角色名”；数量与检测到的窗口一致。
- 绿色=窗口存在且可用，黄色=检测中/身份未确认，红色=检测失败；选中状态用边框表示，不复用状态灯。
- 名称未识别显示“号N”；只读 `cached_labels_for/sync_window_labels`。启动时禁止调用 `prime_visible_labels` 或为了显示姓名主动 OCR。
- 后台使用 `locate_all(..., include_minimized=True)`；结果通过 Qt signal 回主线程，状态变化只更新对应角色项。
- 选择窗口仍写现有 `targets`；队长仍按现有 `captain_index` 语义，不能把顶部队长标记直接当配置真相。
- 无窗口时显示启动器路径、档案和启动按钮；不阻塞主窗口。

## 验收

- 有/无游戏窗口都直接进入完整 Qt 主窗口，不出现 compact 窗口。
- 窗口增删、角色名后到、最小化状态不会卡 UI。
- 选择结果与旧 `targets` 结构一致；队长索引测试通过。
- `python -m unittest discover -t . -v` 通过。

## 不做

- 不迁移任务页。
- 不改变账号启动、OCR、窗口激活算法。
