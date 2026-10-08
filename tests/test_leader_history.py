# -*- coding: utf-8 -*-
"""队长资产离线门禁：虚拟文件与配置保存，不接触真实模板。"""
import copy
import os
from pathlib import Path
import tempfile
from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest import mock

from mhxy.core import config as cfg_mod
from mhxy.core import leader_history as lh


class LeaderHistoryTests(unittest.TestCase):
    def setUp(self):
        self.cfg = copy.deepcopy(cfg_mod.DEFAULT_CONFIG)
        self.files = {}
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.save = self.stack.enter_context(mock.patch.object(cfg_mod, "save_config"))
        self.stack.enter_context(mock.patch.object(lh, "_exists", side_effect=lambda p: p in self.files))
        self.stack.enter_context(mock.patch.object(lh, "_read_bytes", side_effect=self.read_bytes, create=True))
        self.stack.enter_context(mock.patch.object(lh, "_write_bytes", side_effect=self.write_bytes, create=True))
        self.stack.enter_context(mock.patch.object(lh, "_remove", side_effect=self.remove))
        self.copy = self.stack.enter_context(mock.patch.object(lh, "_copy", side_effect=self.copy_bytes))

    def read_bytes(self, path):
        return self.files.get(path)

    def write_bytes(self, path, data):
        self.files[path] = data

    def remove(self, path):
        self.files.pop(path, None)

    def copy_bytes(self, src, dst):
        if src not in self.files:
            return False
        self.files[dst] = self.files[src]
        return True

    def history(self, count=4, active=0):
        entries = [{"slot": lh.SLOT_REL.format(i), "label": str(i)} for i in range(count)]
        tc = self.cfg["tasks"]["teaming"]
        tc.update(leader_id_history=entries, leader_id_active=active)
        tc["templates"]["leader_id"] = lh.ACTIVE_REL if count else None
        for i, entry in enumerate(entries):
            self.files[entry["slot"]] = bytes([i])
        if count:
            self.files[lh.ACTIVE_REL] = bytes([active])
        return entries

    def assert_failed_without_commit(self, action):
        cfg_before, files_before = copy.deepcopy(self.cfg), dict(self.files)
        with self.assertRaises(lh.LeaderHistoryError):
            action()
        self.assertEqual(self.cfg, cfg_before)
        self.assertEqual(self.files, files_before)
        self.save.assert_not_called()

    def test_reads_do_not_migrate_save_or_modify(self):
        self.files[lh.ACTIVE_REL] = b"legacy"
        before = copy.deepcopy(self.cfg)
        self.assertEqual(lh.get_history(self.cfg), [])
        self.assertEqual(lh.get_active_index(self.cfg), 0)
        self.assertEqual(self.cfg, before)
        self.copy.assert_not_called()
        self.save.assert_not_called()

    def test_history_is_deep_snapshot(self):
        self.history()
        before = copy.deepcopy(self.cfg)
        lh.get_history(self.cfg)[0]["label"] = "changed"
        self.assertEqual(self.cfg, before)
        self.save.assert_not_called()

    def test_initialize_migrates_once_and_persists(self):
        self.files[lh.ACTIVE_REL] = b"legacy"
        result = lh.initialize(self.cfg)
        self.assertEqual(len(result), 1)
        self.assertEqual(self.files[result[0]["slot"]], b"legacy")
        self.assertEqual(lh.get_active_index(self.cfg), 0)
        self.assertEqual(self.cfg["tasks"]["teaming"]["templates"]["leader_id"], lh.ACTIVE_REL)
        self.save.assert_called_once()
        self.assertEqual(lh.initialize(self.cfg), result)
        self.save.assert_called_once()
        self.copy.assert_called_once()

    def test_initialize_copy_failure_zero_commit(self):
        self.files[lh.ACTIVE_REL] = b"legacy"
        self.copy.side_effect = None
        self.copy.return_value = False
        self.assert_failed_without_commit(lambda: lh.initialize(self.cfg))

    def test_initialize_normalizes_truncation_active_and_readiness_once(self):
        self.history()
        tc = self.cfg["tasks"]["teaming"]
        tc["leader_id_history"].append({"slot": "unused", "label": "old"})
        tc["leader_id_active"] = 4
        self.files[lh.ACTIVE_REL] = b"truncated-current"
        tc["templates"]["leader_id"] = "old/path.png"
        self.assertEqual(len(lh.initialize(self.cfg)), 4)
        self.assertEqual(self.files[lh.ACTIVE_REL], b"\x00")
        self.assertEqual(lh.get_active_index(self.cfg), 0)
        self.save.assert_called_once()
        lh.initialize(self.cfg)
        self.save.assert_called_once()

    def test_invalid_active_reads_as_zero_without_mutation(self):
        self.history(2)
        for value in (None, "bad", "1", True, -1, 99, 1.5):
            with self.subTest(value=value):
                self.cfg["tasks"]["teaming"]["leader_id_active"] = value
                before = copy.deepcopy(self.cfg)
                self.assertEqual(lh.get_active_index(self.cfg), 0)
                self.assertEqual(self.cfg, before)
        self.save.assert_not_called()

    def test_empty_initialize_clears_readiness_without_creating_assets(self):
        self.cfg["tasks"]["teaming"]["templates"]["leader_id"] = lh.ACTIVE_REL
        lh.initialize(self.cfg)
        self.assertIsNone(self.cfg["tasks"]["teaming"]["templates"]["leader_id"])
        self.assertEqual(self.files, {})
        self.save.assert_called_once()
        lh.initialize(self.cfg)
        self.save.assert_called_once()

    def test_first_calibration_is_not_migrated_twice(self):
        self.files[lh.ACTIVE_REL] = b"new"
        self.assertEqual(len(lh.push_after_calibrate(self.cfg)), 1)
        lh.initialize(self.cfg)
        self.assertEqual(len(lh.get_history(self.cfg)), 1)
        self.copy.assert_called_once()
        self.save.assert_called_once()

    def test_full_push_copy_failure_does_not_evict(self):
        self.history()
        self.files[lh.ACTIVE_REL] = b"new"
        self.copy.side_effect = None
        self.copy.return_value = False
        self.assert_failed_without_commit(lambda: lh.push_after_calibrate(self.cfg))

    def test_full_push_reuses_oldest_slot(self):
        old = copy.deepcopy(self.history())
        self.files[lh.ACTIVE_REL] = b"new"
        result = lh.push_after_calibrate(self.cfg)
        self.assertEqual(len(result), 4)
        self.assertEqual(result[0]["slot"], old[-1]["slot"])
        self.assertEqual(result[1:], old[:-1])
        self.assertEqual(self.files[result[0]["slot"]], b"new")
        self.assertEqual(lh.get_active_index(self.cfg), 0)
        self.save.assert_called_once()

    def test_activate_failure_zero_commit(self):
        self.history(3)
        self.copy.side_effect = None
        self.copy.return_value = False
        self.assert_failed_without_commit(lambda: lh.activate(self.cfg, 2))

    def test_activate_reorders_and_keeps_fixed_path(self):
        old = copy.deepcopy(self.history(3))
        result = lh.activate(self.cfg, 2)
        self.assertEqual(result, [old[2], old[0], old[1]])
        self.assertEqual(self.files[lh.ACTIVE_REL], b"\x02")
        self.assertEqual(self.cfg["tasks"]["teaming"]["templates"]["leader_id"], lh.ACTIVE_REL)

    def test_delete_active_copy_failure_zero_commit(self):
        self.history(3)
        self.copy.side_effect = None
        self.copy.return_value = False
        self.assert_failed_without_commit(lambda: lh.delete(self.cfg, 0))

    def test_delete_active_replaces_before_removing(self):
        old = copy.deepcopy(self.history(3, active=1))
        result = lh.delete(self.cfg, 1)
        self.assertEqual(result, [old[0], old[2]])
        self.assertEqual(self.files[lh.ACTIVE_REL], b"\x00")
        self.assertNotIn(old[1]["slot"], self.files)
        self.assertEqual(lh.get_active_index(self.cfg), 0)

    def test_delete_before_active_preserves_active_bytes(self):
        old = copy.deepcopy(self.history(3, active=2))
        self.assertEqual(lh.delete(self.cfg, 0), old[1:])
        self.assertEqual(lh.get_active_index(self.cfg), 1)
        self.assertEqual(self.files[lh.ACTIVE_REL], b"\x02")
        self.copy.assert_not_called()

    def test_delete_last_clears_assets_and_does_not_resurrect(self):
        self.history(1)
        self.assertEqual(lh.delete(self.cfg, 0), [])
        self.assertEqual(self.files, {})
        self.assertIsNone(self.cfg["tasks"]["teaming"]["templates"]["leader_id"])
        lh.initialize(self.cfg)
        self.assertEqual(lh.get_history(self.cfg), [])
        self.save.assert_called_once()

    def test_remove_failure_restores_assets_and_memory(self):
        self.history(1)
        original = self.remove
        def fail_active(path):
            if path == lh.ACTIVE_REL:
                raise OSError("remove failed")
            original(path)
        with mock.patch.object(lh, "_remove", side_effect=fail_active):
            self.assert_failed_without_commit(lambda: lh.delete(self.cfg, 0))

    def test_save_failure_restores_assets_and_memory(self):
        for operation in ("push", "activate", "delete", "initialize"):
            with self.subTest(operation=operation):
                self.files.clear()
                self.history()
                if operation == "initialize":
                    self.cfg["tasks"]["teaming"]["leader_id_history"] = []
                self.files[lh.ACTIVE_REL] = b"current"
                before, assets = copy.deepcopy(self.cfg), dict(self.files)
                self.save.reset_mock()
                self.save.side_effect = OSError("save failed")
                action = {"push": lambda: lh.push_after_calibrate(self.cfg),
                          "activate": lambda: lh.activate(self.cfg, 2),
                          "delete": lambda: lh.delete(self.cfg, 0),
                          "initialize": lambda: lh.initialize(self.cfg)}[operation]
                with self.assertRaises(lh.LeaderHistoryError):
                    action()
                self.assertEqual(self.cfg, before)
                self.assertEqual(self.files, assets)
                self.save.assert_called_once()

    def test_invalid_indices_do_not_save_or_touch_assets(self):
        self.history()
        for idx in (-1, 4, None, True, "1", 1.5):
            for action in (lh.activate, lh.delete):
                with self.subTest(idx=idx, action=action.__name__):
                    with self.assertRaises(lh.LeaderHistoryError):
                        action(self.cfg, idx)
        self.save.assert_not_called()
        self.copy.assert_not_called()


    def test_initialize_active_repair_copy_failure_zero_commit(self):
        self.history(2)
        self.cfg["tasks"]["teaming"]["leader_id_active"] = 99
        self.copy.side_effect = None
        self.copy.return_value = False
        self.assert_failed_without_commit(lambda: lh.initialize(self.cfg))

    def test_delete_slot_failure_restores_replacement_and_last_active(self):
        for count in (1, 3):
            with self.subTest(count=count):
                self.files.clear()
                old = self.history(count)
                removed_slot = old[0]["slot"]
                original = self.remove
                def fail_slot(path):
                    if path == removed_slot:
                        raise OSError("slot remove failed")
                    original(path)
                with mock.patch.object(lh, "_remove", side_effect=fail_slot):
                    self.assert_failed_without_commit(lambda: lh.delete(self.cfg, 0))

    def test_partial_copy_failure_restores_overwritten_slot(self):
        self.history()
        self.files[lh.ACTIVE_REL] = b"new"
        def partial_failure(src, dst):
            self.files[dst] = b"partial"
            return False
        self.copy.side_effect = partial_failure
        self.assert_failed_without_commit(lambda: lh.push_after_calibrate(self.cfg))

    def test_restore_failure_is_explicit(self):
        self.history()
        before = copy.deepcopy(self.cfg)
        self.save.side_effect = OSError("save failed")
        with mock.patch.object(lh, "_write_bytes", side_effect=OSError("restore failed")):
            with self.assertRaisesRegex(lh.LeaderHistoryError, "恢复失败"):
                lh.activate(self.cfg, 2)
        self.assertEqual(self.cfg, before)


    def test_calibration_commits_draft_assets_and_history_once(self):
        from mhxy.core import calib_profiles as calib
        old = copy.deepcopy(self.history())
        draft = copy.deepcopy(self.cfg)
        pid = calib.get_or_create(draft, [800, 600])
        calib.set_task_profile(draft, "teaming", pid)
        calib.set_template_profile(draft, "teaming", "leader_id", pid)
        result = lh.commit_calibration(self.cfg, draft, b"new-png")
        self.assertEqual(result[0]["slot"], old[-1]["slot"])
        self.assertEqual(result[1:], old[:-1])
        self.assertEqual(self.files[lh.ACTIVE_REL], b"new-png")
        self.assertEqual(self.files[old[-1]["slot"]], b"new-png")
        self.assertEqual(self.cfg["calib_profiles"], draft["calib_profiles"])
        self.assertEqual(self.cfg["targets"], draft["targets"])
        self.assertEqual(calib.template_profile(self.cfg, "teaming", "leader_id"), pid)
        self.assertEqual(calib.task_profile(self.cfg, "teaming"), pid)
        self.save.assert_called_once()
        self.assertEqual(self.save.call_args.args[0], self.cfg)

    def test_calibration_first_capture_has_one_history_without_migration(self):
        self.files[lh.ACTIVE_REL] = b"legacy"
        result = lh.commit_calibration(self.cfg, copy.deepcopy(self.cfg), b"new")
        self.assertEqual(len(result), 1)
        self.assertEqual(self.files[result[0]["slot"]], b"new")
        lh.initialize(self.cfg)
        self.save.assert_called_once()

    def test_calibration_partial_copy_restores_precalibration_bytes(self):
        self.history()
        def fail(src, dst):
            self.files[dst] = b"partial"
            return False
        self.copy.side_effect = fail
        self.assert_failed_without_commit(
            lambda: lh.commit_calibration(self.cfg, copy.deepcopy(self.cfg), b"new"))

    def test_calibration_partial_active_write_restores_precalibration_bytes(self):
        self.history()
        original = self.write_bytes
        def fail(path, data):
            if data == b"new":
                self.files[path] = b"partial"
                raise OSError("active write failed")
            original(path, data)
        with mock.patch.object(lh, "_write_bytes", side_effect=fail):
            self.assert_failed_without_commit(
                lambda: lh.commit_calibration(self.cfg, copy.deepcopy(self.cfg), b"new"))

    def test_calibration_save_failure_restores_existing_and_missing_assets(self):
        for count in (0, 4):
            with self.subTest(count=count):
                self.files.clear()
                self.history(count)
                before, assets = copy.deepcopy(self.cfg), dict(self.files)
                draft = copy.deepcopy(self.cfg)
                draft["targets"]["base_size"] = [800, 600]
                self.save.reset_mock()
                self.save.side_effect = OSError("save failed")
                with self.assertRaises(lh.LeaderHistoryError):
                    lh.commit_calibration(self.cfg, draft, b"new")
                self.assertEqual(self.cfg, before)
                self.assertEqual(self.files, assets)
                self.save.assert_called_once()

    def test_calibration_restore_failure_is_explicit(self):
        self.history()
        before = copy.deepcopy(self.cfg)
        self.save.side_effect = OSError("save failed")
        original = self.write_bytes
        def fail_restore(path, data):
            if data != b"new":
                raise OSError("restore failed")
            original(path, data)
        with mock.patch.object(lh, "_write_bytes", side_effect=fail_restore):
            with self.assertRaisesRegex(lh.LeaderHistoryError, "恢复失败"):
                lh.commit_calibration(self.cfg, copy.deepcopy(self.cfg), b"new")
        self.assertEqual(self.cfg, before)

    def test_direct_entry_commits_once_and_updates_app_only_on_success(self):
        from mhxy.gui import calibrate_dialog as dialog
        from mhxy.core import calib_profiles as calib
        self.history()
        def grab(app, draft, prompt, **kwargs):
            self.assertFalse(kwargs["save_profile"])
            pid = calib.get_or_create(draft, [800, 600])
            return [0, 0, 2, 2], SimpleNamespace(size=4), pid
        for fail in (False, True):
            with self.subTest(fail=fail):
                app = SimpleNamespace(cfg=copy.deepcopy(self.cfg))
                before, assets = copy.deepcopy(app.cfg), dict(self.files)
                toast = mock.Mock()
                self.save.reset_mock()
                self.save.side_effect = OSError("save failed") if fail else None
                with mock.patch.object(cfg_mod, "load_config", side_effect=lambda: copy.deepcopy(self.cfg)), \
                        mock.patch.object(dialog, "grab_roi_on_app", side_effect=grab), \
                        mock.patch.object(dialog.vision, "encode_png", return_value=b"new"), \
                        mock.patch.object(dialog.vision, "save_image") as save_image, \
                        mock.patch.object(lh, "initialize") as initialize:
                    ok = dialog.calibrate_template_direct(app, "teaming", "leader_id", "队长ID", toast)
                self.assertEqual(ok, not fail)
                self.save.assert_called_once()
                save_image.assert_not_called()
                initialize.assert_not_called()
                if fail:
                    self.assertEqual(app.cfg, before)
                    self.assertEqual(self.files, assets)
                    self.assertIn("失败", toast.call_args.args[0])
                else:
                    self.assertEqual(len(lh.get_history(app.cfg)), 4)
                    self.assertEqual(calib.template_profile(app.cfg, "teaming", "leader_id"), 1)

    def test_direct_entry_cancel_or_encoding_failure_never_saves(self):
        from mhxy.gui import calibrate_dialog as dialog
        for cancelled in (True, False):
            with self.subTest(cancelled=cancelled):
                app = SimpleNamespace(cfg=copy.deepcopy(self.cfg))
                before = copy.deepcopy(app.cfg)
                result = (None, None, None) if cancelled else ([0, 0, 2, 2], SimpleNamespace(size=4), None)
                with mock.patch.object(cfg_mod, "load_config", return_value=copy.deepcopy(self.cfg)), \
                        mock.patch.object(dialog, "grab_roi_on_app", return_value=result), \
                        mock.patch.object(dialog.vision, "encode_png", return_value=None):
                    self.assertFalse(dialog.calibrate_template_direct(app, "teaming", "leader_id", "队长ID"))
                self.assertEqual(app.cfg, before)
                self.save.assert_not_called()
                self.assertEqual(self.files, {})

    def test_calibration_rejects_empty_encoding_without_writes(self):
        self.history()
        self.assert_failed_without_commit(
            lambda: lh.commit_calibration(self.cfg, copy.deepcopy(self.cfg), b""))


