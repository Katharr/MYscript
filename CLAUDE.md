# CLAUDE.md —— 项目交接说明（给新会话的 Claude 看）

> 用中文交流（用户全局偏好）。代码/命令/文件名保持英文。

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
  tasks/  可插拔任务
    base.py     Task 基类 + 注册表（register/get_task/all_tasks）
    sniper.py   SniperTask（秒装备）：preflight() 自检 + run(ctx) 主循环
                 刷新=每轮重进货架：_enter_shelf() 点类别→点商品→等加载，再截货架识别
    escort.py        EscortTask（运镖）：开活动→参加→押送普通镖银→循环押满次数
    treasure_map.py  TreasureMapTask（宝图）：开活动→收图→挖宝→领奖 两阶段状态机
    secret_realm.py  SecretRealmTask（秘境降妖）：开活动→参加→点秘境降妖→(选副本点左下角「进入」)→
                      确定→继续挑战→挑战→盯「进入战斗」续战→失败/超时/出现「离开」收尾，可连跑 max_runs 轮
  tools/
    calibrate.py 旧的命令行标定（cv2.selectROI，已不被 GUI 调用，仅留作 CLI 备用）
  gui/
    theme.py            配色/字体/圆角常量（改这里整体换肤；深色现代风）
    app.py              主窗口：侧边导航 + GeneralPage(通用页,置顶,默认页)/各任务Page/SettingsPage/AboutPage
    roi_overlay.py      全屏框选组件（纯 tk，冻结截图上拖框，返回屏幕绝对 ROI）
    calibrate_dialog.py GUI 内标定对话框（区域 + 加装备，全程无黑窗）
