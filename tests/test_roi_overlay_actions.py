# -*- coding: utf-8 -*-
"""冻结截图动作框选：假 Tk/游戏窗口，离线验证，无真实窗口或资产写入。"""
from types import SimpleNamespace
import unittest
from unittest import mock

import numpy as np
from mhxy.gui import roi_overlay as ro
from mhxy.gui import calibrate_dialog as cd


class FakeWidget:
    widgets = []

    def __init__(self, parent=None, **kwargs):
        self.parent, self.kwargs = parent, kwargs
        self.bindings = {}
        self.destroyed = False
        self.grab_released = False
        self.placement = None
        self.widgets.append(self)

    def __getattr__(self, name):
        return lambda *a, **kw: None

    def bind(self, key, callback):
        self.bindings[key] = callback

    def grab_release(self):
        self.grab_released = True

    def destroy(self):
        self.destroyed = True

    def place(self, **kwargs):
        self.placement = kwargs

    def winfo_reqwidth(self):
        return 90 if 'text' in self.kwargs else 280

    def winfo_reqheight(self):
        return 32

    def create_rectangle(self, *args, **kwargs):
        return object()

    create_text = create_rectangle
    create_image = create_rectangle


class OverlayActionTests(unittest.TestCase):
    def setUp(self):
        FakeWidget.widgets = []
        self.image = np.zeros((160, 320, 3), dtype=np.uint8)
        self.image[:, :, :] = [13, 47, 91]
        self.mon = dict(left=-320, top=80, width=320, height=160)
        for name in ('Toplevel', 'Canvas', 'Frame', 'Button'):
            p = mock.patch.object(ro.tk, name, FakeWidget)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(ro.ImageTk, 'PhotoImage', return_value=object())
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(ro.vision, 'capture_monitor_rgb',
                              return_value=(self.mon, (320, 160), self.image.tobytes()))
        self.capture = p.start()
        self.addCleanup(p.stop)

    def draw(self, start, end):
        canvas = next(w for w in FakeWidget.widgets if '<ButtonPress-1>' in w.bindings)
        canvas.bindings['<ButtonPress-1>'](SimpleNamespace(x=start[0], y=start[1]))
        canvas.bindings['<ButtonRelease-1>'](SimpleNamespace(x=end[0], y=end[1]))

    def click(self, label):
        button = next(w for w in reversed(FakeWidget.widgets)
                      if w.kwargs.get('text') == label)
        button.kwargs['command']()

    def run_overlay(self, script, **kwargs):
        master = SimpleNamespace(wait_window=script)
        return ro.select_roi_on_screen(master, **kwargs)

    def test_action_waits_for_click_clamps_reverse_drag_and_crops_original(self):
        def script(top):
            self.draw((330, 170), (260, 120))
            self.assertFalse(top.destroyed)
            toolbar = next(w for w in FakeWidget.widgets if w.placement is not None)
            x, y = toolbar.placement['x'], toolbar.placement['y']
            self.assertGreaterEqual(x, 0)
            self.assertGreaterEqual(y, 0)
            self.assertLessEqual(x + toolbar.winfo_reqwidth(), 320)
            self.assertLessEqual(y + toolbar.winfo_reqheight(), 160)
            self.click('出售')
            self.assertTrue(top.destroyed)
        rect, crop, action = self.run_overlay(script, with_crop=True,
                                             action_choices=[('sell', '出售')])
        self.assertEqual(rect, [-60, 200, 60, 40])
        self.assertEqual(action, 'sell')
        np.testing.assert_array_equal(crop, self.image[120:160, 260:320, ::-1])
        self.capture.assert_called_once()

    def test_retry_uses_same_frozen_image_and_new_rectangle(self):
        def script(top):
            self.draw((10, 10), (30, 30))
            self.click('重新框选')
            self.draw((80, 90), (100, 110))
            self.click('使用')
        rect, crop, action = self.run_overlay(script, with_crop=True,
                                             action_choices=[('use', '使用')])
        self.assertEqual(rect, [-240, 170, 20, 20])
        self.assertEqual(crop.shape, (20, 20, 3))
        self.assertEqual(action, 'use')
        self.capture.assert_called_once()

    def test_cancel_and_escape_clear_existing_selection(self):
        for escape in (True, False):
            with self.subTest(escape=escape):
                FakeWidget.widgets = []
                def script(top):
                    self.draw((10, 10), (30, 30))
                    if escape:
                        top.bindings['<Escape>']()
                    else:
                        self.click('取消')
                self.assertEqual(self.run_overlay(script, with_crop=True,
                                                 action_choices=[('use', '使用')]),
                                 (None, None, None))

    def test_tiny_selection_allows_redraw(self):
        def script(top):
            self.draw((10, 10), (11, 11))
            self.assertFalse(top.destroyed)
            self.draw((-10, -10), (20, 20))
            self.click('使用')
        self.assertEqual(self.run_overlay(script, action_choices=[('use', '使用')]),
                         ([-320, 80, 20, 20], 'use'))

    def test_image_widget_failure_releases_overlay(self):
        with mock.patch.object(ro.ImageTk, 'PhotoImage', side_effect=RuntimeError('image')):
            with self.assertRaises(RuntimeError):
                self.run_overlay(lambda top: None, action_choices=[('use', '使用')])
        top = FakeWidget.widgets[0]
        self.assertTrue(top.destroyed)
        self.assertTrue(top.grab_released)

    def test_wait_and_toolbar_exceptions_release_overlay_and_input(self):
        for failure in ('wait', 'toolbar'):
            with self.subTest(failure=failure):
                FakeWidget.widgets = []
                def script(top):
                    if failure == 'toolbar':
                        with mock.patch.object(ro.tk, 'Frame', side_effect=RuntimeError('toolbar')):
                            self.draw((10, 10), (30, 30))
                    else:
                        raise RuntimeError('wait')
                with self.assertRaises(RuntimeError):
                    self.run_overlay(script, with_crop=True, action_choices=[('use', '使用')])
                top = FakeWidget.widgets[0]
                self.assertTrue(top.destroyed)
                self.assertTrue(top.grab_released)

    def test_default_interfaces_and_cancel_are_unchanged(self):
        for with_crop in (True, False):
            with self.subTest(with_crop=with_crop):
                FakeWidget.widgets = []
                result = self.run_overlay(lambda top: self.draw((10, 10), (30, 30)),
                                          with_crop=with_crop)
                self.assertEqual(result[0] if with_crop else result, [-310, 90, 20, 20])
                if with_crop:
                    self.assertEqual(len(result), 2)
                FakeWidget.widgets = []
                result = self.run_overlay(lambda top: top.bindings['<Escape>'](),
                                          with_crop=with_crop)
                self.assertEqual(result, (None, None) if with_crop else None)


