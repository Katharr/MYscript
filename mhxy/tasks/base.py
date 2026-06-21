# -*- coding: utf-8 -*-
"""
任务基类与注册表。

约定：每个任务继承 Task，实现 run(ctx)，并在 run 的循环里频繁检查 ctx.should_stop()。
任务通过 ctx.log() 输出日志、ctx.window/ctx.mouse 操作游戏，绝不直接引用 GUI。
"""

_REGISTRY = {}


def register(cls):
    """类装饰器：把任务登记进注册表。"""
    _REGISTRY[cls.name] = cls
    return cls


def get_task(name):
    return _REGISTRY.get(name)


def all_tasks():
    """按注册顺序返回任务类列表。"""
    return list(_REGISTRY.values())


class Task:
    name = "base"          # 唯一标识（英文，作为 config.tasks 的键）
    title = "基础任务"      # 界面显示名
    description = ""        # 一句话说明

    def run(self, ctx):
        """任务主体。会在后台线程里执行；需自行在循环中检查 ctx.should_stop()。"""
        raise NotImplementedError

    def preflight(self, ctx):
        """启动前自检。返回 (ok: bool, problems: list[str])。默认通过。"""
        return True, []