class ByteCopyTests(unittest.TestCase):
    def test_copy_preserves_exact_bytes_with_unicode_paths(self):
        # 独立系统临时目录；不用真实 PROJECT_ROOT 下的任何模板。
        with tempfile.TemporaryDirectory(prefix="leader_bytes_") as temp_dir:
            src = Path(temp_dir) / "源图.png"
            dst = Path(temp_dir) / "目标图.png"
            data = b"\x89PNG\r\n\x1a\nopaque-metadata\x00\xff"
            src.write_bytes(data)
            dst.write_bytes(b"old")
            self.assertTrue(lh._copy(str(src), str(dst)))
            self.assertEqual(dst.read_bytes(), data)
            self.assertEqual(src.read_bytes(), data)
            self.assertEqual(set(os.listdir(temp_dir)), {src.name, dst.name})

    def test_replace_failure_keeps_old_bytes_and_cleans_temp(self):
        with tempfile.TemporaryDirectory(prefix="leader_bytes_") as temp_dir:
            src = Path(temp_dir) / "source.png"
            dst = Path(temp_dir) / "target.png"
            src.write_bytes(b"new")
            dst.write_bytes(b"old")
            with mock.patch.object(lh.os, "replace", side_effect=OSError("replace failed")):
                self.assertFalse(lh._copy(str(src), str(dst)))
            self.assertEqual(dst.read_bytes(), b"old")
            self.assertEqual(set(os.listdir(temp_dir)), {src.name, dst.name})

    def test_missing_source_keeps_destination(self):
        with tempfile.TemporaryDirectory(prefix="leader_bytes_") as temp_dir:
            dst = Path(temp_dir) / "target.png"
            dst.write_bytes(b"old")
            self.assertFalse(lh._copy(str(Path(temp_dir) / "missing.png"), str(dst)))
            self.assertEqual(dst.read_bytes(), b"old")


