---
description: 审查 MYscript 项目未提交的代码改动（git diff），检查 bug、结构、性能、行为变更
---

# Code Review

审查当前工作目录下未提交的代码变更（git diff unreviewed changes）。

## 流程

1. 运行 `git diff` 查看未暂存的改动
2. 运行 `git diff --cached` 查看已暂存的改动
3. 运行 `git status --short` 识别 untracked 文件
4. 对每个修改的文件，Read 其完整内容以理解上下文
5. 对 untracked 文件，Read 其完整内容

## 审查要点

### Bugs（主要关注）
- 逻辑错误、off-by-one、不正确的条件判断
- if-else 守卫：缺少守卫、错误分支、不可达代码
- 边界情况：null/空/未定义输入、错误条件、竞态条件
- 错误的错误处理：吞异常、不恰当地抛异常、未捕获的错误返回类型

### 结构
- 代码是否符合已有模式和约定（参见 CLAUDE.md 的架构/任务模块约定）
- 是否应该使用已有抽象（rotation/scan/teaming/inventory）但没用的
- 可用 early return 或提取函数扁平化的过度嵌套

### 性能
- 仅在有明显问题时标记：无界数据的 O(n²)、N+1 查询、热路径上的阻塞 I/O

### 行为变更
- 如果引入了行为变化，需明确说明（尤其可能是无意的）

## 输出格式

结构化审查：Overview → Changes → Issues（如有）→ Summary

## 项目特有约束

参照 CLAUDE.md 中这些约束进行校验：
- 命中即抢不上 OCR（约束 1）
- 拟人化要求（约束 2）
- dry_run 安全默认（约束 4）
- 活动列表卡片列内找「参加」（约束 7）
- GUI 换行铁律与 bind_wraplength（约束 8）
- 多开窗口同尺寸 + activate 前切前台（约束 9）
- 日志统一全局面板，不另造日志框
- 加新任务遵循注册模式