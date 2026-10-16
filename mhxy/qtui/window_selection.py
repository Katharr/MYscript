"""主窗口唯一的选窗状态；枚举顺序只用于兼容旧配置，运行目标固定为句柄。"""

from ..core import config as cfg_mod


class WindowSelection:
    def __init__(self):
        self.selected = set()
        self.captain = None
        self.order = []
        self.initialized = False

    def reconcile(self, states, cfg, *, locked=False, observed=False):
        order = [s.hwnd for s in states]
        previous = self.captain
        if not self.initialized and (order or observed):
            targets = cfg.get("targets") or {}
            if "selected_hwnds" in targets:
                self.selected = set(targets["selected_hwnds"]) & set(order)
            else:
                from .role_window_page import target_indices
                self.selected = {order[i] for i in target_indices(targets, len(order), apply_limit=True)}
            selected = [h for h in order if h in self.selected]
            index = cfg_mod.task_config(cfg, "teaming").get("captain_index", 0)
            self.captain = targets.get("captain_hwnd")
            if self.captain not in order:
                self.captain = selected[index] if 0 <= index < len(selected) else (order[0] if order else None)
            self.initialized = True
        self.selected.intersection_update(order)
        # 运行中不能转移队长；由主窗口先停止，再在完全退出后重新指定。
        if not locked and self.captain not in order:
            self.captain = order[0] if order else None
        self.order = order
        return previous is not None and previous != self.captain

    def write(self, cfg):
        if not self.initialized:
            return []
        indices = [i for i, h in enumerate(self.order) if h in self.selected]
        handles = [self.order[i] for i in indices]
        cfg.setdefault("targets", {}).update(
            selected_hwnds=handles, captain_hwnd=self.captain,
            multi=len(handles) > 1, single_index=indices[0] if indices else -1,
            multi_indices=indices, max_windows=0,
        )
        # 旧任务仍消费各自的 captain_index；统一从全局队长派生，缺席用 -1 明确拒绝。
        captain_index = handles.index(self.captain) if self.captain in handles else -1
        from ..tasks import all_tasks
        tasks = all_tasks()
        team_names = {t.name for t in tasks if t.name == "dungeon" or getattr(t, "is_dungeon", False)} | {"teaming"}
        namespaces = {t.name for t in tasks} | {"teaming"}
        for name in namespaces:
            tc = cfg_mod.task_config(cfg, name)
            if name in team_names or "captain_index" in tc:
                tc["captain_index"] = captain_index
                cfg_mod.set_task_config(cfg, name, tc)
        return handles

    def snapshot(self, cfg):
        handles = self.write(cfg)
        if not handles:
            raise ValueError("请在顶部选择目标窗口")
        cfg["targets"]["window_handles"] = handles
        return cfg
