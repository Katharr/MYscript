# Qt UI 迁移 Specs

目标：把现有 CustomTkinter 界面迁移为 PySide6 Qt Widgets，保留现有 Python 任务、识别、输入和配置行为。

## 执行顺序

1. `00-baseline.md`
2. `01-qt-shell.md`
3. `02-role-window-startup.md`
4. `03-common-task-frame.md`
5. `04-daily-page.md`
6. `05-activity-pages.md`
7. `06-sniper-page.md`
8. `07-freeclick-inventory.md`
9. `08-dungeon-page.md`
10. `09-calibration.md`
11. `10-settings-log-float.md`
12. `11-switch-default.md`

每个 spec 都假定在独立新会话执行。完成一份后运行该份的验收命令并提交，再进入下一份。

## 共同约束

- 后续所有阶段的 Git 提交信息统一使用中文。
- 只用 PySide6 Qt Widgets；不引入 QML、Tauri、React、Rust 或新的服务端。
- 不重写 `mhxy/core` 的任务、识别、输入、配置和窗口业务。
- 不新建复杂领域层、事件总线、插件框架或通用状态管理库。
- UI 只保留一个小桥接对象：Qt 主线程更新控件，后台结果通过 Qt signal/slot 回主线程。
- 现有 `config.json`、模板路径、任务名、字段名和安全默认值保持兼容。
- 每阶段以列出的文件为主要修改范围。为完成该阶段目标，可直接补充必要的 UI 入口接线、调用方适配、配置刷新、回归测试和文档更新，无需因清单漏列这些配套文件而暂停；在阶段文档或完成记录中说明补充文件及原因。
- 配套修改应保持最小范围，不提前实现后续阶段、不顺手重构无关模块。若必须改变核心业务、配置兼容性、安全默认值或本阶段明确的“不做”事项，先说明具体冲突并征求用户意见。
- 默认 `dry_run=true`、急停快捷键、失控角、窗口激活校验和多开轮转行为不能改变。

## 统一验收

```powershell
python -m unittest discover -t . -v
```

测试命令必须从项目根目录运行。不要使用 `discover -s tests`，以免绕过测试包的真实配置隔离。

## 阶段完成记录

| 阶段 | 完成内容 | 验收结果 | 提交 |
| --- | --- | --- | --- |
| spec8：`08-dungeon-page.md` | 完成 Qt 刷副本页迁移，接入副本注册表、队长选择、已组队、跑完解散和队长 ID 历史库；复用原有副本任务与组队流程 | `python -m unittest discover -t . -v`：507 项通过 | `0f3400a`：完成 Qt 刷副本页迁移 |
| spec9：`09-calibration.md` | 完成 Qt 标定中心、尺寸组、异步缩略图和冻结屏幕 ROI 框选；任务页、装备与物品入口接通；复用现有保存与回滚，修订必要配套修改的范围规则 | `python -m unittest discover -t . -v`：529 项通过（新增 22 项 Qt 标定测试） | `00a027d`：完成 Qt 标定中心与框选浮层迁移并修订阶段范围规则 |