class GrabActionTests(unittest.TestCase):
    def setUp(self):
        self.app, self.manager = mock.Mock(), mock.Mock()
        self.cfg = {'window_title': 'game', 'window_offset': [0, 0]}
        self.window = SimpleNamespace(rect=lambda: [100, 200, 800, 600])
        for obj, name, value in ((cd.win_mod, 'locate_all', [self.window]),
                                  (cd.win_mod, 'window_at_point', self.window),
                                  (cd, 'resolve_profile', 2),
                                  (cd, 'select_roi_on_screen', ([110, 220, 30, 40], 'crop', 'use'))):
            p = mock.patch.object(obj, name, return_value=value)
            setattr(self, name, p.start())
            self.addCleanup(p.stop)
        for obj, name in ((cd.time, 'sleep'), (cd.cfg_mod, 'save_config')):
            p = mock.patch.object(obj, name)
            setattr(self, name, p.start())
            self.addCleanup(p.stop)
        self.choices = [('use', '使用')]

    def grab(self, **kwargs):
        return cd.grab_roi_on_app(self.app, self.cfg, '框选',
                                  alpha_windows=(self.app, self.manager), **kwargs)

    def test_new_result_profile_lookup_and_default_restore(self):
        self.assertEqual(self.grab(with_crop=True, action_choices=self.choices),
                         ([10, 20, 30, 40], 'crop', 2, 'use'))
        self.window_at_point.assert_called_once_with('game', [0, 0], 125, 240)
        self.resolve_profile.assert_called_once_with(self.cfg, [800, 600], app=self.app)
        self.save_config.assert_called_once_with(self.cfg)
        self.assertEqual(self.select_roi_on_screen.call_args.kwargs['action_choices'], self.choices)
        for w in (self.app, self.manager):
            self.assertEqual(w.attributes.call_args_list,
                             [mock.call('-alpha', 0.0), mock.call('-alpha', 1.0)])
        self.app.lift.assert_called_once()
        self.app.focus_force.assert_called_once()

    def test_restore_false_keeps_every_window_hidden_on_success_and_cancel(self):
        for result in (([110, 220, 30, 40], 'crop', 'use'), (None, None, None)):
            with self.subTest(result=result):
                self.app.reset_mock()
                self.manager.reset_mock()
                self.select_roi_on_screen.return_value = result
                actual = self.grab(with_crop=True, action_choices=self.choices,
                                   restore_windows=False, save_profile=False)
                self.assertEqual(actual, ([10, 20, 30, 40], 'crop', 2, 'use')
                                 if result[0] else (None,) * 4)
                for w in (self.app, self.manager):
                    w.attributes.assert_called_once_with('-alpha', 0.0)
                self.app.lift.assert_not_called()
                self.app.focus_force.assert_not_called()
        self.save_config.assert_not_called()

    def test_all_normal_failure_paths_have_four_empty_items(self):
        for reason in ('no_windows', 'cancel', 'outside', 'profile_full'):
            with self.subTest(reason=reason):
                self.locate_all.return_value = [] if reason == 'no_windows' else [self.window]
                self.select_roi_on_screen.return_value = ((None,) * 3 if reason == 'cancel'
                                                         else ([110, 220, 30, 40], 'crop', 'use'))
                self.window_at_point.return_value = None if reason == 'outside' else self.window
                self.resolve_profile.return_value = None if reason == 'profile_full' else 2
                self.assertEqual(self.grab(with_crop=True, action_choices=self.choices), (None,) * 4)
        self.save_config.assert_not_called()

    def test_default_three_item_interface_and_no_crop_action_interface(self):
        self.select_roi_on_screen.return_value = [110, 220, 30, 40]
        self.assertEqual(self.grab(track_profile=False), ([10, 20, 30, 40], None, None))
        self.assertNotIn('action_choices', self.select_roi_on_screen.call_args.kwargs)
        self.select_roi_on_screen.return_value = None
        self.assertEqual(self.grab(), (None,) * 3)
        self.select_roi_on_screen.return_value = ([110, 220, 30, 40], 'use')
        self.assertEqual(self.grab(action_choices=self.choices, track_profile=False),
                         ([10, 20, 30, 40], None, None, 'use'))

    def test_exception_restore_respects_switch(self):
        self.select_roi_on_screen.side_effect = RuntimeError('capture failed')
        for restore in (True, False):
            self.app.reset_mock()
            with self.subTest(restore=restore), self.assertRaises(RuntimeError):
                self.grab(with_crop=True, action_choices=self.choices, restore_windows=restore)
            self.assertEqual(self.app.lift.call_count, int(restore))
            self.assertEqual(self.app.focus_force.call_count, int(restore))
            self.assertEqual(self.app.attributes.call_count, 2 if restore else 1)


if __name__ == '__main__':
    unittest.main()