class GalleryFailureTests(unittest.TestCase):
    def test_initialize_between_reload_and_first_render(self):
        from mhxy.gui.leader_gallery import LeaderIdGallery
        events = []
        app = SimpleNamespace(fonts={}, cfg={})
        loaded = {"loaded": True}
        with ExitStack() as stack:
            for name in ("__init__", "title", "geometry", "minsize", "configure", "transient", "after", "protocol"):
                stack.enter_context(mock.patch.object(LeaderIdGallery.__bases__[0], name, return_value=None))
            stack.enter_context(mock.patch.object(cfg_mod, "load_config", side_effect=lambda: events.append("reload") or loaded))
            stack.enter_context(mock.patch.object(lh, "initialize", side_effect=lambda cfg: events.append(("initialize", cfg is loaded))))
            stack.enter_context(mock.patch.object(LeaderIdGallery, "_build"))
            stack.enter_context(mock.patch.object(LeaderIdGallery, "_render", side_effect=lambda: events.append("render")))
            LeaderIdGallery(app)
        self.assertEqual(events, ["reload", ("initialize", True), "render"])

    def test_failed_recalibrate_never_announces_success(self):
        from mhxy.gui.leader_gallery import LeaderIdGallery
        gallery = SimpleNamespace(app=SimpleNamespace(cfg={}), _toast=mock.Mock(),
                                  _after_change=mock.Mock(), withdraw=mock.Mock(),
                                  deiconify=mock.Mock(), lift=mock.Mock(), focus_force=mock.Mock())
        with mock.patch("mhxy.gui.calibrate_dialog.calibrate_template_direct", return_value=False), \
                mock.patch.object(lh, "push_after_calibrate") as push, \
                mock.patch.object(lh, "initialize") as initialize:
            LeaderIdGallery._recalibrate(gallery)
        initialize.assert_not_called()
        push.assert_not_called()
        gallery._after_change.assert_not_called()
        gallery.deiconify.assert_called_once()

    def test_roi_profile_can_be_deferred_without_changing_default(self):
        from mhxy.gui import calibrate_dialog as dialog
        window = SimpleNamespace(rect=lambda: (0, 0, 800, 600))
        app = SimpleNamespace(attributes=mock.Mock(), update_idletasks=mock.Mock(),
                              lift=mock.Mock(), focus_force=mock.Mock())
        for deferred in (True, False):
            with self.subTest(deferred=deferred):
                cfg = copy.deepcopy(cfg_mod.DEFAULT_CONFIG)
                with mock.patch.object(dialog.win_mod, "locate_all", return_value=[window]), \
                        mock.patch.object(dialog.win_mod, "window_at_point", return_value=window), \
                        mock.patch.object(dialog, "select_roi_on_screen", return_value=([10, 10, 2, 2], b"crop")), \
                        mock.patch.object(dialog.time, "sleep"), \
                        mock.patch.object(cfg_mod, "save_config") as save:
                    result = dialog.grab_roi_on_app(app, cfg, "test", with_crop=True,
                                                    save_profile=not deferred)
                self.assertEqual(result, ([10, 10, 2, 2], b"crop", 1))
                self.assertEqual(cfg["targets"]["base_size"], [800, 600])
                self.assertEqual(save.call_count, 0 if deferred else 1)

    def test_successful_recalibrate_does_not_push_or_reload_again(self):
        from mhxy.gui.leader_gallery import LeaderIdGallery
        gallery = SimpleNamespace(app=SimpleNamespace(cfg={}), _toast=mock.Mock(),
                                  _after_change=mock.Mock(), withdraw=mock.Mock(),
                                  deiconify=mock.Mock(), lift=mock.Mock(), focus_force=mock.Mock())
        with mock.patch("mhxy.gui.calibrate_dialog.calibrate_template_direct", return_value=True), \
                mock.patch.object(cfg_mod, "load_config") as reload, \
                mock.patch.object(lh, "push_after_calibrate") as push:
            LeaderIdGallery._recalibrate(gallery)
        reload.assert_not_called()
        push.assert_not_called()
        gallery._after_change.assert_called_once()

    def test_failed_commands_never_announce_success(self):
        from mhxy.gui.leader_gallery import LeaderIdGallery
        gallery = SimpleNamespace(app=SimpleNamespace(cfg={}), _toast=mock.Mock(),
                                  _after_change=mock.Mock())
        for method, command in (("_activate", "activate"), ("_delete", "delete")):
            with self.subTest(command=command), mock.patch.object(lh, command, side_effect=lh.LeaderHistoryError("failed")):
                getattr(LeaderIdGallery, method)(gallery, 0)
                gallery._after_change.assert_not_called()
                self.assertIn("失败", gallery._toast.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
