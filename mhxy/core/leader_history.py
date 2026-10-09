# -*- coding: utf-8 -*-
"""队长ID「当前 + 最近3历史」库；零 GUI 依赖。

激活图永远为 templates/tm_leader_id.png，切换只覆盖字节、不改路径；
历史复用四个固定槽，最新在前。空历史将 templates.leader_id 置 None。
get_history/get_active_index 只读快照；图库 reload 后显式 initialize，
仅有变化时保存。push/activate/delete 不迁移，避免首次标定重复收编。

资产操作先留内存字节快照，使用同目录临时文件原子覆盖，配置仍只走
save_config；复制、删除或保存失败恢复资产并显式报错。此边界不提供跨文件
崩溃事务。commit_calibration 覆盖标定前状态；兼容 push 仅覆盖其调用前状态。
"""
import copy
import os
import tempfile
import time

from . import config as cfg_mod

TASK = "teaming"
ACTIVE_REL = "templates/tm_leader_id.png"
SLOT_REL = "templates/tm_leader_id_slot{}.png"
MAX_SLOTS = 4


class LeaderHistoryError(RuntimeError):
    """资产或配置未提交；恢复失败会在消息中明确指出。"""


def _all_slots():
    return [SLOT_REL.format(i) for i in range(MAX_SLOTS)]


def _now_label():
    return time.strftime("%m-%d %H:%M")


def _abs(rel):
    return rel if os.path.isabs(rel) else str(cfg_mod.PROJECT_ROOT / rel)


def _exists(rel):
    return os.path.exists(_abs(rel))


def _read_bytes(rel):
    try:
        with open(_abs(rel), "rb") as stream:
            return stream.read()
    except FileNotFoundError:
        return None


def _write_bytes(rel, data):
    """同目录原子字节覆盖；中文路径由 Python 文件 API 处理。"""
    path = _abs(rel)
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(dir=parent, prefix=".leader-", suffix=".tmp", delete=False) as stream:
            tmp = stream.name
            stream.write(data)
        os.replace(tmp, path)
    finally:
        if tmp is not None and os.path.exists(tmp):
            os.remove(tmp)


def _copy(src_rel, dst_rel):
    """严格字节复制，不经过图像解码/编码；失败返回 False。"""
    try:
        data = _read_bytes(src_rel)
        if data is None:
            return False
        _write_bytes(dst_rel, data)
        return True
    except OSError:
        return False


def _remove(rel):
    try:
        os.remove(_abs(rel))
    except FileNotFoundError:
        pass


def _valid_index(idx, history):
    return type(idx) is int and 0 <= idx < len(history)


def _load(cfg):
    """读取独立快照并投影四槽/active；不复制、不保存、不修改 cfg。"""
    tc = cfg_mod.task_config(cfg, TASK)
    history = copy.deepcopy(list(tc.get("leader_id_history") or [])[:MAX_SLOTS])
    active = tc.get("leader_id_active", 0)
    if not _valid_index(active, history):
        active = 0
    return tc, history, active


def _slot(item):
    slot = item.get("slot")
    if slot not in _all_slots():
        raise LeaderHistoryError("历史槽位无效。")
    return slot


def _prepared(tc, history, active):
    tc = copy.deepcopy(tc)
    tc["leader_id_history"] = copy.deepcopy(history[:MAX_SLOTS])
    tc["leader_id_active"] = active if _valid_index(active, history) else 0
    tc.setdefault("templates", {})["leader_id"] = ACTIVE_REL if history else None
    return tc


def _commit(cfg, tc, history, active, operations=()):
    """operations 为 copy(src,dst)、write(bytes,path) 或 remove(path)；快照变更目标。"""
    prepared = _prepared(tc, history, active)
    draft = copy.deepcopy(cfg)
    cfg_mod.set_task_config(draft, TASK, prepared)
    snapshots = {}
    try:
        for operation in operations:
            path = operation[-1]
            if path not in snapshots:
                snapshots[path] = _read_bytes(path)
        for operation in operations:
            if operation[0] == "copy":
                if not _copy(operation[1], operation[2]):
                    raise LeaderHistoryError("复制队长图失败。")
            elif operation[0] == "write":
                _write_bytes(operation[2], operation[1])
            else:
                _remove(operation[1])
        cfg_mod.save_config(draft)
    except Exception as exc:
        restore_failed = False
        for path, data in snapshots.items():
            try:
                if _read_bytes(path) == data:
                    continue
                if data is None:
                    _remove(path)
                else:
                    _write_bytes(path, data)
            except Exception:
                restore_failed = True
        detail = "旧资产恢复失败，请检查队长图库。" if restore_failed else "已保留原配置与操作前资产。"
        raise LeaderHistoryError(f"队长历史提交失败：{detail}") from exc
    cfg_mod.set_task_config(cfg, TASK, prepared)
    return copy.deepcopy(prepared["leader_id_history"])


