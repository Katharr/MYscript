"""通用标定目录、活动逐项覆盖及只读解析；不读写图片、不写盘。

公共名称是标定项的身份；BINDINGS 中的旧任务字段仅作为运行接口适配。
tasks.common 保存公共资产，任务原有非空标定自然视为本活动覆盖。
calibration_modes 显式区分继承与区域的「整窗检测」覆盖，避免 None 歧义。
"""

import copy
import re
from pathlib import Path

from . import calib_profiles as profiles
from . import config as cfg_mod


COMMON_TASK = "common"
COMMON_CALIBRATION = {
    "regions": [
        ("scene", "主识别区", "框住主要识别范围；可设为整窗检测", True),
        ("activity_list", "活动列表区域", "框住活动卡片列表，不含页面外区域"),
    ],
    "templates": [
        ("battle_indicator", "战斗界面标志", "框住战斗界面独有且稳定的元素"),
        ("activity_join", "活动参加按钮", "只框活动卡片右侧的「参加」按钮"),
        ("daily_activity", "日常活动按钮", "框住活动页的「日常活动」按钮文字和边框"),
        ("dialog_confirm", "弹窗「确认」按钮", "框住普通弹窗的「确认」按钮"),
        ("dialog_ok", "弹窗「确定」按钮", "框住普通弹窗的「确定」按钮"),
        ("dialog_cancel", "弹窗「取消」按钮", "框住普通弹窗的「取消」按钮"),
    ],
    "watchlist": False,
}

BINDINGS = {
    "treasure_map": {
        "regions": {"scene": "scene", "activity_list": "activity_list"},
        "templates": {"flag_battle": "battle_indicator", "flag_join": "activity_join",
                      "daily_activity": "daily_activity"},
    },
    "escort": {
        "regions": {"scene": "scene", "activity_list": "activity_list"},
        "templates": {"escort_battle": "battle_indicator", "escort_join": "activity_join",
                      "escort_confirm": "dialog_confirm", "daily_activity": "daily_activity"},
    },
    "secret_realm": {
        "regions": {"scene": "scene", "activity_list": "activity_list"},
        "templates": {"sr_battle": "battle_indicator", "sr_join": "activity_join",
                      "sr_confirm": "dialog_ok", "daily_activity": "daily_activity"},
    },
    "taohaiqu": {
        "regions": {"scene": "scene", "activity_list": "activity_list"},
        "templates": {"thq_join": "activity_join", "daily_activity": "daily_activity"},
    },
    "activity": {"templates": {"daily_activity": "daily_activity"}},
}


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _group(group):
    if group not in ("regions", "templates"):
        raise ValueError("标定分组必须为 regions 或 templates")
    return group


def canonical_key(task, group, key):
    """返回公共标定身份；专属标定项返回 None。"""
    _group(group)
    if task == COMMON_TASK:
        return key if any(row[0] == key for row in COMMON_CALIBRATION[group]) else None
    return BINDINGS.get(task, {}).get(group, {}).get(key)


def supports_common(task, group, key):
    return task != COMMON_TASK and canonical_key(task, group, key) is not None


def _mode(tc, group, key):
    return _mapping(_mapping(tc.get("calibration_modes")).get(group)).get(key)


def _profile(tc, group, key):
    metadata = _mapping(tc.get("regions_calib" if group == "regions" else "templates_calib"))
    if key in metadata:
        return metadata[key]
    # Old regions share a task profile; old template records may also only have
    # that task profile. Metadata explicitly cleared to None must stay cleared.
    return _mapping(tc.get("calib")).get("profile")


def _configured(tc, group, key):
    mode = _mode(tc, group, key)
    if mode == "inherit":
        return False
    if mode == "override":
        return True
    return bool(_mapping(tc.get(group)).get(key))


def resolve_item(cfg, task, group, key):
    """解析一个标定项与来源；图片损坏仍保留其路径，由读取方报告错误。

    profile 与 value 来自同一层，不会借用被覆盖项或其它活动的尺寸。
    返回值及其 value 均独立于原配置，调用方不能借此修改持久化数据。
    """
    _group(group)
    tc = cfg_mod.task_config(cfg, task)
    canonical = canonical_key(task, group, key)
    result = {"task": task, "group": group, "key": key,
              "canonical_key": canonical, "value": None, "source": "missing",
              "profile": None, "source_task": None, "source_key": None}
    if _configured(tc, group, key):
        result.update(value=copy.deepcopy(_mapping(tc.get(group)).get(key)),
                      source="common" if task == COMMON_TASK else "local",
                      profile=_profile(tc, group, key), source_task=task, source_key=key)
    elif canonical is not None and task != COMMON_TASK:
        common = cfg_mod.task_config(cfg, COMMON_TASK)
        if _configured(common, group, canonical):
            result.update(value=copy.deepcopy(_mapping(common.get(group)).get(canonical)),
                          source="common", profile=_profile(common, group, canonical),
                          source_task=COMMON_TASK, source_key=canonical)
        elif task != "activity" and group == "templates" and canonical == "daily_activity":
            # This namespace predates the common page and already served every
            # activity. Reuse its original asset/profile without materializing
            # copies in the newly bound task blocks.
            legacy = cfg_mod.task_config(cfg, "activity")
            if _configured(legacy, group, canonical):
                result.update(value=copy.deepcopy(_mapping(legacy.get(group)).get(canonical)),
                              source="common", profile=_profile(legacy, group, canonical),
                              source_task="activity", source_key=canonical)
    return result


