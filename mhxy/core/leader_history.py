# -*- coding: utf-8 -*-
"""
队长ID「当前 + 最近3历史」库：多张候选模板 + 激活指针 + 环形淘汰。纯逻辑、零 GUI 依赖、可单测。

关键约定（改前务必读懂，否则极易破坏组队识别）：
- 激活图路径【永远】是 templates/tm_leader_id.png（teaming._load_templates 与
  calibrate_dialog.calibrate_template_direct 都写死读/写这条）。切换队长ID =
  把选中历史图的【字节复制覆盖】这个同名文件，而【不是】改 config 里的路径串。
  这样 TeamFormation、标定入口都对「文件内容被换」无感 —— 核心零改、零回归。
- 历史存 4 个固定物理槽 templates/tm_leader_id_slot0..3.png（纯 ASCII 名，避开中文路径坑），
  环形复用：淘汰最旧 = 复用其槽位、覆盖字节，自然 GC，不堆时间戳垃圾。
- config（tasks.teaming 下）：
    leader_id_history = [{slot, label}, ...]   最新在前，最多 4 项（当前1 + 历史3）
    leader_id_active  = 指向 history 的下标（仅 UI 高亮用；约定 history[active] 的字节 == 激活图）
  并由本模块统一维护 templates.leader_id：历史非空→指向激活图、历史空→None
  （就绪度判定只看该字段是否非空，不看文件存在，故空历史必须置 None）。
- 迁移兜底只在【读】(get_history/get_active_index) 触发；【写】(push/activate/delete) 绝不迁移，
  否则首次标定会被「迁移 + push」复制成两份。
"""

import os
import time

from . import config as cfg_mod
from . import vision

TASK = "teaming"
ACTIVE_REL = "templates/tm_leader_id.png"        # 激活图：永不改名
SLOT_REL = "templates/tm_leader_id_slot{}.png"   # 历史物理槽
MAX_SLOTS = 4                                      # 当前1 + 历史3


def _all_slots():
    return [SLOT_REL.format(i) for i in range(MAX_SLOTS)]


def _now_label():
    return time.strftime("%m-%d %H:%M")


def _abs(rel):
    return rel if os.path.isabs(rel) else str(cfg_mod.PROJECT_ROOT / rel)


def _exists(rel):
    return os.path.exists(_abs(rel))


def _copy(src_rel, dst_rel):
    """复制图片字节（经 cv2 imdecode/imencode，兼容中文路径，不用 shutil）。成功返回 True。"""
    img = vision.load_template(src_rel)
    if img is None:
        return False
    return bool(vision.save_image(dst_rel, img))


def _remove(rel):
    try:
        p = _abs(rel)
        if os.path.exists(p):
            os.remove(p)
    except OSError:
        pass


# ----------------------------------------------------------------------
def _load(cfg, migrate=True):
    """读取 (tc, history, active)。migrate=True 时做一次性迁移兜底（老用户只有单张激活图、
    还没历史 → 收编成 history[0]）。不落盘（由调用方决定何时 save）。"""
    tc = cfg_mod.task_config(cfg, TASK)
    history = list(tc.get("leader_id_history") or [])
    active = tc.get("leader_id_active", 0)
    if migrate and not history and _exists(ACTIVE_REL):
        slot = SLOT_REL.format(0)
        if _copy(ACTIVE_REL, slot):
            history = [{"slot": slot, "label": _now_label()}]
            active = 0
    if not (0 <= active < len(history)):
        active = 0
    return tc, history, active


def _save(cfg, tc, history, active):
    """落盘 history/active，并同步 templates.leader_id（历史空则置 None，让就绪度判定正确）。"""
    history = history[:MAX_SLOTS]
    if not (0 <= active < len(history)):
        active = 0
    tc["leader_id_history"] = history
    tc["leader_id_active"] = active
    tpl = tc.setdefault("templates", {})
    tpl["leader_id"] = ACTIVE_REL if history else None
    cfg_mod.set_task_config(cfg, TASK, tc)
    cfg_mod.save_config(cfg)
    return history


# ----------------------------------------------------------------------
def get_history(cfg):
    """返回历史列表 [{slot, label}, ...]（含一次性迁移兜底，会落盘以稳定状态）。"""
    tc, history, active = _load(cfg, migrate=True)
    return _save(cfg, tc, history, active)


def get_active_index(cfg):
    _tc, _history, active = _load(cfg, migrate=True)
    return active


def push_after_calibrate(cfg):
    """标定成功后（新图已被 calibrate 写进激活图 tm_leader_id.png）把它收进历史并设为当前；
    环形淘汰最旧。返回新 history。调用前请确保 cfg 已 reload 到最新（calibrate 自行写过盘）。"""
    tc, history, active = _load(cfg, migrate=False)
    used = {h.get("slot") for h in history}
    free = next((s for s in _all_slots() if s not in used), None)
    if free is None:                       # 无空槽：复用最旧那项的槽（环形，物理文件被覆盖）
        history.pop()                      # 最旧在末尾
        used = {h.get("slot") for h in history}
        free = next((s for s in _all_slots() if s not in used), SLOT_REL.format(0))
    if not _copy(ACTIVE_REL, free):        # 复制失败：尽量不破坏现状
        return _save(cfg, tc, history, active)
    history.insert(0, {"slot": free, "label": _now_label()})
    return _save(cfg, tc, history, 0)


def activate(cfg, idx):
    """把 history[idx] 设为当前：复制其槽位字节覆盖激活图，并重排到 history[0]。返回新 history。"""
    tc, history, active = _load(cfg, migrate=False)
    if not (0 <= idx < len(history)):
        return _save(cfg, tc, history, active)
    chosen = history[idx]
    if not _copy(chosen.get("slot"), ACTIVE_REL):
        return _save(cfg, tc, history, active)
    history.pop(idx)
    history.insert(0, chosen)
    return _save(cfg, tc, history, 0)


def delete(cfg, idx):
    """删除 history[idx]：删其槽位文件并维护激活图有效（删的是当前则用新 history[0] 复制覆盖激活图；
    删空则连激活图一起清，回到「未标定」与现状一致）。返回新 history。"""
    tc, history, active = _load(cfg, migrate=False)
    if not (0 <= idx < len(history)):
        return _save(cfg, tc, history, active)
    removed = history.pop(idx)
    _remove(removed.get("slot"))
    if not history:                        # 全空：激活图也清掉
        _remove(ACTIVE_REL)
        return _save(cfg, tc, history, 0)
    if idx == active:                      # 删的是当前：用新的 history[0] 当当前
        _copy(history[0].get("slot"), ACTIVE_REL)
        active = 0
    elif idx < active:
        active -= 1
    return _save(cfg, tc, history, active)
