# -*- coding: utf-8 -*-
"""
标定「尺寸组」（胶囊组）：记录「某一套标定图是在哪个游戏窗口尺寸下标定的」。纯逻辑、零 GUI 依赖、可单测。

为什么要有这个模块（用户痛点）：
  标定图只在标定时那个窗口尺寸下认得出，改分辨率就认不出；用户希望「软件记住尺寸，
  我以后随意修改基准也能随时回到标定时的尺寸」，并且一眼看出「哪些模板是当前尺寸标的、哪些是旧的」。

三条铁律（改前务必读懂，否则极易破坏识别/回归面）：
1. 【全局共用一套模板，只存指针】模板文件永远只有一份（templates/tm_<key>.png）。
   「这张模板属于哪一组」只记在 config 的指针里（tasks.<task>.templates_calib），
   **绝不按尺寸复制模板文件**（如 tm_escort_join_1521x1198.png）——那会让模板数爆炸、
   并破坏 leader_history.py「激活图路径永远写死、切换靠字节覆盖」的契约。
2. 【targets.base_size 是派生的镜像字段，不是真源】4 个任务在跑的时候读它估窗口缩放
   （escort/secret_realm/taohaiqu/treasure_map 的 _calib_size()），故字段和读取点一律不动。
   但它只允许由本模块的 set_active()/get_or_create() 写入（唯一写入口），保证镜像不漂。
   真源是 config 顶层的 calib_profiles.items / active。
3. 【删组绝不删模板文件】delete() 只删组记录，模板图与模板路径串保持原样；
   指向被删组的模板指针会被清掉（降级为「未记录」），于是显示成灰色胶囊 + 可一键重标。

config 结构（顶层，和 targets 平级；放 targets 里会把「运行语义」和「溯源/UI 数据」混在一起）:
    "calib_profiles": {
        "items": [{"id": 1, "size": [1521, 1198], "label": "①", "at": "07-14 21:30"}],
        "active": 1
    },
    "tasks": {
        "escort": {
            "calib": {"profile": 1},                  # 该任务 regions 的标定组
            "templates_calib": {"escort_join": 1}     # 该任务各模板分别属于哪一组
        }
    }

兼容/兜底：老 config 没有 calib_profiles（items 为空）时 active_size() 退回读
targets.base_size（现状行为），故任何调用点在「用户还没重新标定」时都不会更差。
"""

import time

from . import config as cfg_mod

# 最多 3 个尺寸组（用户拍板：胶囊只留三个尺寸位置，多了没有现实意义）
MAX_PROFILES = 3

PROFILES_KEY = "calib_profiles"     # config 顶层
ITEMS_KEY = "items"
ACTIVE_KEY = "active"

TASK_CALIB_KEY = "calib"            # tasks.<name>.calib = {"profile": id}
TASK_TPL_KEY = "templates_calib"    # tasks.<name>.templates_calib = {key: id}

# 同尺寸容差：窗口缩放会被外壳吸附到档位，实测误差 ≤4px 视为同一组（与 window.resize_to 的判定一致）
SIZE_TOL = 4


def _now_label():
    return time.strftime("%m-%d %H:%M")


def _size_ok(size):
    return bool(size) and len(size) >= 2 and int(size[0]) > 0 and int(size[1]) > 0


def _norm_size(size):
    return [int(size[0]), int(size[1])]


# ----------------------------------------------------------------------
# 组列表与激活组
# ----------------------------------------------------------------------
def items(cfg):
    """全部尺寸组，按 id 升序：[{"id","size","label","at"}, ...]。无/非法时返回 []。"""
    raw = ((cfg or {}).get(PROFILES_KEY) or {}).get(ITEMS_KEY) or []
    out = []
    for it in raw:
        if not isinstance(it, dict) or not _size_ok(it.get("size")):
            continue
        try:
            pid = int(it.get("id"))
        except (TypeError, ValueError):
            continue
        out.append({
            "id": pid,
            "size": _norm_size(it["size"]),
            "label": str(it.get("label") or label_for(pid)),
            "at": it.get("at") or "",
        })
    out.sort(key=lambda d: d["id"])
    return out


def get(cfg, pid):
    """按 id 取组，没有返回 None。"""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    for it in items(cfg):
        if it["id"] == pid:
            return it
    return None


