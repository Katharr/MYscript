# CLAUDE.md —— 项目交接说明（给新会话的 Claude 看）

> 用中文交流（用户全局偏好）。代码/命令/文件名保持英文。

> **本文件维护原则：小而精（务必遵守）。** 这是每次会话都会被加载进上下文的文件，越短越好。
> 只写**长期有效**的东西：是什么、用户拍板的约束、架构、怎么跑、踩过且会复发的坑。
> **不写 changelog**——「改了哪几个文件、第几步怎么修、已验证/未验证」属于一次性记录，沉到 `git log`（commit 正文）
> 和 memory 目录，不要堆进本文件。每次更新前先问自己「这条三个月后还有用吗」；没用就别加，过时就删。
> 发现本文件又开始膨胀（尤其「当前状态」变成流水账），主动精简回来。

## 这是什么
《梦幻西游：时空》（网易手游的官方 Windows PC 客户端）的辅助脚本，带图形界面。
原理：**截屏 + OpenCV 图像识别 + 拟人化模拟点击**，不读内存、不注入进程。
当前已实现功能：**秒装备（市场捡漏）**——盯摆摊/市场列表，目标装备一出现就秒点购买。

⚠️ 脚本违反游戏用户协议、有封号风险，已多次向用户说明；用户知情，要求用小号测。
本工具仅供学习交流。

## 用户拍板的关键约束（务必遵守）
1. **命中即抢，不上 OCR**：模板匹配认不了“任意低于 X 价”，所以不做价格过滤。模板只框装备本身。
   - **刷新机制（用户拍板）**：游戏**没有固定刷新按钮**。看货架上新唯一办法是「点左侧类别→点右侧商品→进货架」，
     且货架进去后画面静止、不会自动上新，必须**退出重进**。故"刷新"=每轮重走这条两步路径。
     当前只盯**一个**货架、路径固定就是**两步**（类别+商品），均为固定坐标点击。
2. **必须拟人化**：贝塞尔曲线移动、加减速、落点随机偏移、按下/抬起与各种间隔随机抖动、偶尔走神。
3. **操作原理越底层越好**（怕封号）：鼠标走 Windows `SendInput`(user32) 注入，是用户态最底层标准接口。
   已诚实告知：真正不可检测要硬件级(KMBox/Arduino)/驱动级，本方案不保证 100% 安全。
4. **安全默认 dry_run=true**（只识别不下单）。
5. 用户**基本不读代码**，只在被明确告知“需要你亲手改的地方”才动手；要尽量傻瓜化（一键 + GUI）。
6. 用户要求**模块化**、可持续扩展，并要一个**现代、简约、精致、信息密度适中**的 GUI。
7. **活动列表卡片布局（运镖/宝图等「开活动→参加」类任务共用）**：活动界面的入口是**卡片**，
   每张卡片右侧有「参加」按钮，且**默认两张卡片一排**。按行找「参加」时**只能在条目所属那张卡片的列内找**，
   不能横向一路扫到列表右缘——否则会把右邻卡片的「参加」一起圈进来、点到右边卡片的参加（已踩坑修复）。
   实现：`_find_join_on_row` 按 `loop.activity_columns`（默认 2）把列表等分定位条目所在列、`x1` 收到该列右边界。
   排数变了就改配置 `tasks.<escort|treasure_map>.loop.activity_columns`，不必改代码。
8. **GUI 文字换行铁律（已踩坑两轮，务必遵守）**：所有「可能超一行」的说明性文字（副标题/开关说明/
   卡内提示/`lbl_calib` 标定状态）一律套 `theme.bind_wraplength(label)` + 让标签 `sticky="ew"`/`fill="x"`
   占满父容器，**禁止写死 `wraplength=数字`**（窗口比该值窄就溢出截断）。`bind_wraplength` 有三个非显而易见
   的坑（绑父容器读 `winfo_width` 而非 label 自身、DPI 要 `_reverse_widget_scaling`、要给初始 wraplength），
   细节见该函数 docstring 与 memory `ctklabel-wraplength-gotcha`——**改它前务必看懂，否则极易改回截断/振荡**。