```

### 任务模块约定（加新功能照此做）
- 任务在**后台线程**跑，通过 `ctx.log(msg, level)` 输出（level: info/hit/warn/error），
  循环里**勤查 `ctx.should_stop()`**，绝不直接碰 GUI。
- config 用 `tasks.<name>.*` 存各任务配置（regions/watchlist/dry_run/loop 都在 tasks.sniper 下）。
- 加“自动师门”示例：新建 `mhxy/tasks/shimen.py` 写 `@register class ShimenTask(Task)`；
  在 `tasks/__init__.py` `from . import shimen`；在 `gui/app.py` 仿 `SniperPage` 加页面 + 在 `App.NAV` 加项。

## 当前状态
- 依赖已装好：numpy, opencv-python, mss, pyautogui, pygetwindow, customtkinter, Pillow。
- **2026-06-23（最新）落地《视觉设计规范》(docs/视觉设计规范.md)：令牌化主题 + 白天/夜间模式 + 组件尺寸统一**。
  按规范第 9 节 step 1~4 执行（step 5「抽 BaseTaskPage」是单独立项，本次不做）。改了 5 文件：
  1. **`gui/theme.py`（地基）**：所有颜色常量改成 `(light, dark)` **二元组**（dark 端==历史深色值，故深色观感不变）；
     新增令牌 `PILL_OK_BG/PILL_DANGER_BG/ON_ACCENT/SUCCESS_HOVER`、`RADIUS_PILL=20`、间距 `SP_1..SP_6`(4/8/12/16/20/24)；
     新增 `resolve(token)`（按 `ctk.get_appearance_mode()` 从二元组取当前端单值，单值原样返回——给「不吃二元组」的底层 tk 用）
     和 `apply_log_tags(textbox)`（按 resolve 配置日志各级别前景色，切换明暗后需重跑）；`LEVEL_COLOR` 改存令牌(二元组)。
     ⚠ 因 LEVEL_COLOR 值变成二元组，旧的 `tag_config(foreground=color)` 直接喂会坏——所以日志上色**必须**走 `apply_log_tags`。
  2. **`core/config.py` + `config.example.json`**：顶层加 `"appearance":"dark"`（dark/light）；旧 config 由 `_deep_merge` 自动补。
  3. **`gui/app.py`**：① `App.__init__` 读 `cfg["appearance"]` 调 `set_appearance_mode`（替代写死 "dark"）；
     ② 侧栏底部(风险提示之上, row=100)加明暗切换按钮 `btn_appearance`，`_toggle_appearance`(切换+写回配置+逐页 `apply_log_tags` 重刷日志色)、
     `_render_appearance_btn`(夜间显「🌙 夜间模式」/白天显「☀ 白天模式」)；
     ③ 按迁移表统一：工具按钮 `34/120`(秒装备)与 `38/92`(其它页)→统一 **36×104**；秒装备运行按钮 `180`→**200**(与其它页一致 46 高)；
     卡片内边距 `18`→**16**(SP_4)；药丸底色内联 `#1d3a2b/#3a1d1d/#15301f`→令牌 `PILL_OK_BG/PILL_DANGER_BG`；
     Toast `"white"`→`ON_ACCENT`；4 个任务页日志上色循环→`T.apply_log_tags`；`GeneralPage._card` 自建范式→复用 `Card()`。
  4. **`gui/roi_overlay.py`**：纯 tk 浮层(刻意不用 CTk)的底色/文字/线色改用 `T.resolve(BG/TEXT/ACCENT)` 按打开瞬间外观取单值
     (规范 §4.3：临时浮层无需热切换；遮罩仍用纯黑)。
  5. **`gui/calibrate_dialog.py`**：「＋ 框选添加」绿钮的内联 `#34b87c/#0e1014`→`SUCCESS_HOVER/BG` 令牌(两端对比都够)。
  - 设计取舍：颜色用二元组是规范选定的**低风险路径**——绝大多数 `fg_color=T.X`/`text_color=T.X` 调用点**零改动**即同时支持明暗，
    CTk 随 `set_appearance_mode` 自动重绘；药丸用二元组令牌后切换明暗也自动跟随，无需手动刷。**唯一要手动补刷的是日志 tag**(底层 tk)。
  - **已验证**：6 文件 py_compile 过；example.json 合法；运行实测——令牌是二元组(dark 端==现值)、`resolve` 明/暗两端取值正确、
    单值原样返回、`LEVEL_COLOR['hit'] is SUCCESS`、SP/RADIUS_PILL 值正确、DEFAULT 含 appearance、旧 config 经 `_deep_merge` 自动补；
    app 真实 import、NAV 首项仍 general、`_toggle_appearance` 含写回；全工程无残留 `width=120/92/180`、无内联药丸色、无旧 tag 循环。
  - **未真机端到端验证**（需用户开 GUI 自测）：① 启动默认按 `cfg["appearance"]`；② 点侧栏「🌙/☀」来回切——4 任务页+通用/设置/关于+
    选窗/标定对话框都不应有「读不出的低对比」或「残留深色块」，日志已有行的颜色应跟随切换；③ 工具按钮/运行按钮尺寸是否齐整。
  - **追加修复（用户反馈白天模式按钮看不清）**：① 次级/工具按钮原用 `SURFACE_2` 填充和卡片底糊在一起→新增专用令牌
    `BTN/BTN_HOVER`（明显区别于卡片，dark `#2f3744`/light `#e2e6ec`）+ 1px `BORDER` 描边。
    ② **根因**：CTk 的 `CTkButton/CTkOptionMenu/CTkSegmentedButton` 默认文字色是**不随明暗变的近白色 `#DCE4EE`**——
    深色填充上没事，但 Secondary(浅底)/Ghost(透明浮浅卡) 在白天模式变「浅底+近白字=看不见」。已给**全部 35 个按钮**
    + 2 个 OptionMenu(加 `dropdown_text_color`) + 1 个 SegmentedButton 显式设 `text_color`（填充块用 `ON_ACCENT`、浅底/透明用 `TEXT`）。
    规范 §5.1 已补「必须显式设 text_color」的踩坑说明。改了 `theme.py/app.py/window_picker.py/calibrate_dialog.py`，
    py_compile + import + 「35 按钮全带 text_color」审计脚本全过。
  - **追加修复（秒装备控制卡片）**：三个工具按钮原来竖排，和其它任务页不一致→改成三段式（运行按钮+横排工具/分隔线/开关），四页统一。
  - **追加修复（页眉副标题过长被截断，如秘境降妖）**：CTkLabel **默认不换行**，单行长文本被窗口右缘切掉。
    新增模块助手 `bind_wraplength(label)`（绑 `<Configure>`，按实际宽度设 `wraplength` 自适应换行）。把 4 个任务页页眉重构成
    `bar` grid：标题 row0col0 / 药丸 row0col1 / **副标题单独 row1col0 sticky=ew** + `bind_wraplength`；通用页副标题同理。
    规范 §6 已补「Header 结构」与「长文本必须能自动换行」两条踩坑说明。共 5 处用上 `bind_wraplength`。
