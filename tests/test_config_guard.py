# -*- coding: utf-8 -*-
"""写盘护栏测试：任何「会抹掉标定」的写盘都必须被挡住，且测试永远不会碰真实配置。"""
import copy
import json
import os
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

from mhxy.core import config as cfg_mod
from mhxy.core.config import DEFAULT_CONFIG, ConfigWriteRejected


def _calibrated_cfg():
    """一份「标好了」的完整配置：顶层键齐 + 标定规模接近真实（十几项）。"""
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    escort = cfg["tasks"]["escort"]
    escort["regions"]["activity_list"] = [10, 20, 30, 40]
    for key in ("escort_entry", "escort_join", "escort_silver", "escort_confirm"):
        escort["templates"][key] = "templates/tm_%s.png" % key
    sniper = cfg["tasks"]["sniper"]
    for key in ("listing", "category_button", "product_entry", "buy_button", "confirm_button"):
        sniper["regions"][key] = [1, 2, 3, 4]
    sniper["watchlist"] = [{"name": "装备", "template": "templates/x.png"}]
    teaming = cfg["tasks"]["teaming"]
    for key in ("leader_id", "team_create", "team_apply"):
        teaming["templates"][key] = "templates/tm_%s.png" % key
    cfg["account_launch"]["profiles"] = [{"id": "a", "label": "甲"}, {"id": "b", "label": "乙"}]
    return cfg


class ConfigGuardTests(unittest.TestCase):
    def setUp(self):
        # 自己建唯一目录而不是 tempfile.mkdtemp()：mkdtemp 在 Windows 上给的是 0700 私有
        # 目录，某些受管环境下连自己都写不进去（本机沙箱就是），自建目录两种环境都能跑。
        self.dir = Path(tempfile.gettempdir()) / (
            "mhxy_cfg_guard_%d_%s" % (os.getpid(), uuid.uuid4().hex[:8]))
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "config.json"
        self.backups = self.dir / "config_backups"
        self._patches = [
            mock.patch.object(cfg_mod, "CONFIG_PATH", self.path),
            mock.patch.object(cfg_mod, "CONFIG_BACKUP_DIR", self.backups),
        ]
        for patch in self._patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def _on_disk(self):
        with open(self.path, encoding="utf-8") as handle:
            return json.load(handle)

    def test_rejects_partial_config(self):
        """本次事故的直接成因：把一个只剩几个键的残缺 dict 写进配置。"""
        cfg_mod.save_config(_calibrated_cfg())
        before = self._on_disk()
        with self.assertRaises(ConfigWriteRejected):
            cfg_mod.save_config({"account_launch": {"profiles": []}, "game_dir": "C:/x"})
        self.assertEqual(self._on_disk(), before)                     # 磁盘一个字节都没动
        rejected = list(self.backups.glob("REJECTED-*.json"))
        self.assertEqual(len(rejected), 1)                            # 待写内容留了档
        with open(rejected[0], encoding="utf-8") as handle:
            self.assertIn("account_launch", json.load(handle))

    def test_rejects_calibration_wipe(self):
        """顶层键齐但标定被抹成默认值（load 补默认值后再写盘）也要挡住。"""
        cfg_mod.save_config(_calibrated_cfg())
        before = self._on_disk()
        wiped = copy.deepcopy(DEFAULT_CONFIG)      # 结构完整、标定全空 = 补默认值之后的产物
        with self.assertRaises(ConfigWriteRejected):
            cfg_mod.save_config(wiped)
        self.assertEqual(self._on_disk(), before)

    def test_normal_edit_passes_and_snapshots(self):
        """正常改配置照常通过，并留下世代快照。"""
        cfg_mod.save_config(_calibrated_cfg())
        self.assertEqual(list(self.backups.glob("config-*.json")), [])
        cfg = _calibrated_cfg()
        cfg["tasks"]["escort"]["regions"]["activity_list"] = [11, 22, 33, 44]
        cfg_mod.save_config(cfg)
        snaps = list(self.backups.glob("config-*.json"))
        self.assertEqual(len(snaps), 1)                       # 写新版前，旧版被留了一份
        self.assertTrue(self.path.with_name("config.json.bak").exists())

    def test_snapshot_skips_identical_content(self):
        """磁盘内容没变就不重复攒快照（否则每次开机都多一份一模一样的）。"""
        cfg_mod.save_config(_calibrated_cfg())
        self.assertIsNotNone(cfg_mod.snapshot_config())
        count = len(list(self.backups.glob("config-*.json")))
        self.assertIsNone(cfg_mod.snapshot_config())
        self.assertEqual(len(list(self.backups.glob("config-*.json"))), count)

    def test_rejects_test_process_writing_real_path(self):
        """换任何跑法（含 discover -s tests）测试都不许写真实配置。"""
        with mock.patch.object(cfg_mod, "_REAL_CONFIG_PATH", self.path), \
             mock.patch.object(cfg_mod, "_is_test_process", return_value=True):
            with self.assertRaises(ConfigWriteRejected):
                cfg_mod.save_config(_calibrated_cfg())
        self.assertFalse(self.path.exists())

    def test_force_allows_reset(self):
        """确实要重置配置时，force=True 能放行。"""
        cfg_mod.save_config(_calibrated_cfg())
        cfg_mod.save_config(copy.deepcopy(DEFAULT_CONFIG), force=True)
        self.assertEqual(self._calib_of(self._on_disk()), 0)
        self.assertGreaterEqual(self._calib_of(_calibrated_cfg()), 10)

    @staticmethod
    def _calib_of(cfg):
        return cfg_mod._calib_count(cfg)

    def test_small_calibration_edits_are_not_blocked(self):
        """标定项本来就不多时，删两三样是正常改动，不能被缩水规则误伤。"""
        small = copy.deepcopy(DEFAULT_CONFIG)
        small["tasks"]["escort"]["regions"]["activity_list"] = [1, 2, 3, 4]
        for key in ("escort_entry", "escort_join", "escort_silver"):
            small["tasks"]["escort"]["templates"][key] = "templates/tm_%s.png" % key
        cfg_mod.save_config(small)
        trimmed = copy.deepcopy(small)
        trimmed["tasks"]["escort"]["regions"]["activity_list"] = None
        trimmed["tasks"]["escort"]["templates"]["escort_entry"] = None
        cfg_mod.save_config(trimmed)                       # 4 项 -> 2 项，应放行
        self.assertEqual(self._calib_of(self._on_disk()), 2)

    def test_snapshot_prunes_old_files(self):
        with mock.patch.object(cfg_mod, "BACKUP_KEEP", 2):
            for index in range(4):
                cfg = _calibrated_cfg()
                cfg["tasks"]["escort"]["regions"]["activity_list"] = [index, 0, 0, 0]
                cfg_mod.save_config(cfg)
        self.assertLessEqual(len(list(self.backups.glob("config-*.json"))), 2)


if __name__ == "__main__":
    unittest.main()