## 架构（三层，包名 mhxy/）
```
启动.bat            双击入口 -> python start.py
start.py            装依赖 + 启动 GUI
config.json         配置（标定后生成；用 tasks.<任务名> 命名空间）
config.example.json 配置示例
templates/          装备模板图   captures/  命中截图
mhxy/
  core/   通用基础设施（与玩法无关）
    config.py   配置读写（DEFAULT_CONFIG / load/save / task_config / set_task_config）
    window.py   GameWindow（locate/rect/activate/坐标换算）+ grab() 截图 + set_dpi_aware()
    vision.py   load_template / save_image / match()  （兼容中文路径）
    input.py    Mouse 类：SendInput 底层 + human_move/click/sleep/maybe_idle
    context.py  TaskContext：打包 window/mouse/cfg/log/stop_event 给任务
    runner.py   TaskRunner：后台线程跑 Task + 线程安全日志队列
    rotation.py 多开轮转推进器：通用 while/for 骨架 +「连续推进到等待点才让出」
                （切一次前台把本号能自主做完的步连做完，state 不变=在等待才切下一号，少切前台）
    teaming.py  TeamFormation：跨窗口组队握手编排（基于 rotation）
  tasks/  可插拔任务
    base.py     Task 基类 + 注册表（register/get_task/all_tasks）
                + _make_rotation()：把运镖/宝图/秘境共用的多开轮转包成 RotationConfig（窗口消失 skip、
                  activate 失败节流、time_limit 到点自停文案），任务只传 step_fn(rec)
    sniper.py   SniperTask（秒装备）：preflight() 自检 + run(ctx) 主循环
                 刷新=每轮重进货架：_enter_shelf() 点类别→点商品→等加载，再截货架识别
    escort.py        EscortTask（运镖）：开活动→参加→押送普通镖银→循环押满次数
    treasure_map.py  TreasureMapTask（宝图）：开活动→收图→挖宝→领奖 两阶段状态机
    secret_realm.py  SecretRealmTask（秘境降妖）：开活动→参加→点秘境降妖→(选副本点左下角「进入」)→
                      确定→继续挑战→挑战→盯「进入战斗」续战→失败/超时/出现「离开」收尾，可连跑 max_runs 轮
    dungeon.py       DungeonTask（组队/一键组队）：把所选多开窗口组成一队即停。是「通用」页「一键组队」跑的任务
                      （角色参数 captain_index/dry_run 存共享 tasks.teaming）。注意：name 仍叫 "dungeon" 只为兼容，
                      「刷副本」页本身不再跑它，而是跑被选中的副本（见下「副本中枢」）。
    taohaiqu.py      TaohaiquTask（蹈海去·50，is_dungeon=True）：组队后由队长跑完整条剧情战斗，跑一遍即停
  tools/
    calibrate.py 旧的命令行标定（cv2.selectROI，已不被 GUI 调用，仅留作 CLI 备用）
  gui/
    theme.py            配色/字体/圆角常量（改这里整体换肤；深色现代风）
                        + bind_wraplength：说明性文字自动换行助手（见下「GUI 文字换行」铁律）
    app.py              主窗口：侧边导航 + GeneralPage(通用页,置顶,默认页)/各任务Page/SettingsPage/AboutPage
    roi_overlay.py      全屏框选组件（纯 tk，冻结截图上拖框，返回屏幕绝对 ROI）
    calibrate_dialog.py GUI 内标定对话框（区域 + 加装备，全程无黑窗）
```

### 任务模块约定（加新功能照此做）
- 任务在**后台线程**跑，通过 `ctx.log(msg, level)` 输出（level: info/hit/warn/error），
  循环里**勤查 `ctx.should_stop()`**，绝不直接碰 GUI。
- config 用 `tasks.<name>.*` 存各任务配置（regions/watchlist/dry_run/loop 都在 tasks.sniper 下）。
- **多开轮转走 `core/rotation.py`（统一底层，新任务照此接入）**：每号一份 record（含 `state/ctx/done`），
  用非阻塞状态机——每个 `_do_*/_cap_*/_mem_*` 处理方法只推进一小步，能往下做就 `_goto` 改 `state`、
  在等待（门控未就绪/没找到目标/监控态盯帧差）就**不改 state**。推进器据此「**连续推进到等待点才让出**」：
  切一次前台后把本号能自主做完的步连做完，`state` 不变=在等待才让出切下一号，省掉无谓的反复切前台。
  接入方式：运镖/宝图/秘境（普通逐号任务）直接用 `base.Task._make_rotation(ctx, records, step_fn, …)`
  得到 `RotationConfig` 再调 `rotation.run_rotation()`，只需传 `step_fn(rec)`（闭包绑自己的 loop/regions/threshold）；
  组队这类有跨窗口握手/特殊收尾的，自己构造 `RotationConfig`（见 `teaming.py`）。**五个任务均已接入**。
  **铁律：监控态未触发转移时绝不 `_goto`**，否则会在一个号上空转盯屏、饿死别的号。（详见 memory: rotation-engine）
- 加“自动师门”示例：新建 `mhxy/tasks/shimen.py` 写 `@register class ShimenTask(Task)`；
  在 `tasks/__init__.py` `from . import shimen`；在 `gui/app.py` 仿 `SniperPage` 加页面 + 在 `App.NAV` 加项。