- **2026-06-23 新增「通用 / 工具」页 + 窗口尺寸归一化（方案A：一键还原到标定尺寸）**。
  背景：脚本检测区/按钮点位(`regions`)是按标定那一刻的窗口尺寸记录的**窗口相对像素**，模板又是**单尺度**匹配——
  窗口**移动**没事(每次 `window.rect()` 实时换算)，但被手操**拉大/缩小**后点位全错、模板也认不出。用户常多开 3 个号、
  手操会把某些号拉大，要能一键拉回。**调研过两个方案**：A=把窗口 resize 回标定尺寸(简单可靠,识别/点击逻辑零改动)；
  B=多尺度匹配+坐标层缩放归一化(改动大、风险高，且单纯多尺度匹配不完整——大量固定相对点位[购买/确认/活动列表区/参加列定位]
  不靠模板，缩放后照样点偏)。**用户拍板只做方案A**，基准尺寸=标定时的窗口尺寸。pygetwindow 的窗口对象**确实支持
  `resizeTo/moveTo`**(实测 `dir(gw.Window)` 有)，故可行。⚠ 唯一不确定点：游戏若锁分辨率档位，`resizeTo` 可能被忽略
  (按钮会提示「部分窗口可能不支持自由缩放」，那方案A对该游戏不成立，需回头评估方案B)——**需用户真机点按钮确认窗口真能被 resize**。
  - 核心实现：`core/window.py` 加 `GameWindow.resize_to(w,h,move_to=None)`(resizeTo+读回校验,误差>4px 重试1次,成功返 True)
    和模块函数 `restore_targets_size(title,offset,targets,base_size)`(复用 `resolve_targets` 选窗,逐个 activate+resize,
    返回 `(成功数,总数,各号实际尺寸)`)。`core/config.py`+`config.example.json`：`targets` 加 `base_size:None`(标定时记录的[w,h])。
  - **基准尺寸有两条录入路径**：① `gui/calibrate_dialog.py` 的 `_grab_roi` 每次框选成功后**自动**把当前窗口[w,h]写入
    `targets.base_size`(兜底)；② 通用页里**显式**点某个窗口「设为基准」。两者都是"某个窗口的尺寸"，行为一致。
  - **用户要求把跨任务/任务流程之外的功能集中**，故新增 `GeneralPage`(`gui/app.py`)——**侧边栏置顶 + 启动默认页**
    (`NAV` 第一项 + `__init__` `_show("general")`)。两张卡片：①目标窗口(摘要+「选择窗口」)；②窗口尺寸归一化
    (显示**当前基准尺寸**+「还原尺寸」「刷新」+**列出检测到的所有窗口**[号N/尺寸/位置]每行「设为基准」,当前基准行标✓)。
    `GeneralPage` **不在 RUNNABLE_KEYS**(无 runner/pump/update_game_pill；`_show` 切入时调 `refresh()` 才枚举窗口,省启动开销；
    热键作用于当前页时它没 `_toggle_run` 会被跳过)。摘要算法在页内内联(不依赖 App 定义顺序)。
  - 任务页按钮取舍(用户拍板)：**任务页保留「选择窗口」**(高频就近)，**「还原尺寸」只放通用页**(低频集中,已从4个任务页移除)。
    `App.restore_window_size(after)` 读 `targets.base_size`(空则 toast 提示去标定)→`restore_targets_size`→按结果 toast(全成功/部分不支持)。
  - 改了 4 文件：`core/window.py`、`core/config.py`、`config.example.json`、`gui/calibrate_dialog.py`、`gui/app.py`(新增 GeneralPage+接线)。
  - **已验证**：5 文件 py_compile 过；example.json 合法；DEFAULT 含 base_size + 旧 config 经 `_deep_merge` 自动补默认；
    `restore_targets_size` 四分支(多开都还原/锁档位 ok=0/空 base_size/无窗口)全 PASS；app 真实 import、NAV 含 general、
    GeneralPage 方法齐全且非 runnable、还原按钮全工程仅通用页 1 处。
  - **未真机端到端验证**(需用户开 2~3 个号自测)：通用页看窗口列表→点「设为基准」看基准尺寸更新+标✓→手操拉大某号→
    「还原尺寸」**看窗口是否真缩回基准**(锁档位则提示不支持)→还原后跑演练看识别/点击恢复正常。
