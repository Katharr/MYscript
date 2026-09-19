# -*- coding: utf-8 -*-
"""开发自检脚本（不参与运行、不被 GUI 导入）。

跑法（项目根目录）：
    python -m tools.check_calib_profiles      # 标定尺寸组纯逻辑（加减组/镜像/满 3 组/stale）
    python -m tools.check_scale_normalize     # 方案二：预缩放 + 坐标反算 + 动态阈值（合成图，不碰屏幕）
"""

