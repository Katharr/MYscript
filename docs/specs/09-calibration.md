# Spec 09：迁移标定中心和框选浮层

## 目标

迁移标定入口、尺寸组、模板缩略图和 ROI 框选，不改变标定数据格式。

## 修改范围

- 新增 `mhxy/qtui/calibration_page.py`、`calibration_dialog.py`、`roi_overlay.py`。
- 配套修改 `mhxy/qtui/window.py` 接入标定中心；修改秒装备、活动、副本和背包页的标定入口，统一打开可编辑的 Qt 标定对话框，保存后刷新所属页。
- 物品清单与自由点击复用 Qt ROI 框选；新增 Qt 标定回归测试，更新本说明与完成记录。上述接线和验证属于本阶段必要范围。
- 复用 `core.calib_profiles`、`calibration_assets`、`core.window`。
- 必须保留现有标定测试覆盖的保存、取消、删除和回滚行为。

## 实现

- 尺寸组最多三个；当前组绿，其余灰；激活尺寸继续镜像到 `targets.base_size`。
- 每个任务列出必要区域和模板；缩略图异步加载，失败显示未标定。
- ROI 浮层只返回相对坐标和裁剪图；取消不写配置。
- 所有写盘只走 `core.config.save_config`。
- 图片提交复用 `calibration_assets.commit_image` 的回滚流程；队长 ID 复用 `leader_history.commit_calibration`，同步保留历史库。
- 区域、模板、装备与物品操作分别在独立草稿上提交；取消框选、命名或尺寸替换均不发布草稿，保存失败保留原配置与图片。

## 验收

- 区域、模板、装备和物品标定均能保存、取消、重新打开。
- 尺寸组切换、删除、替换遵守最多三组规则。
- ROI 坐标与旧测试一致；中文路径可用。
- `test_calib_profiles.py`、`test_calibration_assets.py`、`test_roi_overlay_actions.py`、`test_region_calibration.py` 和全量测试通过。

## 不做

- 不迁移识别算法。
- 不按尺寸复制模板文件。

## 完成说明

- 主窗口新增标定中心入口；秒装备、宝图、运镖、秘境、刷副本及背包页接入统一 Qt 标定编辑器。共享组队、活动页及启动登录标定可在任务选择器中访问。
- 物品与自由点击原有截图入口改为复用冻结屏幕浮层；浮层保留原始像素，按屏幕 DPI 换算物理坐标，再按落点参照窗口换为相对坐标。
- 尺寸组变更仅走 `calib_profiles`；普通图片与队长历史分别复用既有资产提交入口，保留保存失败的配置和图片回滚。
- 新增 `tests/test_qt_calibration.py`，扩展 Qt 独立入口冒烟测试。验证采用临时配置与资产、假游戏窗口和 offscreen Qt，不操作真实游戏；实际多显示器不同 DPI 的游戏框选仍需在目标桌面验证。
- 验收结果：`python -m unittest discover -t . -v` 共 529 项通过（含 22 项新增 Qt 标定回归测试）。
