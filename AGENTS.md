# AGENTS.md — MYscript (梦幻西游:时空 辅助)

> 详尽约定/架构/踩坑记录在根目录 `CLAUDE.md`，本文件只留**代理最容易漏、且会踩坑**的要点。
> 中文交流；代码/命令/文件名保持英文。

## 这是什么
截屏 + OpenCV 模板匹配 + Windows `SendInput` 拟人化输入，操作《梦幻西游:时空》PC 客户端。
**不读内存、不注入进程**。⚠ 违反游戏 ToS，用户知情、用小号测。

## 关键事实
- **Windows-only**，Python 3.10+，依赖见 `requirements.txt`。入口：`启动.bat` → `start.py`（自动装依赖、提权、起 GUI）。
- 运行时数据：`config.json` / `templates/` / `captures/` 在项目根（exe 时在同级）。
- 任务在**后台线程**跑，用 `ctx.log()` 输出、`ctx.should_stop()` 控停，**绝不直接碰 GUI**。
- 三种急停（GUI 停止 / 热键默认 Ctrl+Alt+F12 / 鼠标甩屏角）都走 `app.stop_all_tasks()`，只能停在两次原子动作之间。

## 架构（三层，包名 `mhxy/`）
```
core/    config, window(定位/截图), vision(匹配), input(SendInput), scan(滚动查找),
         rotation(多开轮转), context, runner(后台线程), teaming(组队握手), inventory(整理背包)
tasks/   base(Task 基类+注册表) + 各任务(sniper/escort/treasure_map/secret_realm/taohaiqu/dungeon/disband/organize_bag/daily)
gui/     app(主窗+导航), theme, calibrate_dialog, roi_overlay(全屏框选+放大镜), leader_gallery, inventory_items_dialog
```
- 新任务：建 `mhxy/tasks/xxx.py` → `@register class XxxTask(Task)` → 在 `tasks/__init__.py` import。
- 新副本：类上加 `is_dungeon = True` 即自动进「刷副本」页下拉，GUI 不用改。
- GUI 新页：在 `gui/app.py` 的 `App.NAV` 加项 + 写继承 `ctk.CTkFrame` 的页类。

## 关键坑（踩过会复发）
1. **`dry_run=true` 安全默认**：只识别不点真实动作。改默认/加动作前先想演练路径。
2. **进程过滤 = `MyGame_x64r.exe`**（`config.window_process` / `window.set_game_process`）。标题会和终端/编辑器撞，靠进程名才稳；客户端 exe 改名只改这两处。
3. **多开轮转铁律**（`core/rotation.py`）：监控态没触发转移时**绝不** `_goto`，否则在一个号上空转盯屏、饿死别号。逐号任务走 `base._make_rotation()`，只传 `step_fn(rec)`。
4. **活动卡片默认两张一排**：`escort/treasure_map/secret_realm` 的「开活动→参加」共用逻辑里，`_find_join_on_row(ctx, list_region, entry_screen_xy, threshold, loop, join_key, entry_key)`（已在 base）**只在条目所属卡片列内**找「参加」，否则会点到右邻卡片。列数改 `tasks.<name>.loop.activity_columns`；调用**必须传** `join_key`/`entry_key`（各自模板键，如 `escort_join`/`escort_entry`）。
5. **模板文件名铁律**：`templates/tm_<key>.png`。**绝不改键名**——改名会让用户已有标定文件失效。模板键是 task 专有的（`escort_`/`flag_`/`sr_`/`thq_`），不要统一成同名。
6. **队长ID 库**：激活图路径恒为 `templates/tm_leader_id.png`；切换队长=字节覆盖该文件，不改 config 路径串。
7. **配置命名空间** `tasks.<name>.*`；跨任务共享：`tasks.teaming` / `tasks.organize_bag` / `tasks.dungeon`。
8. **全局唯一日志面板**（`App.log_line`）：页面设类属性 `LOG_SOURCE` + `_log_line` 转发，**不要**自建日志框。
9. **GUI 文字换行**一律 `theme.bind_wraplength(label)`，**禁止写死 `wraplength=N`**（窗口窄会截断）。改前先读其 docstring 的三个坑。
10. **Task 基类已集中 11 个共用工具**：`_focus` / `_load_flags` / `_scene_rect` / `_grab_scene` / `_present` / `_match_scene` / `_match_subregion` / `_find_join_on_row` / `_goto` / `_state_elapsed` / `_resolve_contexts`，以及 `BASE_CALIBRATION_REGIONS`（公共 `scene`/`activity_list` 标定向导项）。新任务**不要**重定义这些，直接 `self.xxx`；模板键用类属性 `_FLAG_KEYS`（base `_load_flags` 读它）。`CALIBRATION["regions"]` 用 `*Task.BASE_CALIBRATION_REGIONS` 起头再追加专有项。
11. **标定框选带鼠标放大镜**（`roi_overlay.select_roi_on_screen`）：跟随光标放大周围像素，倍数看 `MAG`/`MAG_VIEW` 常量，便于精细框选小特征。
12. **无 linter / typecheck / test suite**：改动只做 `py_compile` + 纯逻辑模拟验证。识别/点击/多开轮转的**真实手感必须 Windows 上开 2~3 个号自测**，报 bug 按「哪个任务哪一步点歪/没识别」定位。

## 参见
- 根 `CLAUDE.md`：详尽约束、架构地图、用户拍板项。
- `core/rotation.py` / `core/scan.py` docstring：多开轮转、滚动查找细节。
