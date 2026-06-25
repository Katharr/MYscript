# CLAUDE.md —— 项目交接说明（给新会话的 Claude 看）

> 用中文交流（用户全局偏好）。代码/命令/文件名保持英文。

> **本文件维护原则：小而精（务必遵守）。** 这是每次会话都会被加载进上下文的文件，越短越好。
> 只写**长期有效**的东西：是什么、用户拍板的约束、架构地图、怎么跑、踩过且会复发的坑。
> **不写 changelog**——「改了哪几个文件、第几步怎么修、已验证/未验证、依赖装好没、打了 exe 没、
> 任务清单」都属一次性记录，沉到 `git log`（commit 正文）和 memory，不要堆进本文件。
> 实现细节凡代码 docstring / memory 已写清的，这里只留「一句话 + 指针」，别原样复述。
> 每次更新前先问「这条能从代码/git 派生吗、三个月后还成立吗」；能派生或会过期就别加。
> 发现本文件又开始膨胀（尤其冒出「当前状态」这种流水账容器），主动精简回来。

## 这是什么
《梦幻西游：时空》（网易手游的官方 Windows PC 客户端）的辅助脚本，带图形界面。
原理：**截屏 + OpenCV 图像识别 + 拟人化模拟点击**，不读内存、不注入进程。
具体有哪些功能看 GUI 各页 / `base.all_tasks()`（代码注册表为准，别在这里列清单）。

⚠️ 脚本违反游戏用户协议、有封号风险，已多次向用户说明；用户知情，要求用小号测。
本工具仅供学习交流。

## 用户拍板的关键约束（务必遵守）
1. **命中即抢，不上 OCR**：模板匹配认不了“任意低于 X 价”，所以不做价格过滤。模板只框装备本身。
   - **刷新机制（用户拍板）**：游戏**没有固定刷新按钮**。看货架上新唯一办法是「点左侧类别→点右侧商品→进货架」，
     且货架进去后画面静止、不会自动上新，必须**退出重进**。故“刷新”=每轮重走这条两步路径（类别+商品，固定坐标点击）。
2. **必须拟人化**：贝塞尔曲线移动、加减速、落点随机偏移、按下/抬起与各种间隔随机抖动、偶尔走神。
3. **操作原理越底层越好**（怕封号）：鼠标走 Windows `SendInput`(user32) 注入（见 `core/input.py`）。
   已诚实告知：真正不可检测要硬件级(KMBox/Arduino)/驱动级，本方案不保证 100% 安全。
4. **安全默认 dry_run=true**（只识别不下单）。
5. 用户**基本不读代码**，只在被明确告知“需要你亲手改的地方”才动手；要尽量傻瓜化（一键 + GUI）。
6. 用户要求**模块化**、可持续扩展，并要一个**现代、简约、精致、信息密度适中**的 GUI。
7. **活动列表卡片布局（运镖/宝图等「开活动→参加」类任务共用）**：活动入口是**卡片**、**默认两张一排**，
   每张右侧有「参加」。按行找「参加」时**只能在条目所属那张卡片的列内找**，不能横向扫到列表右缘——
   否则会圈进右邻卡片的「参加」、点到隔壁（已踩坑修复）。排数变了改配置 `tasks.<name>.loop.activity_columns`
   即可，不必改代码；实现见 `escort.py` 的 `_find_join_on_row` docstring。
8. **GUI 文字换行铁律（已踩坑两轮）**：所有「可能超一行」的说明性文字（副标题/开关说明/卡内提示/标定状态）
   一律套 `theme.bind_wraplength(label)` + 让标签 `sticky="ew"`/`fill="x"` 占满父容器，
   **禁止写死 `wraplength=数字`**（窗口比它窄就溢出截断）。三个非显而易见的坑见 `theme.bind_wraplength`
   docstring 与 memory `ctklabel-wraplength-gotcha`——**改它前务必看懂，否则极易改回截断/振荡**。
9. **多开通用约束（贯穿全部任务）**：多开各号窗口须**同尺寸**（共用标定点位）；一只鼠标，多开节奏天然慢于单开；
   操作某号前先 `window.activate()` 切前台（`_force_foreground` 绕过焦点抢占并校验，失败则跳过该号、下轮重试，
   绝不在后台号瞎点）。