- **2026-06-23 运镖任务改成多开逐号轮转**（用户反馈「运镖多开没有生效」）。原 `escort.py` 是
  阻塞式状态机、只操作选中的第一个号（还主动打印「暂不支持多号轮跑」）。现**仿「秘境降妖」重构成非阻塞逐号轮转**：
  每个号一份 record(`_new_record`：state/escorts/seen_ongoing/gone_since/t_trip/recover/done)，主循环对每个号
  `_step_once` 各推进一小步，号间逐步轮转(操作前先 `window.activate()`，switch_delay 间隔)。
  状态：OPEN_ACTIVITY→FIND_CARD(每访问滚一屏找运镖入口点参加)→DIALOG(点「押送普通镖银」,escorts+1)→
  CONFIRM(点「确认」/超时容错)→ESCORTING(监控运镖中标志/对话框复现：又弹框且未满次数就续点回 CONFIRM,
  满次数或运镖中标志消失够 done_idle_sec 就 `_finish_escort` 收尾该号)。所有号 done 或时间上限则整体停。
  续趟统一走「点押送银→escorts+1→CONFIRM」，首趟与续趟共用一套。卡死兜底 `_recover_window`：仅
  `escorts==0`(还没开始任何一趟)时回开活动重试，否则放弃该号(避免「已参加」后重开活动连环卡死)。
  改了 2 文件：`tasks/escort.py`(重写 run 及状态机)、`core/config.py`+`config.example.json`(escort.loop 加 `tick_interval_sec`)。
  GUI 无需改（运镖页+「选择窗口」按钮+多开开关 `targets.multi` 早已就位，run 直接读 `ctx.cfg["targets"]["multi"]`）。
  - **已验证**：py_compile 过；example.json 合法；注册/配置合并(含 tick_interval_sec)/旧config补默认/关键方法齐全/
    record 字段齐全 全 PASS。**未真机端到端验证**（需用户开 2~3 个同尺寸号自测：选窗口勾多开→演练看 [号1]/[号2] 轮转识别→
    实战看多号逐个开活动/参加/押镖/续趟）。⚠ 各号须同尺寸(共用标定点位)；一只鼠标故多开节奏天然慢于单开。
- **2026-06-23 新增第四个任务「秘境降妖」**（`secret_realm`）。用户描述的真实流程：
  开活动→点活动卡片右侧「参加」→弹框点「秘境降妖」→**(可选)若有选副本界面，点屏幕左下角的「进入」**
  →「确定」→「继续挑战」→「挑战」→开始自动战斗→**到难度关卡不再自动，实时盯「进入战斗」按钮一出现就点续战**
  →出现 失败/「离开」按钮 → 点「离开」退出秘境收尾。**「超时」不是屏幕标志、是按时长判定**
  （挂够 `battle_timeout_sec` 仍没结束就视为超时、点离开——用户拍板：超时怎么会有标定物）。可连跑 `max_runs` 轮（默认 1）。
  - 关键设计：① 几个副本的「进入」按钮**长得一样，只能靠位置**区分 → `_match_subregion` 只在 scene 的
    左下角比例框 `loop.dungeon_enter_box`（默认 [0,0.5,0.55,1.0]）里匹配；选副本是**可选环节**，没标
    `sr_dungeon_enter` 模板或短超时(`dungeon_select_wait_sec`)内没出现就跳过。
    ② 确定/继续挑战/挑战 容错点击：超时仅 warn 继续，靠战斗监控兜底，避免流程微变就死卡。
    ③ 战斗监控 `_do_battle`：每访问扫一遍，**只在【判定失败 sr_fail】或【时长超时】后才点「离开」**
    (用户拍板：不设"看到离开就点"的旁路，避免误判提前退出；秘境打到失败/超时为止)；失败要**先点掉失败结算
    再点离开**(用户实测：不先点失败，离开点不到)；否则有「进入战斗」(`sr_enter_battle`)就点续战；否则等。
    **「超时」无模板，纯按时长**：挂够 `battle_timeout_sec` 仍没结束就判超时、点离开（用户拍板去掉了 sr_timeout 标定项）。
    ④ 复用运镖/宝图的 `_find_join_on_row`(按列定位避免点到右邻卡片)、整窗检测。
    ⑤ **支持多开轮转**（2026-06-23 用户要求「多号每一步都轮转」重做）：见下。
  - 必备模板：sr_entry/sr_join/sr_select/sr_continue/sr_challenge/sr_enter_battle/sr_leave；
    可选：sr_dungeon_enter(选副本进入)/sr_confirm(确定)/sr_fail/sr_battle(仅诊断)。（无 sr_timeout——超时是时长判定）
  - **「确定」只在选了副本后才弹**（用户实测：不选副本没有确认键）→ `S_CONFIRM` 用 `picked_dungeon`
    gate 住，没点过副本「进入」就跳过(`S_DUNGEON` 超时直接转 `S_CONTINUE`)，不再空等 step_timeout；故 sr_confirm 也降为可选模板。
  - **多开轮转（2026-06-23 重做：从阻塞状态机改成非阻塞逐号轮转）**：原来是阻塞式状态机，多开只能操作第一个号。
    现重构为：每个号一份 record(`_new_record`：state/计时/runs/picked_dungeon/entered_battle/recover)，主循环对每个号
    `_step_once` 各推进**一小步**(非阻塞)，号与号之间逐步轮转(操作前先 `window.activate()`，switch_delay 间隔)。
    状态：OPEN_ACTIVITY→FIND_CARD(每访问滚一屏找卡片点参加)→SELECT(点秘境降妖)→DUNGEON(可选选副本)→
    CONFIRM(选副本才有)→CONTINUE→CHALLENGE→BATTLE(扫一遍:失败/超时去 LEAVE，进入战斗就续点)→LEAVE(点离开收尾)→
    `_finish_run`(轮数+1，没满回 OPEN_ACTIVITY，满了该号 done)。所有号 done 或时间上限则整体停。`tick_interval_sec`
    控制每整轮间隔。单开=列表只 1 个号、同走这套。⚠ 各号须**同尺寸**(共用标定点位)；一只鼠标物理限制故多开节奏慢。
  - 改了 4 文件：`tasks/secret_realm.py`(新)、`tasks/__init__.py`(注册)、`core/config.py`(加 secret_realm 配置块含 tick_interval_sec)、
    `config.example.json`(同步)、`gui/app.py`(新增 `SecretRealmPage` 仿 EscortPage + NAV/PAGES/RUNNABLE_KEYS 接线)。
  - **已验证**：py_compile 全过；example.json 合法；注册/配置合并/CALIBRATION 规格(scene 带 full_window 标记)/
    模板键一致/关键方法齐全 全 PASS；GUI 模块真实 import + 接线确认 OK。
  - **未真机端到端验证**（需用户开游戏自测）：标定(活动列表区 + 各按钮模板)→演练看各标志识别→实战走完整流程，
    重点核对：左下角「进入」比例框是否框对那个进入、「进入战斗」按钮模板是否够独特、失败/超时能否触发离开。
