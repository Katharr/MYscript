"""秘境参数页；轮数、时限和运行流程沿用 secret_realm 命名空间。"""

from ..tasks.secret_realm import SecretRealmTask, _REQUIRED_FLAGS
from .treasure_map_page import _ActivityPage


class SecretRealmPage(_ActivityPage):
    TASK_NAME = "secret_realm"
    TITLE = "秘境降妖"
    LOG_SOURCE = "秘境"
    START_TEXT = "开始秘境"
    TASK_CLASS = SecretRealmTask
    REQUIRED_TEMPLATES = tuple(_REQUIRED_FLAGS)
    PARAMETERS = (
        ("max_runs", "每号秘境轮数", 1, 1, True),
        ("time_limit_min", "时间上限（分钟，0 = 不限）", 45, 0, False),
        ("battle_timeout_sec", "单轮战斗时限（秒）", 1800, 1, False),
    )