def effective_task_config(cfg, task):
    """供展示、自检、运行读取的独立快照；禁止将解析后的快照整体写回任务。"""
    tc = cfg_mod.task_config(cfg, task)
    for group, bindings in BINDINGS.get(task, {}).items():
        values = dict(_mapping(tc.get(group)))
        metadata_key = "regions_calib" if group == "regions" else "templates_calib"
        metadata = dict(_mapping(tc.get(metadata_key)))
        for key in bindings:
            item = resolve_item(cfg, task, group, key)
            values[key], metadata[key] = item["value"], item["profile"]
        tc[group], tc[metadata_key] = values, metadata
    return tc


def _validated_profile(cfg, pid):
    if pid is None:
        return None
    if isinstance(pid, bool) or not isinstance(pid, (str, int)):
        raise ValueError("标定尺寸组 ID 无效")
    profile = profiles.get(cfg, pid)
    if profile is None:
        raise ValueError("标定尺寸组不存在，无法记录归属")
    return profile["id"]


def set_item(cfg, task, group, key, value, pid=None):
    """只更新一个原始标定项；不切换尺寸、不写盘，由调用方在草稿中提交。"""
    _group(group)
    profile = _validated_profile(cfg, pid)
    tc = cfg.setdefault("tasks", {}).setdefault(task, {})
    tc.setdefault(group, {})[key] = copy.deepcopy(value)
    tc.setdefault("calibration_modes", {}).setdefault(group, {})[key] = "override"
    metadata_key = "regions_calib" if group == "regions" else "templates_calib"
    tc.setdefault(metadata_key, {})[key] = profile
    return resolve_item(cfg, task, group, key)


def restore_common(cfg, task, group, key):
    """删除本活动一个覆盖，保留公共资产及其它标定项。"""
    if not supports_common(task, group, key):
        raise ValueError("该标定项没有通用标定可继承")
    tc = cfg.setdefault("tasks", {}).setdefault(task, {})
    _mapping(tc.get(group)).pop(key, None)
    metadata_key = "regions_calib" if group == "regions" else "templates_calib"
    _mapping(tc.get(metadata_key)).pop(key, None)
    tc.setdefault("calibration_modes", {}).setdefault(group, {})[key] = "inherit"
    return resolve_item(cfg, task, group, key)


def size_for(cfg, task, group, key):
    """返回最终标定自己的尺寸；无有效记录时保持旧配置的激活尺寸兜底。"""
    item = resolve_item(cfg, task, group, key)
    profile = profiles.get(cfg, item["profile"])
    return list(profile["size"]) if profile else profiles.active_size(cfg)


def _path_identity(path):
    if not isinstance(path, (str, Path)) or not str(path):
        return None
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = cfg_mod.PROJECT_ROOT / candidate
    # Configuration is Windows-owned even when pure tests run on another OS.
    return str(candidate.resolve()).replace("\\", "/").casefold()


def template_path(cfg, task, key):
    """选择重标定写入路径，活动覆盖永远不会覆写通用图片。

    已有且独立的专属路径保持稳定。若旧配置曾手工共用同一图，重标定时
    另选独立路径；不修改原文件，也不迁移资产。
    """
    tc = cfg_mod.task_config(cfg, task)
    current = _mapping(tc.get("templates")).get(key)
    common_paths = {_path_identity(path) for path in
                    _mapping(cfg_mod.task_config(cfg, COMMON_TASK).get("templates")).values()}
    # The pre-existing shared activity namespace is also an asset boundary.
    activity = _mapping(cfg_mod.task_config(cfg, "activity").get("templates"))
    if task != "activity":
        common_paths.update(_path_identity(path) for path in activity.values())
    if task != COMMON_TASK:
        common_paths.update(_path_identity(path)
                            for name, block in _mapping(cfg.get("tasks")).items()
                            for other_key, path in _mapping(_mapping(block).get("templates")).items()
                            if name != task or other_key != key)
    common_paths.discard(None)
    if current and (task == COMMON_TASK or _path_identity(current) not in common_paths):
        # A common item must not adopt another item's existing local asset either.
        if task != COMMON_TASK:
            return str(current)
        local_paths = {_path_identity(path)
                       for name, block in _mapping(cfg.get("tasks")).items()
                       if name != COMMON_TASK
                       for path in _mapping(_mapping(block).get("templates")).values()}
        local_paths.update(_path_identity(path) for other_key, path in
                           _mapping(tc.get("templates")).items() if other_key != key)
        if _path_identity(current) not in local_paths:
            return str(current)
    safe_task = re.sub(r"[^A-Za-z0-9_-]", "_", task)
    safe_key = re.sub(r"[^A-Za-z0-9_-]", "_", key)
    stem = f"tm_common_{safe_key}" if task == COMMON_TASK else f"tm_{safe_key}"
    if task not in (COMMON_TASK, "activity") and key == "daily_activity":
        stem = f"tm_{safe_task}_{safe_key}"
    candidate = f"templates/{stem}.png"
    # Manually configured public paths can equal the default local destination.
    # Include task identity and then a suffix until the result is independent.
    occupied = common_paths
    if task == COMMON_TASK:
        occupied = {_path_identity(path)
                    for name, block in _mapping(cfg.get("tasks")).items()
                    if name != COMMON_TASK
                    for path in _mapping(_mapping(block).get("templates")).values()}
        occupied.update(_path_identity(path) for other_key, path in
                        _mapping(tc.get("templates")).items() if other_key != key)
    suffix = 0
    while _path_identity(candidate) in occupied:
        suffix += 1
        candidate = f"templates/{stem}_{safe_task}_{suffix}.png"
    return candidate