- **2026-06-22（最新）新增基础特性「目标窗口选择 + 整窗检测」，并在其上实现秒装备多开轮转**。
  用户诉求：常多开 3 个号（桌面互不重叠、**同尺寸**）；要能轮流照看多个号；并且用「选窗口」直接把
  **整窗当检测区**省掉框大区域那步。用户拍板：**所有功能都基于这个新特性，作为基础**。
  关键设计取舍：① 鼠标光标全局唯一 → 多开走**单线程轮转**（每步挨个号做，无需锁）；
  ② 三个号标题相同、HWND 重启会变 → 窗口身份用**屏幕位置序号**（左→右，见 `window.locate_all` 排序），
  匹配「号固定摆位」；③ 检测区(`listing`/`scene`)**留空=整窗**，运行时用 `window.rect()`，自动适配尺寸；
  ④ 操作某号前先 `activate()` 切前台，避免点击穿透/点错号。**单开=选 1 个窗口、多开=选多个**，二者同走轮转(单开列表长度=1)。
  改了 10 个文件 + 新增 1 个：
  - `core/window.py`：`rect()` 包 try/except（绑定窗口被关后返 None）；新增 `bind()`、`locate_all()`（枚举+左→右排序）、
    纯函数 `resolve_targets(title,offset,targets)`（按选择取窗，供任务与 GUI 共用）。
  - `core/config.py` + `config.example.json`：顶层新增 `targets`（multi/single_index/multi_indices/max_windows/switch_delay_sec）。
    `build.py` 用 DEFAULT_CONFIG 写发布配置，自动同步，未改 build。
  - `core/context.py`：`__init__` 加 `window/label`；`log()` 多开加「[号N] 」前缀；`make_child()`（共享鼠标/日志/停止/配置）；
    `select_windows()`（走 resolve_targets）；`detection_rect(region)`（**整窗检测核心**：region 空→窗口 rect）。
  - `tasks/base.py`：新增 `_acquire_target_window(ctx)`——把 `ctx.window` 绑到选中目标窗口并保持有效，替代旧的 `ctx.window.locate()`(自动选最大)。
  - `tasks/sniper.py`：抽出 `_snipe_one_round`；`run` 改单/多开轮转（`_resolve_contexts`/`_ensure_contexts`/`_prepare_window`）；
    大检测区改 `detection_rect`；`listing` 变可选；preflight 改校验 `select_windows()` 非空。
  - `tasks/escort.py`、`tasks/treasure_map.py`：run/preflight/dry_run 里 `ctx.window.locate()` → `self._acquire_target_window(ctx)`；
    `scene` 变可选整窗；多开时日志提示「暂只操作选中的第一个号，多号顺序跑后续支持」。**这俩是有状态状态机，本期不做多号轮跑**。
  - `gui/window_picker.py`（新）：选择窗口对话框——枚举窗口渲卡片(带**实时缩略图**，截图时透明化避免拍进自己)，
    单开单选/多开多选，存 `cfg["targets"]`；尺寸不一致会警告。
  - `gui/app.py`：三页 tools 行加「选择窗口」按钮(走 `App.open_window_picker`)；`update_game_pill(connected, summary)` 显示
    「● 号N · 单开 / N 号 · 多开」；`_kick_locate` 改用 `locate_all`+`_compute_target_state` 算摘要，`_apply_game_state` 缓存比较元组。
  - `gui/calibrate_dialog.py`：CALIBRATION region 项支持第4元素 `full_window=True` → 多渲「用整窗」按钮、状态显示「整窗(默认)」；
    `_grab_roi` 改按 `resolve_targets` 选中的窗口标定(不再自动选最大，去掉末尾会改选的 `win.locate()`)。
  - 兼容：旧 config 无 `targets` 由 `_deep_merge` 补默认(单开/号1)，等价原行为；DEFAULT 里 listing/scene 本就 None=现整窗。
  - **已验证**：10 文件 py_compile 过；example.json 合法；纯逻辑模拟全 PASS（locate_all 左→右排序+过滤最小化/异名、
    resolve_targets 单/多开/越界/截断、rect 失效返 None、make_child「[号N]」前缀+共享鼠标、detection_rect 整窗、
    任务注册、CALIBRATION full_window 标记、sniper 单/多开上下文构建、App._compute_target_state 摘要）；三 GUI 模块真实 import OK。
  - **未真机端到端验证**（需用户开 2~3 个同尺寸号自测）：选择窗口看缩略图认号 → 单开选1/多开勾多 → 药丸摘要 →
    秒装备**不框 listing** 整窗演练看 [号1]/[号2] 轮转 → 单开回归 → 运镖/宝图选窗口+整窗 scene 能跑。
  - ⚠ 多开各号窗口须**同尺寸**否则共用标定的小按钮点位会点错；`activate()` 每号切前台约 0.3s 故多开节奏天然慢于单开（一只鼠标的物理限制）。
