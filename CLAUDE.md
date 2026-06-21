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
  tools/
    calibrate.py 旧的命令行标定（cv2.selectROI，已不被 GUI 调用，仅留作 CLI 备用）
  gui/
    theme.py            配色/字体/圆角常量（改这里整体换肤；深色现代风）
    app.py              主窗口：侧边导航 + SniperPage/SettingsPage/AboutPage
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
