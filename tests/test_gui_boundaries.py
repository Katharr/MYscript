# -*- coding: utf-8 -*-
"""拆分页面的离线边界门禁；不建真实 Tk，不探测窗口、不读写真实资产。"""
import ast
import builtins
import copy
import importlib
import inspect
import symtable
import unittest
from types import SimpleNamespace
from unittest import mock

from mhxy.core.config import DEFAULT_CONFIG
from mhxy.gui.app import App
from mhxy.gui import widgets, shortcuts


class GuiBoundaryTests(unittest.TestCase):
    def test_all_registered_pages_import_independently_with_resolved_globals(self):
        """导入成功不足以抓住延迟回调的 NameError，逐个作用域核对全局绑定。"""
        for key, page in App.PAGE_CLASSES.items():
            with self.subTest(page=key):
                module = importlib.import_module(page.__module__)
                if key != 'launch_login':
                    self.assertNotEqual(page.__module__, 'mhxy.gui.app')
                source = inspect.getsource(module)
                symbols = symtable.symtable(source, module.__file__, 'exec')
                known = set(vars(module)) | set(vars(builtins))

                def check(table):
                    for symbol in table.get_symbols():
                        if symbol.is_global() and symbol.is_referenced():
                            self.assertIn(symbol.get_name(), known,
                                          f'{key}: {table.get_name()}: {symbol.get_name()}')
                    for child in table.get_children():
                        check(child)
                check(symbols)

    def test_gui_never_imports_runner_or_platform_input_and_pages_do_not_import_app(self):
        from pathlib import Path
        directory = Path(inspect.getfile(App)).parent
        for path in directory.glob('*.py'):
            with self.subTest(module=path.name):
                tree = ast.parse(path.read_text(encoding='utf-8'))
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom):
                        self.assertNotIn(node.module, ('core.runner', 'core.input'))
                        if path.name != 'app.py':
                            self.assertNotEqual(node.module, 'app')
                    if isinstance(node, ast.Import):
                        for name in node.names:
                            self.assertNotIn(name.name, ('cv2', 'ctypes'))
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                        owner = node.func.value
                        if isinstance(owner, ast.Attribute) and owner.attr.startswith('runner'):
                            self.assertNotIn(node.func.attr, ('start', 'stop', 'is_running'))

    def test_page_start_failure_stays_visible_success_binds_once_and_stop_only_requests_stop(self):
        for key in ('sniper', 'freeclick', 'treasure_map', 'escort', 'secret_realm',
                    'dungeon', 'daily', 'launch_login'):
            page = App.PAGE_CLASSES[key]
            module = importlib.import_module(page.__module__)
            for success in (False, True):
                with self.subTest(page=key, success=success):
                    stub = SimpleNamespace(runner=None, app=mock.Mock(), btn_run=mock.Mock(),
                                           TASK_NAME=getattr(page, 'TASK_NAME', key),
                                           LOG_SOURCE=getattr(page, 'LOG_SOURCE', key),
                                           _cal_dialog=None, _selected='taohaiqu',
                                           _apply_params=mock.Mock(), _apply_time_limit=mock.Mock(), _save=mock.Mock(),
                                           _selected_title=lambda: '副本', _log_line=mock.Mock(),
                                           _set_editable=mock.Mock(), _render_progress=mock.Mock(),
                                           _set_edit_controls=mock.Mock(), entry_count=mock.Mock(),
                                           switch_mode=mock.Mock())
                    stub.entry_count.get.return_value = '1'
                    stub.switch_mode.get.return_value = False
                    stub._busy = lambda: bool(stub.runner and runtime.is_running(stub.runner))
                    stub._toggle_run = lambda: page._toggle_run(stub)
                    runtime = mock.Mock()
                    runtime.is_running.return_value = False
                    runtime.start.return_value = success, ['not ready'] if not success else []
                    cfg = copy.deepcopy(DEFAULT_CONFIG)
                    with mock.patch.object(module, 'get_runtime', return_value=runtime), \
                         mock.patch.object(module, 'get_task', return_value=mock.Mock()), \
                         mock.patch.object(module.cfg_mod, 'load_config', return_value=cfg), \
                         mock.patch.object(module.cfg_mod, 'save_config'):
                        page._toggle_run(stub)
                        runtime.start.assert_called_once()
                        if not success:
                            stub.app.on_task_started.assert_not_called()
                            self.assertIsNone(stub.runner)
                        else:
                            stub.app.on_task_started.assert_called_once()
                            runtime.is_running.return_value = True
                            handle = stub.runner
                            page._toggle_run(stub)
                            runtime.stop.assert_called_once_with(handle)
                            self.assertEqual(runtime.create_runner.call_count, 1)
                            self.assertEqual(runtime.start.call_count, 1)

    def test_shortcut_configuration_roundtrips_and_invalid_key_is_disabled(self):
        for key in shortcuts.HOTKEY_NAMES:
            for ctrl, alt, shift in ((True, True, False), (False, False, True), (False, False, False)):
                value = shortcuts._compose_stop_hotkey(ctrl, alt, shift, key)
                self.assertEqual(shortcuts._split_stop_hotkey(value), (ctrl, alt, shift, key))
        self.assertEqual(shortcuts._parse_stop_hotkey('ctrl+unknown'), ([0x11], None))

    def test_monitor_snapshot_selects_containing_monitor_and_freezes_original_rgb(self):
        from mhxy.core import vision
        primary = {'left': 0, 'top': 0, 'width': 10, 'height': 10}
        secondary = {'left': -10, 'top': 0, 'width': 10, 'height': 10}
        snapshot = SimpleNamespace(width=10, height=10, rgb=b'pixels')
        screen = mock.Mock(monitors=[{}, primary, secondary])
        screen.grab.return_value = snapshot
        for point, expected in ((None, primary), ((-1, 1), secondary), ((100, 100), primary)):
            with self.subTest(point=point), mock.patch('mss.mss') as capture:
                capture.return_value.__enter__.return_value = screen
                mon, size, rgb = vision.capture_monitor_rgb(point)
                self.assertEqual(mon, expected)
                self.assertEqual(size, (10, 10))
                self.assertEqual(rgb, b'pixels')
                screen.grab.assert_called_with(expected)

    def test_calibration_rgb_conversion_preserves_color_and_shape(self):
        import numpy as np
        from PIL import Image
        from mhxy.core import vision
        image = Image.new('RGB', (3, 2), (255, 50, 10))
        bgr = vision.rgb_to_bgr(image)
        self.assertEqual(bgr.shape, (2, 3, 3))
        np.testing.assert_array_equal(bgr[0, 0], [10, 50, 255])

    def test_physical_stop_observations_preserve_corner_and_key_predicates(self):
        from mhxy.core import emergency
        api = mock.Mock()
        api.GetSystemMetrics.side_effect = lambda index: (1920, 1080)[index]
        api.GetAsyncKeyState.return_value = 0x8000
        with mock.patch.object(emergency.ctypes, 'windll', SimpleNamespace(user32=api), create=True):
            self.assertTrue(emergency.in_failsafe_corner(1918, 0, 'top_right'))
            self.assertFalse(emergency.in_failsafe_corner(100, 100, 'top_right'))
            self.assertFalse(emergency.in_failsafe_corner(1918, 0, 'off'))
            self.assertTrue(emergency.vk_down(0x7B))
            api.GetAsyncKeyState.return_value = 1
            self.assertFalse(emergency.vk_down(0x7B))
            api.GetSystemMetrics.side_effect = RuntimeError('unavailable')
            self.assertFalse(emergency.in_failsafe_corner(0, 0, 'top_left'))

    def test_thumbnail_fallback_uses_rgb_observation_and_retains_gui_image(self):
        import numpy as np
        from PIL import Image
        rgb = np.zeros((8, 16, 3), dtype=np.uint8)
        rgb[:, :, 0] = 255
        refs = []
        with mock.patch.object(widgets.os.path, 'exists', return_value=True), \
             mock.patch.object(Image, 'open', side_effect=OSError('decode failed')), \
             mock.patch('mhxy.core.vision.load_template_rgb', return_value=rgb) as pixels, \
             mock.patch.object(widgets.ctk, 'CTkImage') as image:
            result = widgets.load_thumb('templates/fake.png', refs, max_h=40)
        pixels.assert_called_once_with('templates/fake.png')
        self.assertIs(result, image.return_value)
        self.assertEqual(refs, [result])
        self.assertEqual(image.call_args.kwargs['size'], (80, 40))
        self.assertEqual(image.call_args.kwargs['light_image'].getpixel((0, 0)), (255, 0, 0))


if __name__ == '__main__':
    unittest.main()