- **副本中枢（「刷副本」页 = DungeonPage）**：副本不各占一个导航项，统一收进「刷副本」页用「选择副本」下拉切换、
  选谁跑谁（为以后「连刷多个副本」铺路）。**加新副本只需写个 `is_dungeon = True` 的 Task**（照 taohaiqu.py），
  `tasks/__init__.py` import 注册即可——`base.dungeon_tasks()` 会自动把它列进下拉，GUI 不用改。
  约定：副本「选谁跑」存中枢命名空间 `tasks.dungeon.selected`；每个副本自己的队长/演练实战/**已组队(skip_team)**/标定
  都在**该副本自己的命名空间**（如 `tasks.taohaiqu`），中枢页只是代理读写到选中副本。副本就绪度由其 `CALIBRATION`
  spec 通用推导（区域 spec 第 4 项 True=可选，不计必要项）。
  - **「已组队」开关（skip_team）**：勾上=已自行组好队，副本任务跳过组队握手、直接由队长开刷；此时 preflight 放宽
    （不要求多开≥2、不查组队资产/open_team/open_friend，只需队长那个号能定位）。就绪判定也随之不要求组队标定。
- **组队是共享能力、单独可一键触发**：组队握手在 `core/teaming.TeamFormation`；「通用 / 工具」页有
  「选队长 + 一键组队」（跑 `DungeonTask`），角色参数（captain_index/dry_run）存共享 `tasks.teaming`。
- **「队长ID 库」就近入口**：刷副本页 + 通用页都在「谁当队长」旁放了一个**带缩略图**的小按钮（一眼看到
  当前认哪张脸），点开 `gui/leader_gallery.LeaderIdGallery`（2x2 画廊：当前 + 最近 3 历史，可设为当前/删除/
  重新标定追加）。数据逻辑全在纯函数 `core/leader_history.py`：**激活图路径永远是 `templates/tm_leader_id.png`**
  （teaming 与 calibrate 都写死读它），切换 = 把选中历史图**字节复制覆盖**该文件、**绝不改 config 路径串**，
  故 `TeamFormation` 零改、零回归；历史存 4 个固定槽 `tm_leader_id_slot0..3.png` 环形淘汰最旧。两处入口写同一份
  config + 同一批文件、天然同步，变更后由 `App.refresh_leader_thumb()` **广播刷新两页**缩略图。
  ⚠ 就绪度判定只看 `templates.leader_id` 是否非空（不看文件存在），故 `leader_history` 在历史空时把它置 None。
  组队全套标定仍走通用页「标定（组队）」（`exclude=["leader_id"]`）。任何副本跑之前都先自动组队，这里只是把
  「单纯组个队」抽成手动动作。

## 当前状态
- 依赖已装好：numpy, opencv-python, mss, pyautogui, pygetwindow, customtkinter, Pillow。已打 v1.0 单文件 exe（`python build.py` / 双击 `打包.bat` 重打；数据落 exe 同级目录）。
- 任务已就位：秒装备(sniper) / 宝图(treasure_map) / 运镖(escort) / 秘境降妖(secret_realm) / 日常一条龙(daily)
  / 刷副本(副本中枢，目前收录蹈海去 taohaiqu) / 一键组队(通用页, dungeon)。
  逐号任务多开轮转（非阻塞状态机）；秒装备也多开轮转。GUI 已落地令牌化主题 + 白天/夜间切换。
- **通用约束（贯穿全部任务）**：多开各号窗口须**同尺寸**（共用标定点位）；一只鼠标，多开节奏天然慢于单开；
  操作某号前先 `window.activate()` 切前台（用 `_force_foreground` 绕过焦点抢占并校验，失败则跳过该号、下轮重试，绝不在后台号瞎点）。
- ⚠ **几乎所有改动都「未真机端到端验证」**——代码层 py_compile / example.json 合法 / 纯逻辑模拟基本都过了，
  但识别/点击/多开轮转的真实手感都要用户开 2~3 个号自测。报修 bug 时按「哪个任务的哪一步点歪/没识别」定位。

> 详细改动历史不在本文件里——查 `git log`（每条 commit 写了改了什么、为什么）和 memory 目录
> （treasure-map-task / escort-task / secret-realm-task / daily-chain-task 等）。本文件只保留**长期有效的约束与架构**，避免膨胀。

## 怎么跑
- 用户侧：双击 `启动.bat`，界面里「标定/加装备」→ 演练「开始秒装备」看 captures/ → 开「实战」开关再跑。
- 停止：界面「停止」按钮，或鼠标甩屏幕左上角（pyautogui/SendInput FAILSAFE）。

## 环境备注
- Windows 11，PowerShell 主 shell；也有 Bash 工具。
- git 推送走 Clash 代理端口 7897（见全局 CLAUDE.md），本项目已是 git 仓库。
- 记忆目录有更详细背景：game-mhxy-shikong / sniper-design-decisions / project-architecture。
```