## 架构（三层，包名 mhxy/）
> 文件树是「地图」，只给一句话功能；实现细节看各文件 docstring 与 memory，别往这里抄。
```
启动.bat            双击入口 -> python start.py
start.py            装依赖 + 启动 GUI
config.json         配置（标定后生成；用 tasks.<任务名> 命名空间）  config.example.json 示例
templates/          装备/模板图   captures/  命中截图
mhxy/
  core/   通用基础设施（与玩法无关）
    config.py   配置读写（DEFAULT_CONFIG / load/save / task_config / set_task_config）
    window.py   GameWindow（locate/rect/activate/坐标换算）+ grab() 截图 + set_dpi_aware()
    vision.py   load_template / save_image / match() / frame_diff()（兼容中文路径）
    scan.py     通用「滚动查找」scroll_search()（翻列表/翻包裹统一底层）；详见 docstring + memory scroll-search-scan
    input.py    Mouse 类：SendInput 底层 + human_move/click/sleep/maybe_idle
    context.py  TaskContext：打包 window/mouse/cfg/log/stop_event 给任务
    runner.py   TaskRunner：后台线程跑 Task + 线程安全日志队列
    rotation.py 多开轮转推进器：连续推进到等待点才让出；详见 docstring + memory rotation-engine
    teaming.py  TeamFormation：跨窗口组队握手编排 + run_disband() 解散队伍（每号同一套退队流程）
    inventory.py InventoryOrganizer：整理背包（翻包裹逐物使用/丢弃/出售）的可复用编排，只依赖 ctx；详见 memory organize-bag-task
  tasks/  可插拔任务
    base.py     Task 基类 + 注册表（register/get_task/all_tasks）+ _make_rotation()（包多开轮转）+ dungeon_tasks()
    sniper.py   SniperTask（秒装备）：preflight() 自检 + run() 主循环；刷新=每轮重进货架 _enter_shelf()
    escort.py        EscortTask（运镖）：开活动→参加→押送普通镖银→循环押满次数
    treasure_map.py  TreasureMapTask（宝图）：开活动→收图→挖宝→领奖 两阶段状态机
    secret_realm.py  SecretRealmTask（秘境降妖）：开活动→参加→挑战→盯「进入战斗」续战，可连跑 max_runs 轮
    dungeon.py       DungeonTask（组队/一键组队）：把所选多开窗口组成一队即停（通用页「一键组队」跑它，
                      角色参数存共享 tasks.teaming）。name 仍叫 "dungeon" 仅为兼容；刷副本页跑的是选中副本而非它。
    disband.py       DisbandTask（解散队伍/一键解散）：让所选各号都退出当前队伍（每号同一套流程，不分队长队员），
                      复用 teaming.run_disband；通用页「一键解散」跑它，副本勾「跑完解散队伍」也调它
    taohaiqu.py      TaohaiquTask（蹈海去·50，is_dungeon=True）：组队后队长跑完整条剧情战斗，跑一遍即停
    organize_bag.py  OrganizeBagTask（整理背包）：通用页可单独跑的共享能力封装，逐号 activate→core/inventory 整理；详见 memory organize-bag-task
  tools/
    calibrate.py 旧的命令行标定（已不被 GUI 调用，仅留作 CLI 备用）
  gui/
    theme.py            配色/字体/圆角令牌（改这里整体换肤）+ bind_wraplength 换行助手（见约束 8）
    app.py              主窗口：侧边导航 + 通用页(置顶,默认)/各任务Page/SettingsPage/AboutPage
    roi_overlay.py      全屏框选组件（纯 tk，冻结截图上拖框，返回屏幕绝对 ROI）
    calibrate_dialog.py GUI 内标定对话框（区域 + 模板缩略图画廊 + 加装备），按任务 CALIBRATION spec 驱动
    leader_gallery.py   队长ID 库画廊（见下「队长ID 库」约束）
    inventory_items_dialog.py 整理背包「物品清单」管理弹窗（缩略图+名字+动作下拉+框选添加，写 tasks.organize_bag.items）
```

### 任务模块约定（加新功能照此做）
- 任务在**后台线程**跑，通过 `ctx.log(msg, level)` 输出（level: info/hit/warn/error），
  循环里**勤查 `ctx.should_stop()`**，绝不直接碰 GUI。