- **2026-06-21（最新）提速「抢不过暴力脚本」+ 把用户调好的速度固化为标准配置并随包打包**。
  用户痛点：原每轮巡航 ~3.5s（傻等加载 1.2s + 轮间空等 1.0s + 慢贝塞尔进货架）、命中下单 ~2s，抢不过别人。改了 5 个文件，分两块：
  1. **硬提速（不破坏巡航拟人化）**：
     - `core/input.py`：`human_move/click/_speed` 加可选 `speed` 倍率覆盖——只在「命中下单那一下」用激进倍率，
       巡航点击仍走常速拟人化。极速时步数随倍率收缩（更直、更少步）。
     - `tasks/sniper.py`：① 新增 `_wait_shelf_loaded()` 自适应等加载——先等 `shelf_load_min_sec`，再每 60ms 截 listing
       区比上一帧，`_frame_diff()`（numpy 均值绝对差 <1.5）判定画面静止即「立即识别」，不傻等满 `shelf_load_wait_sec`
       （后者改成上限/超时），返回该帧复用。② `_enter_shelf()` 去掉内部固定等待，只点类别+商品。
       ③ `_buy_sequence(...,speed)` + `_snipe_sleep()` 走极速倍率（`humanize.snipe_speed`）。
     - `gui/app.py`：`SettingsPage.SPEED_CONTROLS` 新增滑杆「命中下单极速倍率」「货架加载最短等待」，
       「货架加载等待」改名「最长等待」。列表驱动，`_apply` 自动按命名空间写回 humanize/loop。
     - 效果：巡航 ~3.5s→~1.2~1.5s，下单 ~2s→~0.4s。用户实测「能抢过一些慢脚本了」。
  2. **标准配置固化 + 随包打包**（用户拍板：以他调好的为标准；打包「只带速度标准、标定清空」）：
     - `core/config.py` `DEFAULT_CONFIG` 速度标准化：speed 2.0 / snipe_speed 5.0 / idle_chance 0.0 /
       refresh_interval_sec 0.2 / shelf_load_min_sec 0.15（dry_run 仍 True、regions 仍 None——安全+待标定）。
     - `config.example.json` 同步标准值与新字段。
     - `build.py`：复制 exe 后新增 `_write_standard_config()`，用 `DEFAULT_CONFIG` 在发布目录写 config.json
       （单一来源、改默认即同步、不漂移；regions 空/watchlist 空/dry_run 演练）。**发布目录已有 config.json 则跳过不覆盖**
       （防覆盖用户标定）。spec 不动（配置仍放 exe 同级，不进 exe 内部）。
  - 已验证：5 文件 py_compile 通过；运行实测 DEFAULT_CONFIG 已标准化、发布 config.json 内容正确（标定空/演练/速度标准）、
    `_write_standard_config` 写入+跳过覆盖两条路径均 OK；example.json JSON 合法。**未真机端到端验证**（需用户开游戏自测）。
  - ⚠ 标定坐标若与上新后商品排序不符仍会点错（固定坐标老问题）；`shelf_load_min_sec` 太小可能没开始加载就截图→偶发漏识别，调大即可。
