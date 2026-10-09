"""目录引导：确保 ID-Mapper 已导入本地 SQLite。

映射数据集变动很少，故只在为空时导入一次；"刷新目录"按钮触发重新导入。
实时价（BUFF/Steam）是按需拉取+缓存，不在此模块。
"""
from __future__ import annotations

import idmap


def get_status() -> dict:
    return idmap.get_status()


def ensure_ready() -> dict:
    import store

    if store.get_store().catalog_count() == 0:
        return idmap.import_catalog()
    return {"ok": True, "count": store.get_store().catalog_count(), "reason": "already"}


def refresh() -> dict:
    return idmap.import_catalog()
