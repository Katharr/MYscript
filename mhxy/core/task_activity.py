"""任务状态文案，只接受执行路径的显式上报，不解析自然语言日志。"""


def activity_text(name):
    known = {
        "treasure_map": "挖宝中…", "escort": "运镖中…", "secret_realm": "秘境降妖中…",
        "sniper": "秒装备中…", "freeclick": "自由点击中…", "organize_bag": "整理背包中…",
        "dungeon": "组队中…", "disband": "解散队伍中…", "daily": "日常执行中…",
    }
    if name in known:
        return known[name]
    from ..tasks import get_task
    task = get_task(name)
    return (str(task.title) + "中…") if task else "执行中…"


def report(ctx, text=None, tone="success"):
    session = getattr(ctx, "input_session", None)
    from .input_session import InputSession
    if isinstance(session, InputSession):
        session.report_activity(ctx.window, text, tone)