- **2026-06-21（最新）打 v1.0 exe：单文件 + 数据集中到 exe 同级文件夹**。用户要求「标定的图片和配置生成在一个文件夹里」。
  直接打包当前代码会坏三处，已修：
  1. **`mhxy/core/config.py`**：新增 `DATA_ROOT`——`sys.frozen` 时 = `Path(sys.executable).parent`（exe 同级），
     源码时 = 项目根。`PROJECT_ROOT/CONFIG_PATH/TEMPLATES_DIR/CAPTURES_DIR` 全跟随。
     **原因**：onefile 下 `__file__` 在临时目录 `%TEMP%\_MEIxxxx`，退出即清，标定数据会丢。
     `PROJECT_ROOT` 保留为 `DATA_ROOT` 别名，`vision.py`/`gui/app.py:231` 无需改。
  2. **`start.py`**：`main()` 开头加 `frozen = getattr(sys,'frozen',False)`，frozen 时**整段短路**
     提权/`ensure_deps`/`_relaunch_windowless`（这些是源码运行专用，exe 里会出错），直接走到 GUI。
  3. **打包配置**：新增 `梦幻秒装备.spec`（`collect_all('customtkinter')` 收 53 个资源含 3 主题 json，
     否则启动崩；`uac_admin=True` 嵌 manifest 双击请求管理员；`console=False` 无黑窗；onefile）
     + `build.py`（`python build.py` 一键打，产物复制到 `发布\梦幻秒装备_v1.0\梦幻秒装备.exe`）
     + `打包.bat`。build.py 已 `reconfigure(utf-8)` 防 GBK 控制台 print 崩。
  - 已验证：py_compile 全过；PyInstaller 打包成功（exe 66.9 MB 已生成并入交付夹）；
    warn 文件无关键模块缺失（只有 numpy._core.* 已知误报）；collect_all 确认 customtkinter 资源入包；
    模拟 `sys.frozen=True` 验证 DATA_ROOT 正确指向 exe 同级（PASS）。
  - **未验证**：exe 因 `uac_admin` 无法在 headless 下拉起（错误 740 需点 UAC），故**真机启动 GUI / 标定 / 识别未端到端测**，需用户双击自测。
  - 重打方式：改完代码 `python build.py` 或双击 `打包.bat`。换图标：把 .ico 放进项目，spec 里 `icon=` 填路径再重打。
- **2026-06-21（最新）GUI 流畅度优化：解决「上下滑动 + 标定时像重新渲染、拖沓」**。
  根因是直接读本机 `customtkinter 5.2.2` 源码 + `core/window.py` 定位的，不是猜的。改了 3 文件：
  1. **`gui/app.py`（主因）**：`App._tick` 原本每 ~1.2s 在**主线程**调 `game_win.locate()`，
     而它内部 `pygetwindow.getAllWindows()` 枚举所有窗口逐个读 title/宽高（每项一次 Win32 调用，几十 ms），
     周期性阻塞 UI → 滑动一顿一顿。改为后台守护线程定位、`self.after(0, ...)` 回主线程更新；
     连接状态加缓存 `_game_connected`，**仅在变化时**才动药丸控件（省无谓重绘）。新增 `_kick_locate/_apply_game_state`。
  2. **`gui/calibrate_dialog.py`**：`_grab_roi` 原来每次框选都 `app.withdraw()+deiconify()`，
     整窗重新映射会重画所有圆角控件 → 闪+拖。改成 `attributes("-alpha",0)` 透明化（alpha=0 不被 mss 截到，
     但不整窗重建），新增 `_set_alpha()` 助手；等待 0.25s→0.12s，并用 `try/finally` 保证异常也恢复显示。
  3. **`gui/theme.py`**：新增 `tune_scroll_speed()`，把 CTkScrollableFrame 滚轮步长从约 20px/格调到约 60px/格
     （库 win 下 `yscrollincrement=1`、每格 `delta/6=20` unit）；app 的两处 + calibrate 的 body 三个滚动区都接上。
  - 已验证：3 文件 `py_compile` 通过；**未真机端到端验证**（需开游戏自测滑动/标定手感）。
  - 若标定时偶发"没透明就截图（本助手出现在截图里）"，把 calibrate_dialog 那个 `time.sleep(0.12)` 调回 `0.25`。
