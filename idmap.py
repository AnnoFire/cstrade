"""ID-Mapper：下载并导入原站公开的跨平台 ID 对照数据集，充当本地全目录索引。

来源 EricZhu-42/SteamTradingSite-ID-Mapper（MIT，公开）。三张表都以 market_hash_name 为键：
- buff/730.json  → BUFF goods_id（用于免登录 sell_order 取实时价）
- steam/730.json → {cn_name, name_id}（中文名 + Steam item_nameid）
- uuyp/730.json  → UU 商品 id（本阶段仅存 id，实时价待后续接 Bearer）

这是"免登录全目录检索"的关键：不再需要 BUFF cookie 去枚举列表。
"""
from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from typing import Any

import config
import store

FILES = {
    "buff": "buff/730.json",
    "steam": "steam/730.json",
    "uuyp": "uuyp/730.json",
}

_lock = threading.Lock()
_status: dict[str, Any] = {"state": "idle", "message": "", "imported_at": None}


def get_status() -> dict[str, Any]:
    return dict(_status)


def _local_path(kind: str) -> str:
    cfg = config.get_config()
    return os.path.join(cfg.idmap_dir, f"{kind}.730.json")


def _ensure_file(kind: str) -> str | None:
    """确保映射文件在本机存在；缺失则从公开数据集下载。返回本地路径或 None。"""
    cfg = config.get_config()
    path = _local_path(kind)
    if os.path.isfile(path) and os.path.getsize(path) > 0:
        return path
    os.makedirs(cfg.idmap_dir, exist_ok=True)
    url = f"{cfg.idmap_base}/{FILES[kind]}"
    req = urllib.request.Request(url, headers={"User-Agent": cfg.user_agent})
    tmp = path + ".part"
    last = "unknown"
    for attempt in range(3):  # CI 上偶发抖动重试；下载后校验 JSON 完整性再原子落盘
        try:
            with urllib.request.urlopen(req, timeout=cfg.request_timeout_sec * 4) as resp:
                data = resp.read()
            json.loads(data)
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
            return path
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            last = type(exc).__name__
            if os.path.exists(tmp):
                os.remove(tmp)
            __import__("time").sleep(2 * (attempt + 1))
    _status.update(state="error", message=f"下载 {kind} 映射失败:{last}")
    return None


def import_catalog() -> dict[str, Any]:
    """下载(若缺)+导入 catalog 表。并发安全。返回 {ok, count, reason}。"""
    if not _lock.acquire(blocking=False):
        return {"ok": True, "count": 0, "reason": "already_running"}
    try:
        _status.update(state="downloading", message="获取 ID-Mapper 数据集")
        loaded: dict[str, dict] = {}
        for kind in FILES:
            path = _ensure_file(kind)
            if not path:
                return {"ok": False, "count": 0, "reason": _status["message"]}
            with open(path, encoding="utf-8") as f:
                loaded[kind] = json.load(f)

        _status.update(state="importing", message="导入本地目录索引")
        buff_map = loaded["buff"]
        steam_map = loaded["steam"]
        uuyp_map = loaded["uuyp"]

        rows: list[tuple] = []
        for hash_name, goods_id in buff_map.items():
            if not isinstance(goods_id, int) or goods_id <= 0:
                continue  # BUFF 未收录（-1）则无法取实时价，跳过
            sinfo = steam_map.get(hash_name) or {}
            cn_name = sinfo.get("cn_name") if isinstance(sinfo, dict) else None
            name_id = sinfo.get("name_id") if isinstance(sinfo, dict) else None
            uuyp_id = uuyp_map.get(hash_name)
            if isinstance(uuyp_id, int) and uuyp_id <= 0:
                uuyp_id = None
            rows.append((goods_id, hash_name, cn_name, name_id, uuyp_id))

        db = store.get_store()
        db.replace_catalog(rows)
        imported_at = __import__("time").time()
        db.set_meta("catalog_imported_at", str(imported_at))
        db.set_meta("catalog_count", str(len(rows)))
        _status.update(
            state="ready",
            message=f"目录就绪，共 {len(rows)} 条",
            imported_at=imported_at,
        )
        return {"ok": True, "count": len(rows), "reason": ""}
    finally:
        _lock.release()