def active_id(cfg):
    """激活组的 id。没有组返回 None；active 非法时兜底到第一组（绝不返回悬空 id）。"""
    its = items(cfg)
    if not its:
        return None
    aid = ((cfg or {}).get(PROFILES_KEY) or {}).get(ACTIVE_KEY)
    try:
        aid = int(aid)
    except (TypeError, ValueError):
        return its[0]["id"]
    return aid if any(it["id"] == aid for it in its) else its[0]["id"]


def active(cfg):
    """激活组 dict，没有组返回 None。"""
    return get(cfg, active_id(cfg))


def active_size(cfg):
    """激活组的尺寸 [w,h]；没有组时退回 targets.base_size（老配置的镜像兜底），都没有返回 None。

    这是「当前用哪套标定图」的唯一读入口（4 个任务的 _calib_size() 与镜像同步都走它）。
    """
    prof = active(cfg)
    if prof is not None:
        return list(prof["size"])
    base = ((cfg or {}).get("targets") or {}).get("base_size")
    return _norm_size(base) if _size_ok(base) else None


def find_by_size(cfg, size):
    """按尺寸（±SIZE_TOL）找已有的组，返回其 id；没有返回 None。"""
    if not _size_ok(size):
        return None
    w, h = _norm_size(size)
    for it in items(cfg):
        iw, ih = it["size"]
        if abs(iw - w) <= SIZE_TOL and abs(ih - h) <= SIZE_TOL:
            return it["id"]
    return None


# ----------------------------------------------------------------------
# 显示用（照 theme.PROFILE_COLORS / PROFILE_NUMERALS；编号才是主键，颜色只是辅助）
# ----------------------------------------------------------------------
def color_index(pid):
    """组号 -> PROFILE_COLORS 下标（按色板长度取模循环）。非法组号返回 0。

    延迟 import gui.theme：本模块刻意保持「零 GUI 依赖」（见模块头），故只在真正要画颜色时才取；
    取不到（还没建 Tk / 打包裁剪）退回 3 色板长度，不影响任何逻辑。
    """
    try:
        from ..gui import theme as _t
        n = len(_t.PROFILE_COLORS)
    except Exception:
        n = 3
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return 0
    return (max(1, pid) - 1) % max(1, n)


def label_for(pid):
    """组号 -> 圈码「①」。超出圈码表则退回阿拉伯数字（最多 3 组，留作兜底）。"""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return "?"
    numerals = "①②③④⑤⑥⑦⑧⑨"
    if 1 <= pid <= len(numerals):
        return numerals[pid - 1]
    return str(pid)


def size_text(prof):
    """组 -> 「1521×1198」（无组返回「未设置」）。"""
    if not prof or not _size_ok(prof.get("size")):
        return "未设置"
    return f"{prof['size'][0]}×{prof['size'][1]}"


# ----------------------------------------------------------------------
# 写入口（唯二允许写 targets.base_size 的地方，见模块头铁律 2）
# ----------------------------------------------------------------------
def _sync_mirror(cfg, size):
    """把 targets.base_size 镜像成激活组尺寸（派生字段，见铁律 2）。"""
    cfg.setdefault("targets", {})["base_size"] = _norm_size(size)


def set_active(cfg, pid):
    """切换激活组：写 active + 同步 targets.base_size 镜像。成功返回 True。
    组不存在返回 False（不动任何字段，避免把镜像写坏）。"""
    prof = get(cfg, pid)
    if prof is None:
        return False
    blk = cfg.setdefault(PROFILES_KEY, {})
    blk[ITEMS_KEY] = [dict(it) for it in items(cfg)]
    blk[ACTIVE_KEY] = prof["id"]
    _sync_mirror(cfg, prof["size"])
    return True


def get_or_create(cfg, size):
    """按当前窗口尺寸取组：同尺寸（±SIZE_TOL）复用，否则新建一组；一律设为激活组 + 同步镜像。
    返回组号 id。组已满 MAX_PROFILES 且尺寸是新的 → 抛 ValueError（调用方提示用户先删一组）。"""
    if not _size_ok(size):
        raise ValueError("窗口尺寸无效，无法记录标定组")
    size = _norm_size(size)
    pid = find_by_size(cfg, size)
    if pid is not None:
        set_active(cfg, pid)
        return pid
    its = items(cfg)
    if len(its) >= MAX_PROFILES:
        raise ValueError(f"已有 {MAX_PROFILES} 个尺寸组（{MAX_PROFILES} 个上限）："
                         f"请先在「窗口尺寸归一化」里删掉一个旧组，再标定 {size[0]}×{size[1]}")
    blk = cfg.setdefault(PROFILES_KEY, {})
    nid = (max([it["id"] for it in its]) + 1) if its else 1
    its.append({"id": nid, "size": size, "label": label_for(nid), "at": _now_label()})
    blk[ITEMS_KEY] = its
    blk[ACTIVE_KEY] = nid
    _sync_mirror(cfg, size)
    return nid


