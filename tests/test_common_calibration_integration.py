"""共享标定与尺寸管理、活动摘要的接线回归；不截图、不发送输入。"""

import copy
from types import SimpleNamespace
import unittest
from unittest import mock

from mhxy.core import calibration, calib_profiles, config
from mhxy.qtui.dungeon_page import DungeonPage
from mhxy.qtui.treasure_map_page import _ActivityPage


class InlineThread:
    def __init__(self, *, target, **kwargs):
        self.target = target

    def start(self):
        self.target()


class CommonCalibrationIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.cfg = copy.deepcopy(config.DEFAULT_CONFIG)
        self.pid = calib_profiles.get_or_create(self.cfg, [1200, 900])
        calibration.set_item(self.cfg, "common", "regions", "activity_list",
                             [10, 20, 300, 400], self.pid)
        calibration.set_item(self.cfg, "common", "templates", "activity_join",
                             "templates/common_join.png", self.pid)

    def test_deleting_size_group_clears_common_and_local_region_pointers(self):
        calibration.set_item(self.cfg, "escort", "regions", "scene", [1, 2, 50, 60], self.pid)
        calib_profiles.delete(self.cfg, self.pid)
        for task, key in (("common", "activity_list"), ("escort", "scene")):
            with self.subTest(task=task):
                self.assertIsNone(self.cfg["tasks"][task]["regions_calib"][key])
                self.assertIsNone(calibration.resolve_item(self.cfg, task, "regions", key)["profile"])
        self.assertEqual(self.cfg["tasks"]["common"]["templates"]["activity_join"],
                         "templates/common_join.png")

    def test_dungeon_readiness_counts_inherited_assets(self):
        spec = {"regions": [("activity_list", "列表", "")],
                "templates": [("thq_join", "参加", "")]}
        page = SimpleNamespace(cfg=self.cfg, _selected="taohaiqu",
                               selected_task=SimpleNamespace(CALIBRATION=spec),
                               _template_ready=lambda value: value == "templates/common_join.png")
        self.assertEqual(DungeonPage._required_calibration(page), (1, 1, 1, 1))
        self.assertIsNone(self.cfg["tasks"]["taohaiqu"]["templates"]["thq_join"])

    def test_activity_summary_reads_same_effective_assets_and_sources(self):
        class Task:
            name = "escort"
            CALIBRATION = {"regions": [("activity_list", "列表", "")],
                           "templates": [("escort_join", "参加", "")]}

            def preflight(self, ctx):
                return True, []

        page = SimpleNamespace(
            cfg=self.cfg, TASK_NAME="escort", TASK_CLASS=Task,
            _calibration_generation=0, calibration_ready=mock.Mock(),
            calibration_status=mock.Mock(),
            _required_assets=lambda tc: ({"activity_list"}, {"escort_join"}))
        before = copy.deepcopy(self.cfg)
        with mock.patch("mhxy.qtui.treasure_map_page.threading.Thread", InlineThread), \
                mock.patch("mhxy.qtui.treasure_map_page.TaskContext"), \
                mock.patch("mhxy.qtui.treasure_map_page.vision.load_template", return_value=object()):
            _ActivityPage._request_calibration(page)
        generation, result, error = page.calibration_ready.emit.call_args.args
        self.assertEqual((generation, result["missing"], error), (1, 0, ""))
        self.assertEqual(result["text"].count("已标定 · 通用"), 2)
        self.assertEqual(self.cfg, before)


if __name__ == "__main__":
    unittest.main()
