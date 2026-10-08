# -*- coding: utf-8 -*-
"""自由点击标定门禁：无 Tk、假框选、独立临时图片与完整配置。"""
import copy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from mhxy.core import calib_profiles as cp, config as cfg_mod, vision
from mhxy.gui import app as gui, calibrate_dialog as cd


class FreeClickAssetsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="freeclick_assets_")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for target, attr, value in ((cfg_mod, "PROJECT_ROOT", self.root),
                                    (cfg_mod, "CONFIG_PATH", self.root / "config.json"),
                                    (cfg_mod, "CONFIG_BACKUP_DIR", self.root / "config_backups")):
            patch = mock.patch.object(target, attr, value)
            patch.start()
            self.addCleanup(patch.stop)
        self.cfg = copy.deepcopy(cfg_mod.DEFAULT_CONFIG)
        self.first = cp.get_or_create(self.cfg, [1200, 900])
        self.lists = [
            {"id": "a", "name": "A", "items": [
                {"name": "one", "template": "templates/custom.png", "extra": 7},
                {"name": "two", "template": "templates/fc_two.png"}]},
            {"id": "b", "name": "B", "items": [{"name": "other", "template": "templates/other.png"}]},
        ]
        tc = self.cfg["tasks"]["freeclick"]
        tc.update(lists=copy.deepcopy(self.lists), selected_list="a",
                  items=copy.deepcopy(self.lists[0]["items"]))
        cp.set_template_profile(self.cfg, "freeclick", "one", self.first)
        self.page = object.__new__(gui.FreeClickPage)
        self.page.app = SimpleNamespace(cfg=self.cfg)
        self.page.runner = None
        self.page._lists = copy.deepcopy(self.lists)
        self.page._selected_list_id = "a"
        self.page._items = copy.deepcopy(self.lists[0]["items"])
        self.page._rows = [object(), object()]
        self.page._sig = [(x["name"], x["template"]) for x in self.page._items]
        self.page._log_line = mock.Mock()
        self.page._ask_name = mock.Mock(return_value="new")
        self.page._render_items = mock.Mock()
        self.snapshot()
        self.grab = self.patch(cd, "grab_roi_on_app", side_effect=self.capture)
        self.encode = self.patch(vision, "encode_png", return_value=b"new png")
        self.real_save = cfg_mod.save_config
        self.save = self.patch(cfg_mod, "save_config")

    def patch(self, target, attr, **kwargs):
        patch = mock.patch.object(target, attr, **kwargs)
        value = patch.start()
        self.addCleanup(patch.stop)
        return value

    def snapshot(self):
        self.before = copy.deepcopy(self.page.app.cfg)
        self.before_lists = copy.deepcopy(self.page._lists)
        self.before_items = copy.deepcopy(self.page._items)
        self.before_sig = copy.deepcopy(self.page._sig)

    def capture(self, app, draft, prompt, **kwargs):
        self.assertIsNot(draft, app.cfg)
        self.assertTrue(kwargs["with_crop"])
        self.assertFalse(kwargs["save_profile"])
        pid = cp.get_or_create(draft, [1600, 1200])
        return [1, 2, 3, 4], SimpleNamespace(size=12), pid

    def action(self, rec=False):
        if rec:
            self.page._recalibrate(self.page._rows[0])
        else:
            self.page._add_shot()

    def image(self, rec=False):
        path = self.root / ("templates/custom.png" if rec else "templates/fc_new.png")
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def unchanged(self):
        self.assertIs(self.page.app.cfg, self.cfg)
        self.assertEqual(self.page.app.cfg, self.before)
        self.assertEqual(self.page._lists, self.before_lists)
        self.assertEqual(self.page._items, self.before_items)
        self.assertEqual(self.page._sig, self.before_sig)
        self.page._render_items.assert_not_called()
        self.assertFalse(any(c.args[1] == "hit" for c in self.page._log_line.call_args_list))

    def success(self, rec):
        path = self.image(rec)
        path.write_bytes(b"old")
        def saved(draft):
            self.unchanged()
            self.assertEqual(path.read_bytes(), b"new png")
            tc = draft["tasks"]["freeclick"]
            self.assertEqual(tc["selected_list"], "a")
            self.assertEqual(tc["items"], tc["lists"][0]["items"])
            self.assertEqual(tc["lists"][1], self.lists[1])
            self.assertEqual(tc["items"][:2], self.lists[0]["items"])
            self.assertEqual(len(tc["items"]), 2 if rec else 3)
            key = "one" if rec else "new"
            self.assertEqual(cp.task_profile(draft, "freeclick"), cp.active_id(draft))
            self.assertEqual(cp.template_profile(draft, "freeclick", key), cp.active_id(draft))
            self.assertEqual(draft["targets"]["base_size"], [1600, 1200])
            self.assertEqual(set(draft), set(self.before))
        self.save.side_effect = saved
        self.action(rec)
        self.save.assert_called_once()
        self.page._render_items.assert_called_once_with(self.page._lists[0]["items"])
        self.assertIsNot(self.page.app.cfg, self.cfg)
        self.assertEqual(self.cfg, self.before)

    def test_add_success_commits_once_before_publish(self):
        self.success(False)

    def test_recalibrate_success_preserves_path_order_and_other_list(self):
        self.success(True)

    def test_naming_cancel_discards_size_draft(self):
        self.page._ask_name.return_value = None
        self.action()
        self.unchanged()
        self.save.assert_not_called()
        self.encode.assert_not_called()
        self.assertFalse(self.image().exists())

    def test_cancel_empty_and_capture_exception_do_not_submit(self):
        for rec in (False, True):
            for result in ((None, None, None), ([1], None, self.first),
                           ([1], SimpleNamespace(size=0), self.first), OSError("capture")):
                with self.subTest(rec=rec, result=result):
                    self.grab.side_effect = result if isinstance(result, Exception) else None
                    self.grab.return_value = result
                    self.action(rec)
                    self.unchanged()
        self.save.assert_not_called()
        self.encode.assert_not_called()

    def test_encoding_failure_preserves_image_and_page(self):
        for rec in (False, True):
            for failure in (None, OSError("encode")):
                with self.subTest(rec=rec, failure=failure):
                    path = self.image(rec)
                    path.write_bytes(b"old")
                    self.encode.side_effect = failure
                    self.encode.return_value = None
                    self.action(rec)
                    self.assertEqual(path.read_bytes(), b"old")
                    self.unchanged()
        self.save.assert_not_called()

    def test_save_failure_restores_old_or_removes_new_image(self):
        self.save.side_effect = OSError("save")
        for rec in (False, True):
            for exists in (False, True):
                with self.subTest(rec=rec, exists=exists):
                    path = self.image(rec)
                    if exists:
                        path.write_bytes(b"old")
                    self.action(rec)
                    self.assertEqual(path.read_bytes() if path.exists() else None,
                                     b"old" if exists else None)
                    self.unchanged()

    def test_partial_write_failure_restores_image(self):
        original = Path.write_bytes
        def write(path, data):
            if data == b"new png":
                original(path, b"partial")
                raise OSError("write")
            return original(path, data)
        for rec in (False, True):
            path = self.image(rec)
            path.write_bytes(b"old")
            with mock.patch.object(Path, "write_bytes", write):
                self.action(rec)
            self.assertEqual(path.read_bytes(), b"old")
            self.unchanged()
        self.save.assert_not_called()

    def test_read_failure_never_writes(self):
        self.image().write_bytes(b"old")
        with mock.patch.object(Path, "read_bytes", side_effect=OSError("read")), \
                mock.patch.object(Path, "write_bytes") as write:
            self.action()
            write.assert_not_called()
        self.unchanged()
        self.save.assert_not_called()

    def test_restore_failure_reports_error_without_publish(self):
        self.image(True).write_bytes(b"old")
        self.save.side_effect = OSError("save")
        original = Path.write_bytes
        def write(path, data):
            if data == b"old":
                raise OSError("restore")
            return original(path, data)
        with mock.patch.object(Path, "write_bytes", write):
            self.action(True)
        self.unchanged()
        self.assertIn("恢复失败", self.page._log_line.call_args.args[0])

    def test_full_profile_replacement_is_discarded_on_failure(self):
        cp.get_or_create(self.cfg, [1700, 1300])
        cp.get_or_create(self.cfg, [1900, 1400])
        self.snapshot()
        def capture(app, draft, prompt, **kwargs):
            pid = cd.resolve_profile(draft, [2200, 1600],
                                     app=SimpleNamespace(on_profile_full=lambda size: self.first))
            return [1], SimpleNamespace(size=12), pid
        self.grab.side_effect = capture
        self.save.side_effect = OSError("save")
        for rec in (False, True):
            self.action(rec)
            self.unchanged()
        self.assertEqual(cp.template_profile(self.cfg, "freeclick", "one"), self.first)

    def test_missing_template_path_only_published_on_success(self):
        self.page._items[0]["template"] = None
        self.page._lists[0]["items"][0]["template"] = None
        self.snapshot()
        self.save.side_effect = OSError("save")
        self.action(True)
        self.unchanged()
        self.assertFalse((self.root / "templates/fc_one.png").exists())
        self.save.side_effect = None
        self.action(True)
        self.assertEqual(self.page._lists[0]["items"][0]["template"], "templates/fc_one.png")

    def test_busy_and_stale_row_do_not_capture(self):
        self.page.runner = SimpleNamespace(is_running=lambda: True)
        for rec in (False, True):
            self.action(rec)
        self.page.runner = None
        self.page._recalibrate(object())
        self.grab.assert_not_called()
        self.unchanged()

    def test_missing_list_does_not_capture_or_write(self):
        self.page._selected_list_id = "missing"
        for rec in (False, True):
            self.action(rec)
        self.grab.assert_not_called()
        self.save.assert_not_called()
        self.unchanged()

    def test_delete_item_commits_once_before_publish_and_keeps_image(self):
        path = self.image(True)
        path.write_bytes(b"old")
        old_items, old_lists = self.page._items, self.page._lists
        def saved(draft):
            self.unchanged()
            tc = draft["tasks"]["freeclick"]
            self.assertEqual(tc["items"], self.lists[0]["items"][1:])
            self.assertEqual(tc["items"], tc["lists"][0]["items"])
            self.assertNotIn("one", tc["templates_calib"])
            self.assertEqual(tc["lists"][1], self.lists[1])
        self.save.side_effect = saved
        self.page._delete_item(self.page._rows[0])
        self.save.assert_called_once()
        self.assertEqual(old_items, self.before_items)
        self.assertEqual(old_lists, self.before_lists)
        self.assertEqual(path.read_bytes(), b"old")
        self.page._render_items.assert_called_once_with(self.lists[0]["items"][1:])

    def test_delete_failure_preserves_all_references(self):
        self.page.refresh = mock.Mock()
        refs = self.page._items, self.page._lists, self.page._rows, self.page._sig
        self.save.side_effect = OSError("save")
        for action in (lambda: self.page._delete_item(self.page._rows[0]), self.page._delete_list):
            action()
            self.unchanged()
            for old, current in zip(refs, (self.page._items, self.page._lists,
                                          self.page._rows, self.page._sig)):
                self.assertIs(old, current)
            self.assertEqual(self.page._selected_list_id, "a")
        self.page.refresh.assert_not_called()

    def test_delete_real_atomic_failure_then_success_with_isolated_backups(self):
        import json
        path = self.image(True)
        path.write_bytes(b"old")
        self.real_save(self.cfg)
        disk_before = cfg_mod.CONFIG_PATH.read_bytes()
        self.save.side_effect = self.real_save
        with mock.patch.object(cfg_mod.os, "replace", side_effect=OSError("replace")):
            self.page._delete_item(self.page._rows[0])
        self.unchanged()
        self.assertEqual(cfg_mod.CONFIG_PATH.read_bytes(), disk_before)
        self.assertEqual(path.read_bytes(), b"old")
        self.assertTrue(list((self.root / "config_backups").glob("config-*.json")))
        self.page._delete_item(self.page._rows[0])
        disk = json.loads(cfg_mod.CONFIG_PATH.read_text(encoding="utf-8"))
        self.assertEqual(disk, self.page.app.cfg)
        self.assertEqual(disk["tasks"]["freeclick"]["items"], self.lists[0]["items"][1:])
        self.assertEqual(path.read_bytes(), b"old")

    def test_delete_shared_name_keeps_ownership(self):
        self.page._lists[1]["items"].append(copy.deepcopy(self.page._items[0]))
        self.page._delete_item(self.page._rows[0])
        self.assertEqual(cp.template_profile(self.page.app.cfg, "freeclick", "one"), self.first)

    def test_delete_list_selects_neighbor_and_cleans_only_unused_names(self):
        self.page.refresh = mock.Mock()
        self.page._delete_list()
        tc = self.page.app.cfg["tasks"]["freeclick"]
        self.assertEqual(tc["selected_list"], "b")
        self.assertEqual(tc["lists"], self.lists[1:])
        self.assertEqual(tc["items"], self.lists[1]["items"])
        self.assertNotIn("one", tc["templates_calib"])
        self.save.assert_called_once()
        self.page.refresh.assert_called_once()

    def test_delete_invalid_busy_or_last_list_never_saves(self):
        self.page._delete_item(object())
        self.page._selected_list_id = "missing"
        self.page._delete_list()
        self.page._delete_item(self.page._rows[0])
        self.page._selected_list_id = "a"
        self.page.runner = SimpleNamespace(is_running=lambda: True)
        self.page._delete_list()
        self.page._delete_item(self.page._rows[0])
        self.page.runner = None
        self.page._lists = self.page._lists[:1]
        self.page._delete_list()
        self.save.assert_not_called()

    def test_later_cancel_or_failure_keeps_previous_success(self):
        self.action()
        committed = copy.deepcopy(self.page.app.cfg)
        lists = copy.deepcopy(self.page._lists)
        self.page._render_items.reset_mock()
        self.page._log_line.reset_mock()
        self.page._ask_name.return_value = None
        self.action()
        self.page._ask_name.return_value = "later"
        self.save.side_effect = OSError("save")
        self.action()
        self.assertEqual(self.page.app.cfg, committed)
        self.assertEqual(self.page._lists, lists)
        self.assertEqual(self.image().read_bytes(), b"new png")
        self.assertFalse((self.root / "templates/fc_later.png").exists())
        self.page._render_items.assert_not_called()
        self.assertFalse(any(c.args[1] == "hit" for c in self.page._log_line.call_args_list))