def delete(cfg, pid):
    """删掉一个组记录（绝不删模板文件）。返回删除后的激活组号（没了返回 None）。

    级联清理：指向该组的「任务标定组指针」与「模板组指针」一并清掉（降级为「未记录」，
    于是界面上显示成灰胶囊 + 可被「重标旧尺寸的模板」筛出来）；激活组被删则改激活剩余第一组、
    并同步镜像；删空则清空镜像（base_size=None，与从未标定一致）。
    """
    prof = get(cfg, pid)
    if prof is None:
        return active_id(cfg)
    its = [it for it in items(cfg) if it["id"] != prof["id"]]
    blk = cfg.setdefault(PROFILES_KEY, {})
    blk[ITEMS_KEY] = its
    # 清掉指向该组的指针（任务级 + 模板级）
    for tc in (cfg.get("tasks") or {}).values():
        if not isinstance(tc, dict):
            continue
        cal = tc.get(TASK_CALIB_KEY)
        if isinstance(cal, dict) and _as_id(cal.get("profile")) == prof["id"]:
            cal["profile"] = None
        tpls = tc.get(TASK_TPL_KEY)
        if isinstance(tpls, dict):
            for k, v in list(tpls.items()):
                if _as_id(v) == prof["id"]:
                    tpls[k] = None
    if not its:
        blk[ACTIVE_KEY] = None
        cfg.setdefault("targets", {})["base_size"] = None
        return None
    if _as_id(blk.get(ACTIVE_KEY)) == prof["id"] or _as_id(blk.get(ACTIVE_KEY)) is None:
        set_active(cfg, its[0]["id"])
    else:
        # 删的不是激活组：镜像保持指向仍在的激活组（重写一遍防漂）
        set_active(cfg, blk.get(ACTIVE_KEY))
    return active_id(cfg)


def _as_id(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# ----------------------------------------------------------------------
# 任务/模板的组指针
# ----------------------------------------------------------------------
def set_task_profile(cfg, task, pid):
    """记「该任务 regions 是在哪一组下标定的」：tasks.<task>.calib.profile。"""
    tc = cfg.setdefault("tasks", {}).setdefault(task, {})
    tc.setdefault(TASK_CALIB_KEY, {})["profile"] = _as_id(pid)
    return pid


def task_profile(cfg, task):
    """该任务的标定组号；未记录返回 None。"""
    tc = (cfg.get("tasks") or {}).get(task) or {}
    return _as_id((tc.get(TASK_CALIB_KEY) or {}).get("profile"))


def set_template_profile(cfg, task, key, pid):
    """记「该模板属于哪一组」：tasks.<task>.templates_calib[key]。"""
    tc = cfg.setdefault("tasks", {}).setdefault(task, {})
    tc.setdefault(TASK_TPL_KEY, {})[key] = _as_id(pid)
    return pid


def template_profiles(cfg, task):
    """该任务全部模板的组指针 {key: id|None}（只含显式记录过的）。"""
    tc = (cfg.get("tasks") or {}).get(task) or {}
    raw = tc.get(TASK_TPL_KEY) or {}
    if not isinstance(raw, dict):
        return {}
    return {k: _as_id(v) for k, v in raw.items()}


def template_profile(cfg, task, key):
    """单个模板的组号；未记录返回 None。"""
    return template_profiles(cfg, task).get(key)


def stale_templates(cfg, task):
    """「组指针 ≠ 激活组」的模板 key 列表（含未记录的），供「重标旧尺寸的模板」一键筛出。

    只统计【当前已标定过的模板】（tc["templates"][key] 非空）——没标过的项本来就要标，
    列进来只会让「重标」按钮长出一堆空项。
    """
    tc = (cfg.get("tasks") or {}).get(task) or {}
    saved = tc.get("templates") or {}
    aid = active_id(cfg)
    ptr = template_profiles(cfg, task)
    out = []
    for key, path in saved.items():
        if not path:
            continue
        if ptr.get(key) != aid:
            out.append(key)
    return out
