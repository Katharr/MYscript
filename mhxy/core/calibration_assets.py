# -*- coding: utf-8 -*-
"""普通标定单图与完整配置的同步提交；不提供跨文件崩溃事务。"""
from pathlib import Path

from . import config as cfg_mod


class CalibrationAssetError(RuntimeError):
    pass


def commit_image(draft, rel_path, image_bytes):
    """先留目标旧字节，写新图后只走 save_config；失败恢复旧图或删除新图。

    调用方只在成功返回后发布草稿。配置恢复依赖 save_config 的原子替换，
    编码由调用方在写图前完成；恢复失败必须明确报告。
    """
    if not isinstance(image_bytes, bytes) or not image_bytes:
        raise CalibrationAssetError("模板编码为空。")
    path = Path(rel_path)
    if not path.is_absolute():
        path = cfg_mod.PROJECT_ROOT / path
    try:
        previous = path.read_bytes() if path.exists() else None
    except Exception as exc:
        raise CalibrationAssetError("读取旧模板失败，本次未提交。") from exc
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(image_bytes)
        cfg_mod.save_config(draft)
    except Exception as exc:
        try:
            if previous is None:
                path.unlink(missing_ok=True)
            elif not path.exists() or path.read_bytes() != previous:
                path.write_bytes(previous)
        except Exception as restore_exc:
            raise CalibrationAssetError("标定提交失败，旧模板恢复失败，请检查模板资产。") from restore_exc
        raise CalibrationAssetError("标定提交失败，已保留原配置与模板。") from exc
