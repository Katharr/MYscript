"""运镖参数页；次数、时限和运行流程沿用 escort 命名空间。"""

from ..tasks.escort import EscortTask, _REQUIRED_FLAGS
from .treasure_map_page import _ActivityPage


class EscortPage(_ActivityPage):
    TASK_NAME = "escort"
    TITLE = "运镖"
    LOG_SOURCE = "运镖"
    START_TEXT = "开始运镖"
    TASK_CLASS = EscortTask
    REQUIRED_TEMPLATES = tuple(_REQUIRED_FLAGS)
    PARAMETERS = (
        ("max_escorts", "每号运镖次数（押满即停）", 3, 1, True),
        ("time_limit_min", "时间上限（分钟，0 = 不限）", 30, 0, False),
    )