- **2026-06-21 重做刷新机制：从「点固定刷新按钮」改成「每轮重进货架」**。
  现状：识别商品 OK，但旧的"刷新按钮"思路不成立（游戏没这按钮）。改动涉及 4 文件 + 1 CLI 备用：
  1. **`core/config.py`**：`regions` 用 `category_button`（左侧类别）+ `product_entry`（右侧商品条目）
     取代 `refresh_button`；`loop` 新增 `shelf_load_wait_sec`（进货架后等加载，默认 1.2s，带抖动）。
  2. **`tasks/sniper.py`**：主循环每轮调新增的 `_enter_shelf()`（点类别→等切换→点商品→等加载），
     再截 `listing` 识别；`preflight()` 增校验这两个新区域。购买流程 `_buy_sequence` 未动。
  3. **`gui/calibrate_dialog.py`**：标定项改为 货架/列表区域 / 商品类别按钮 / 商品条目 / 购买 / 确认。
  4. `config.example.json` 与 `tools/calibrate.py`（CLI 备用）同步新字段。
  - 兼容：旧 `config.json` 残留的 `refresh_button` 键无害（`_deep_merge` 自动补新字段为 None），
    但 `category_button`/`product_entry` 为 None，**用户必须重新标定这两个点**。
  - 已验证：全部 py `compileall`/`ast.parse` 通过、`config.example.json` JSON 合法；**未在真机端到端验证**。
  - 待用户实测调参：`shelf_load_wait_sec`（慢机/慢网调大，否则没加载完就截图会漏识别）；
    商品条目位置须确实固定（若类别下商品因上新而变排序，固定坐标会点错）。
- **2026-06-21 早些时候改了三件事（针对“完全不可用 + 黑窗 + 标定难用”）**：
  1. **去黑窗（启动）**：`start.py` 装好依赖后用 `pythonw` 无窗口重启自己，原控制台随即关闭——
     不再有黑色命令行窗口残留在 GUI 后面。
  2. **标定全部搬进 GUI（无黑窗）**：删掉了原来 `CREATE_NEW_CONSOLE` 调 `mhxy.tools.calibrate`
     的子进程黑窗。新增 `gui/roi_overlay.py`（全屏冻结截图上拖框选 ROI）+ `gui/calibrate_dialog.py`
     （界面内标定区域 / 加装备）。点 SniperPage 的「标定 / 加装备」即弹该对话框。
  3. 框选时会临时 withdraw 本助手所有窗口再截图，保证截图里只有游戏画面。
- **已验证**：compileall 通过；overlay 在 root 隐藏时能弹出、Esc 取消正常、
  合成拖拽事件坐标映射准确（拖 200×140 框得到 [left,top,200,140]）；CalibrateDialog 构建/渲染/无游戏分支 OK。
- **仍未在真实游戏画面上端到端验证**（标定/识别/点击需用户开着游戏自测）。
  让用户双击 `启动.bat`，走：标定（框 货架区域/类别按钮/商品条目/购买 + 加装备）→ 演练「开始」看 captures/ → 开实战再跑。
- DPI 一致性：overlay 用普通 tk.Toplevel（非 CTk）避开 customtkinter 缩放，物理像素与 mss 截图 1:1；
  若真机高缩放下仍偏，重点查这里。

## 怎么跑
- 用户侧：双击 `启动.bat`，界面里「标定/加装备」→ 演练「开始秒装备」看 captures/ → 开「实战」开关再跑。
- 停止：界面「停止」按钮，或鼠标甩屏幕左上角（pyautogui/SendInput FAILSAFE）。

## 环境备注
- Windows 11，PowerShell 主 shell；也有 Bash 工具。
- git 推送走 Clash 代理端口 7897（见全局 CLAUDE.md），本项目当前不是 git 仓库。
- 记忆目录有更详细背景：game-mhxy-shikong / sniper-design-decisions / project-architecture。
```