def get_history(cfg):
    """返回独立历史快照，不触发迁移或保存。"""
    return _load(cfg)[1]


def get_active_index(cfg):
    """返回当前下标；无效指针只在返回值中投影为 0。"""
    return _load(cfg)[2]


def initialize(cfg):
    """显式、幂等初始化：收编旧图、截断历史、修正指针和就绪字段。"""
    tc, history, active = _load(cfg)
    operations = []
    if not history and _exists(ACTIVE_REL):
        slot = SLOT_REL.format(0)
        history = [{"slot": slot, "label": _now_label()}]
        active = 0
        operations.append(("copy", ACTIVE_REL, slot))
    elif history and (type(tc.get("leader_id_active", 0)) is not int
                      or tc.get("leader_id_active", 0) != active):
        # 越界/非法指针归零时，激活字节也必须对应新的第 0 项。
        operations.append(("copy", _slot(history[0]), ACTIVE_REL))
    prepared = _prepared(tc, history, active)
    if not operations and prepared == tc:
        return copy.deepcopy(history)
    return _commit(cfg, tc, history, active, operations)


def _append_history(tc):
    history = copy.deepcopy(list(tc.get("leader_id_history") or [])[:MAX_SLOTS])
    used = {_slot(item) for item in history}
    free = next((slot for slot in _all_slots() if slot not in used), None)
    if free is None:
        free = _slot(history[-1])
        history = history[:-1]
    history.insert(0, {"slot": free, "label": _now_label()})
    return history, free


def commit_calibration(cfg, draft, image_bytes):
    """草稿含尺寸归属；一次保存新图、历史与配置，同步失败恢复标定前资产。

    不迁移旧激活图，不提供跨文件崩溃事务；成功后才更新调用方内存配置。
    """
    if not isinstance(image_bytes, bytes) or not image_bytes:
        raise LeaderHistoryError("队长模板编码为空。")
    working = copy.deepcopy(draft)
    tc = cfg_mod.task_config(working, TASK)
    history, slot = _append_history(tc)
    result = _commit(working, tc, history, 0,
                     [("write", image_bytes, ACTIVE_REL), ("copy", ACTIVE_REL, slot)])
    cfg.clear()
    cfg.update(working)
    return result


def push_after_calibrate(cfg):
    """兼容已保存新图的调用方；恢复仅覆盖 push 调用前，不触发迁移。"""
    tc, _history, _active = _load(cfg)
    history, free = _append_history(tc)
    return _commit(cfg, tc, history, 0, [("copy", ACTIVE_REL, free)])


def activate(cfg, idx):
    """复制选中槽到固定激活图并置顶；失败显式报错、零配置提交。"""
    tc, history, _active = _load(cfg)
    if not _valid_index(idx, history):
        raise LeaderHistoryError("队长历史下标无效。")
    chosen = history.pop(idx)
    history.insert(0, chosen)
    return _commit(cfg, tc, history, 0, [("copy", _slot(chosen), ACTIVE_REL)])


def delete(cfg, idx):
    """删除记录及槽；删除当前图时先准备替换，删空同时清激活图。"""
    tc, history, active = _load(cfg)
    if not _valid_index(idx, history):
        raise LeaderHistoryError("队长历史下标无效。")
    removed = history.pop(idx)
    operations = []
    if not history:
        operations.append(("remove", ACTIVE_REL))
        active = 0
    elif idx == active:
        operations.append(("copy", _slot(history[0]), ACTIVE_REL))
        active = 0
    elif idx < active:
        active -= 1
    operations.append(("remove", _slot(removed)))
    return _commit(cfg, tc, history, active, operations)
