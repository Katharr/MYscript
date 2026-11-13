# UI 3.0 修正验收记录（2026-11-13）

本次直接按 `22-ui-v3-polish.md` 实施，未采用工单编排，未提交或推送。保留原有工作区修改，标定入口、项目和保存流程不作调整。

## 实现

- 主题统一声明字体与逻辑字号，使用微软雅黑、PreferAntialias 和 PreferNoHinting。正常 Windows 进程确认字体存在，应用及控件解析为 Microsoft YaHei UI。原比例截图中小字笔画连续性改善；只声明样式表字体未消除原现象，渲染参数对比才出现明显改善。底层字体引擎原因未进一步断言。
- 顶栏按钮恢复静止边框、按下和选中状态，删除日志前竖线。原生任务标签保留操作与可访问性，关闭基线，按文字宽度绘制短选中线。
- 单一日志面板与整个左侧业务区组成水平 splitter，状态栏留在左侧。默认约三分之一宽，可拖动并恢复；正常旧宽度兼容，最小宽度随字体需求限制。
- 窄窗将同一日志面板转为全高覆盖，顶部留出原有全局操作行；日志停止按钮留在覆盖层底部。收起后归还空间。顶栏唯一按钮与 Esc 控制开关、焦点及未读提示。临时窄窗不覆盖宽窗偏好。

## 原生视觉证据

使用 `python tools/ui_polish_capture.py captures/ui-polish-final --auto-exit`，加载正式 `get_application` / `MainWindow`，使用临时配置、临时 QSettings、虚拟角色和模拟任务；不操作真实游戏或保存用户配置。

本机 Windows 原生平台，逻辑 DPI 96，DPR 1.5（系统 150%），字体 Microsoft YaHei UI。截图原始像素保留；系统窗口捕获可能按逻辑尺寸采样，字体判断以 Qt 原比例原图为依据。

- 修正前：[单项任务原图](../../captures/ui-polish-before-native/escort.png)
- 修正后：[日常](../../captures/ui-polish-final/1120-daily.png)、[单项任务](../../captures/ui-polish-final/1120-escort.png)、[通用工具](../../captures/ui-polish-final/1120-general.png)、[角色与窗口](../../captures/ui-polish-final/1120-role_window.png)、[设置](../../captures/ui-polish-final/1120-settings.png)、[标定中心](../../captures/ui-polish-final/1120-calibration.png)
- 窄窗：[覆盖展开](../../captures/ui-polish-final/800-escort.png)、[收起](../../captures/ui-polish-final/800-escort-closed.png)、[运行](../../captures/ui-polish-final/800-running.png)、[停止中](../../captures/ui-polish-final/800-stopping.png)、[无角色](../../captures/ui-polish-final/800-roles-0.png)、[多角色](../../captures/ui-polish-final/800-roles-5.png)
- [几何与控件命中结果](../../captures/ui-polish-final/checks.json)、[字体及环境](../../captures/ui-polish-final/environment.json)

六个页面在 1120×720、800×640、1440×900 下完成原生截图与布局检查。日志上下边界在主窗口坐标中与客户区对齐；全局按钮及日志停止入口通过 `childAt` 实际命中检查。原图检查可见按钮边框、短标签线和全高日志；窄窗下参数区保持原布局。

额外以独立原生 Qt 进程通过 `QT_SCALE_FACTOR` 模拟总 DPR 1、1.25、2，均完成六页截图和同样的命中、几何检查。证据位于 `captures/ui-polish-scale-1`、`ui-polish-scale-1.25`、`ui-polish-scale-2`。

**限制：没有切换用户 Windows 显示设置，因此真实系统 100%、125%、200% 尚未验收；模拟 DPR 不等同于这些系统档位。未执行真实游戏任务、真实唤窗或关闭游戏。**

## 回归

覆盖日志缓存、筛选、滚动跟随、重启偏好、宽窄切换、唯一开关与焦点、任务标签、运行锁定、角色选择及全关确认。新增折叠后顶栏宽度和未拖动默认宽度的重启检查。

入口与异步任务测试原先使用固定延时，发现回调尚未交付时可能错误断言并让事件循环一直运行；现改为有界等待真实结果，异常时让测试子进程失败退出。

此前针对性回归 44 项中 42 项通过、2 项失败，原始记录保留在 `captures/polish-focused-verified.txt`。用户同意续验后，本轮完成两项修正：

1. 补齐入口测试遗漏的 `deadline` 定义，继续使用有界等待扫描结果。
2. Esc 焦点测试原来选中了无角色时被禁用的“开始”按钮，它本来不能获得焦点。改用启用的“实战模式”控件，并在按键前确认窗口激活及控件确已获得焦点，避免错误测试前提。

两项针对性测试均通过：[结果](../../captures/polish-resume-target.txt)。本轮未继续修改界面生产代码。

实际 Windows 窗口通过电脑操作工具发送 Esc 后，日志入口从“收起运行日志”变为“展开运行日志”，确认可以收起。工具返回的焦点字段仍显示此前的次数控件，与自动测试的焦点结果不一致。**真机焦点返回仍属未确认项**，不据此宣称通过，也未追加猜测性修补。

本轮仅执行一次最终全量回归 `python -m unittest discover -t . -v`：**830 项全部通过**，耗时 543.332 秒，进程退出码 0。结果见 [完整日志](../../captures/polish-resume-full.txt)。末尾出现的依赖拒绝打包、临时路径写入失败文案来自对应护栏测试，不是本轮测试失败或实际发布操作。

`git diff --check` 通过。保留现有工作区修改，未提交或推送。

当前结论：自动回归已完成；150% 原生视觉检查及实际 Esc 收起已完成；真机焦点返回、真实系统 100% / 125% / 200% 缩放仍未确认，因此不宣称所有真机验收项全部通过。
