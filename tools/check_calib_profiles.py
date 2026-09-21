# -*- coding: utf-8 -*-
"""标定尺寸组（calib_profiles）纯逻辑自测：加减组 / 镜像同步 / 满 3 组 / 指针级联。
用临时 config 字典（不碰真实 config.json）。跑法（项目根目录）：
    python -m tools.check_calib_profiles
"""
import copy
import sys

from mhxy.core import calib_profiles as cp
from mhxy.core.config import DEFAULT_CONFIG


def fresh():
    return copy.deepcopy(DEFAULT_CONFIG)


def check(name, cond, extra=""):
    print(("  OK  " if cond else "  FAIL") + f"  {name} {extra}")
    if not cond:
        raise AssertionError(name)


def main():
    cfg = fresh()
    check("初始 items 为空", cp.items(cfg) == [])
    check("初始 active_id None", cp.active_id(cfg) is None)
    check("初始 active_size=None（base_size 也是 None）", cp.active_size(cfg) is None)

    # 1) 新建两组
    p1 = cp.get_or_create(cfg, [1521, 1198])
    check("get_or_create 建第 1 组 = 1", p1 == 1, p1)
    check("镜像同步 base_size", cfg["targets"]["base_size"] == [1521, 1198], cfg["targets"]["base_size"])
    check("label 是圈码①", cp.label_for(p1) == "①")
    p2 = cp.get_or_create(cfg, [1900, 1500])
    check("第 2 组 = 2", p2 == 2, p2)
    check("active 切到 2", cp.active_id(cfg) == 2)
    check("镜像跟到第 2 组", cfg["targets"]["base_size"] == [1900, 1500])

    # 2) 同尺寸复用（±4 容差）
    again = cp.get_or_create(cfg, [1523, 1196])
    check("±4px 内复用第 1 组", again == 1, again)
    check("复用后自动激活", cp.active_id(cfg) == 1)
    check("镜像回到第 1 组", cfg["targets"]["base_size"] == [1521, 1198])
    check("组数没变（仍 2 组）", len(cp.items(cfg)) == 2)

    # 3) 切组
    check("set_active(2) 成功", cp.set_active(cfg, 2) is True)
    check("切组后镜像=第2组尺寸", cfg["targets"]["base_size"] == [1900, 1500])
    check("set_active(99) 失败且不动镜像", cp.set_active(cfg, 99) is False
          and cfg["targets"]["base_size"] == [1900, 1500])

    # 4) 满 3 组后新建应报错
    p3 = cp.get_or_create(cfg, [1280, 960])
    check("第 3 组 = 3", p3 == 3, p3)
    try:
        cp.get_or_create(cfg, [1024, 768])
        check("第 4 组应报错", False)
    except ValueError as e:
        check("第 4 组报错（含提示）", "3 个" in str(e), str(e))
    check("报错后组数仍 3", len(cp.items(cfg)) == 3)

    # 5) 任务/模板组指针
    cp.set_task_profile(cfg, "escort", p1)
    cp.set_template_profile(cfg, "escort", "escort_join", p1)
    cp.set_template_profile(cfg, "escort", "escort_silver", p3)
    cp.set_template_profile(cfg, "escort", "escort_ongoing", None)
    cfg["tasks"]["escort"]["templates"] = {
        "escort_join": "templates/tm_escort_join.png",
        "escort_silver": "templates/tm_escort_silver.png",
        "escort_ongoing": "templates/tm_escort_ongoing.png",
        "never_calibrated": None,
    }
    check("task_profile 读回", cp.task_profile(cfg, "escort") == p1)
    check("template_profiles 三个键", cp.template_profiles(cfg, "escort") ==
          {"escort_join": 1, "escort_silver": 3, "escort_ongoing": None})

    # 6) 删组：级联清指针 + 激活兜底 + 镜像同步（绝不删模板文件）
    cp.set_active(cfg, 1)
    left = cp.delete(cfg, 1)
    check("删激活组后激活=剩余第一组(2)", left == 2, left)
    check("镜像跟到第 2 组", cfg["targets"]["base_size"] == [1900, 1500])
    check("模板路径串没被动（不删文件/不改路径）",
          cfg["tasks"]["escort"]["templates"]["escort_join"] == "templates/tm_escort_join.png")
    check("指向被删组的指针已清空", cp.template_profiles(cfg, "escort")["escort_join"] is None)
    check("任务组指针也清空", cp.task_profile(cfg, "escort") is None)
    check("其它组的指针不受影响", cp.template_profiles(cfg, "escort")["escort_silver"] == 3)

    # 7) 删空 → 回到「从未标定」
    cp.delete(cfg, 2)
    cp.delete(cfg, 3)
    check("删空后 items 空", cp.items(cfg) == [])
    check("删空后 active_id None", cp.active_id(cfg) is None)
    check("删空后 base_size 置 None", cfg["targets"]["base_size"] is None)
    check("删空后 active_size None", cp.active_size(cfg) is None)
    check("delete 不存在的组不炸", cp.delete(cfg, 42) is None)

    # 8) 老配置兜底：没有 calib_profiles 时 active_size 退回 targets.base_size
    legacy = fresh()
    legacy["targets"]["base_size"] = [1521, 1198]
    check("老配置 active_size 退回 base_size 镜像", cp.active_size(legacy) == [1521, 1198])

    # 9) 脏数据兜底
    dirty = fresh()
    dirty["calib_profiles"] = {"items": [{"id": "x"}, {"id": 2, "size": [0, 0]},
                                         {"id": 3, "size": [1600, 1200], "label": "③"}],
                               "active": 99}
    check("脏 items 被过滤，只剩合法项", [it["id"] for it in cp.items(dirty)] == [3])
    check("active 越界兜底到第一组", cp.active_id(dirty) == 3)
    check("尺寸文本", cp.size_text(cp.get(dirty, 3)) == "1600×1200")

    print("\n全部通过")


if __name__ == "__main__":
    sys.exit(main())
