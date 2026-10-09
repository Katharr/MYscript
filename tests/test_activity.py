# -*- coding: utf-8 -*-
import copy
import unittest
from unittest import mock

import numpy as np

from mhxy.core import activity, calib_profiles, config
from mhxy.tasks.escort import EscortTask
from mhxy.tasks.secret_realm import SecretRealmTask
from mhxy.tasks.treasure_map import TreasureMapTask
from mhxy.tasks.taohaiqu import TaohaiquTask


class ActivityNavigationTests(unittest.TestCase):
    def setUp(self):
        self.cfg = copy.deepcopy(config.DEFAULT_CONFIG)
        self.ctx = mock.Mock()
        self.ctx.cfg = self.cfg
        self.ctx.task_cfg.side_effect = lambda name: config.task_config(self.cfg, name)
        self.ctx.should_stop.return_value = False
        self.ctx.window.rect.return_value = (100, 200, 800, 600)
        self.sleep = mock.Mock()

    def _calibrate(self):
        self.cfg["tasks"]["activity"]["templates"]["daily_activity"] = "templates/test_daily.png"

    def test_optional_uncalibrated_button_does_not_capture_or_click(self):
        with mock.patch.object(activity.win_mod, "grab_scene") as grab:
            self.assertFalse(activity.select_daily(self.ctx, self.sleep))
        grab.assert_not_called()
        self.ctx.mouse.click.assert_not_called()

    def test_missing_template_does_not_capture_or_click(self):
        self._calibrate()
        with mock.patch.object(activity.vision, "load_template", return_value=None), \
             mock.patch.object(activity.win_mod, "grab_scene") as grab:
            self.assertFalse(activity.select_daily(self.ctx, self.sleep))
        grab.assert_not_called()
        self.ctx.mouse.click.assert_not_called()

    def test_absent_button_skips_click_and_wait(self):
        self._calibrate()
        scene = mock.Mock()
        scene.match.return_value = None
        with mock.patch.object(activity.vision, "load_template", return_value=object()), \
             mock.patch.object(activity.win_mod, "grab_scene", return_value=scene):
            self.assertFalse(activity.select_daily(self.ctx, self.sleep))
        self.ctx.mouse.click.assert_not_called()
        self.sleep.assert_not_called()

    def test_template_uses_own_profile_and_clicks_screen_coordinates(self):
        self._calibrate()
        pid = calib_profiles.get_or_create(self.cfg, [1000, 750])
        calib_profiles.set_template_profile(self.cfg, "activity", "daily_activity", pid)
        calib_profiles.get_or_create(self.cfg, [1200, 900])
        # 当前窗口是模板标定尺寸的 0.8 倍；局部坐标必须缩回并叠加窗口偏移。
        scene = activity.win_mod.ScaledScene((100, 200, 800, 600), [1000, 750])
        template = np.zeros((20, 40, 3), dtype=np.uint8)
        with mock.patch.object(activity.vision, "load_template", return_value=template), \
             mock.patch.object(activity.win_mod, "grab_scene", return_value=scene) as grab, \
             mock.patch.object(activity.win_mod.ScaledScene, "match", return_value=(125, 250, 0.96)):
            self.assertTrue(activity.select_daily(self.ctx, self.sleep))
        grab.assert_called_once_with((100, 200, 800, 600), [1000, 750])
        self.ctx.mouse.click.assert_called_once_with(200, 400)
        self.sleep.assert_called_once_with(0.35)

    def test_stop_before_capture_or_after_match_never_clicks(self):
        self._calibrate()
        for stops in ([True], [False, True]):
            with self.subTest(stops=stops):
                self.ctx.should_stop.side_effect = stops
                scene = mock.Mock()
                scene.match.return_value = (10, 20, 0.99)
                with mock.patch.object(activity.vision, "load_template", return_value=object()), \
                     mock.patch.object(activity.win_mod, "grab_scene", return_value=scene):
                    self.assertFalse(activity.select_daily(self.ctx, self.sleep))
                self.ctx.mouse.click.assert_not_called()

    def test_all_activity_entries_select_daily_after_opening_wait(self):
        for cls in (EscortTask, SecretRealmTask, TreasureMapTask, TaohaiquTask):
            with self.subTest(task=cls.name):
                task = cls()
                events = []
                self.ctx.send_hotkey.side_effect = lambda key: events.append(key) or True
                task._focus = mock.Mock()
                task._jitter = lambda delay, ctx: delay
                task._interruptible_sleep = lambda ctx, delay: events.append("wait")
                with mock.patch.object(activity, "select_daily", side_effect=lambda *args: events.append("daily")), \
                     mock.patch("mhxy.tasks.taohaiqu.scan.scroll_search", return_value=mock.Mock(found=False, stopped=True)):
                    if cls is TaohaiquTask:
                        task._open_and_join(self.ctx, {}, {}, 0.85)
                    else:
                        task._do_open_activity(self.ctx, {}, {}, {}, 0.85)
                self.assertEqual(events[:3], ["open_activity", "wait", "daily"])


if __name__ == "__main__":
    unittest.main()
