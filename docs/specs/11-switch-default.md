# Spec 11：切换默认入口、移除旧 UI 依赖、性能验收

## 目标

Qt UI 完成后再切换默认启动入口，清理 compact/旧 Tk 路径，并对照 Spec 00 验收性能。

## 修改范围

- `start.py`、`requirements.txt`、`打包工具/build.py`、PyInstaller spec。
- 仅在前置页面全部完成后删除旧 UI 的未使用入口。
- 新增或更新启动、打包和性能测试。

## 实现

- 启动流程直接创建一个 `QApplication` 和一个 Qt 主窗口；无游戏窗口也进入角色与窗口页。
- 不创建 Tk splash、compact panel 或第二个 root。
- 保留管理员提权、依赖检查、错误日志、配置/模板同目录规则。
- 运行时仍默认演练；发布配置仍把任务 `dry_run` 设为 true。
- 用 Spec 00 同样条件重测冷启动、首次页、重复切页和内存；记录新文档，不修改旧基线。

## 验收

- 源码启动、无窗口启动、有窗口启动、管理员启动均可用。
- PyInstaller 产物包含 Qt 依赖、`config.json` 和 `templates/`，可在干净目录启动。
- 原有全量测试通过，且 Qt 启动冒烟测试通过。
- 对比基线：首次可操作时间、重复切页 P95、峰值内存、主线程最大卡顿均有数据。

## 停止条件

- 若 Qt 首屏或切页没有达到可接受体感，只优化 Qt 页面生命周期和模型刷新，不回退到重新创建整页。
- 若打包体积或启动时间明显恶化，先记录数据，不顺手改任务核心。

## 完成记录（2026-10-16）

- `start.py` 默认创建单个 `QApplication` 与 `mhxy.qtui.window.MainWindow`；移除启动 splash、compact 与第二个 root，保留提权、依赖安装确认、pythonw 无窗口重启、DPI、错误日志和同目录数据规则。
- `requirements.txt` 去除 CustomTkinter；`requirements-dev.txt` 保留历史 Tk 回归依赖。Qt 发布 spec 排除 Tk/QML，收集 Qt Windows 平台插件、OCR YAML 与 ONNX 模型。
- 发布脚本先做依赖预检，交付目录始终包含 `梦幻小助手.exe`、`config.json`、`templates/`；写入发布配置时所有任务安全默认 `dry_run=true`，组队显式动作维持原有安全语义。
- 新增 `--smoke-report` 只读启动诊断，校验 12 个 Qt 页面、OCR、窗口枚举、Qt-only 模块和外置数据目录。
- 性能原始数据：[qt-raw-20261016.json](qt-raw-20261016.json)。当前三窗口实测：冷启动中位数 1414.05 ms / 峰值工作集 136.71 MB；重复切页 P95 中位数 73.545 ms；悬浮恢复中位数 59.17 ms；主线程最长卡顿中位数 2136.21 ms。无窗口场景因本机始终存在 3 个目标窗口，未伪造数据，运行 `python docs/specs/measure_qt.py` 于无窗口环境补测。
- 验收：`python -m unittest discover -t . -v`（557 项通过）；Python 3.11 隔离环境完成 PyInstaller 构建，产物约 120.6 MB，并包含 Qt 平台插件与 OCR 资源。