- config 用 `tasks.<name>.*` 存各任务配置（regions/watchlist/dry_run/loop 等）。
- **多开轮转用 `core/rotation.py`**（非阻塞状态机：每号一份 record，处理方法只推进一小步，能往下做就 `_goto`
  改 state、在等待就不改 state；推进器据此「连续推进到等待点才让出」省切前台）。逐号任务接入走
  `base.Task._make_rotation()`，只传 `step_fn(rec)`；有跨窗口握手的自己构造 `RotationConfig`（见 `teaming.py`）。
  **铁律：监控态未触发转移时绝不 `_goto`**，否则在一个号上空转盯屏、饿死别号。详见 memory `rotation-engine`。
- 加新任务示例：新建 `mhxy/tasks/xxx.py` 写 `@register class XxxTask(Task)`；在 `tasks/__init__.py` import；
  在 `gui/app.py` 仿 `SniperPage` 加页面 + 在 `App.NAV` 加项。
- **日志统一到「全局日志面板」（约束，别再各页造日志框）**：日志框只此一处——常驻主窗口右侧（`App._build_log_panel`），
  统一出口 `App.log_line(msg, level, source)`。新页面**不要**自建日志框：设个类属性 `LOG_SOURCE = "短名"`，
  页内 `_log_line` 照范式写成一行转发 `self.app.log_line(msg, level, getattr(self,"LOG_SOURCE",None))`，
  全局面板会按 source 打来源标签（如「秒装备 ›」）。一页里有多种来源（如通用页的组队/整理背包）就在
  `pump`/各消息处显式把第三个参 source 传成对应短名覆盖。
- **副本中枢（「刷副本」页 = DungeonPage）**：副本统一收进该页用「选择副本」下拉切换、选谁跑谁。
  **加新副本只需写个 `is_dungeon = True` 的 Task**（照 `taohaiqu.py`）、在 `tasks/__init__.py` import——
  `base.dungeon_tasks()` 自动把它列进下拉，GUI 不用改。约定：选谁跑存 `tasks.dungeon.selected`；每个副本自己的
  队长/演练实战/已组队(skip_team)/标定都在**该副本自己的命名空间**，中枢页只代理读写到选中副本。
  - **「已组队」开关（skip_team）**：勾上=已自行组好队，副本跳过组队握手、直接由队长开刷；preflight 随之放宽
    （不要求多开≥2、不查组队资产，只需队长那个号能定位）。
- **组队是共享能力、单独可一键触发**：握手在 `core/teaming.TeamFormation`；通用页有「选队长 + 一键组队」
  （跑 `DungeonTask`），角色参数存共享 `tasks.teaming`。任何副本跑之前都先自动组队。
- **「队长ID 库」（`gui/leader_gallery.py` + 纯函数 `core/leader_history.py`）非显而易见的约束**：
  **激活图路径永远是 `templates/tm_leader_id.png`**（teaming 与 calibrate 都写死读它），切换当前队长 = 把选中历史图
  **字节复制覆盖**该文件、**绝不改 config 路径串**，故 `TeamFormation` 零改、零回归。⚠ 就绪度判定只看
  `templates.leader_id` 是否**非空**（不看文件存在），故历史空时 `leader_history` 把它置 None。画廊/槽位/广播刷新等
  细节见 memory `leader-id-gallery`。

> 改动历史不在本文件——查 `git log`（commit 正文写了改了什么、为什么）和 memory 目录
> （treasure-map-task / escort-task / secret-realm-task / daily-chain-task / teaming-and-dungeon-task 等）。

## 怎么跑
- 用户侧：双击 `启动.bat`，界面里「标定/加装备」→ 演练看 captures/ → 开「实战」开关再跑。
- 停止：界面「停止」按钮，或鼠标甩屏幕左上角（pyautogui/SendInput FAILSAFE）。
- ⚠ 改动多止于代码层验证（py_compile / example.json 合法 / 纯逻辑模拟）；识别/点击/多开轮转的真实手感
  需用户开 2~3 个号自测。报 bug 时按「哪个任务的哪一步点歪/没识别」定位。

## 环境备注
- Windows 11，PowerShell 主 shell；也有 Bash 工具。
- git 推送走 Clash 代理端口 7897（见全局 CLAUDE.md），本项目已是 git 仓库。
- 记忆目录有更详细背景：game-mhxy-shikong / sniper-design-decisions / project-architecture。
