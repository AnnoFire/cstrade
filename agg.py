"""零配置的 Steam 价回退源：公开聚合快照（prices.csgotrader.app）。

用途：本机无代理、直连 Steam 被墙时，用该聚合的 steam/buff 快照(美元)算出"全球区估算比例"，
让工具开箱即用。快照每小时更新、无单品源时间戳 → 界面标注"全球估算/约每小时"，不当国服实时价。

取数与币种：
- buff163.json: {hash: {starting_at:{price(USD)}}}  —— BUFF 最低卖(美元)
- steam.json:   {hash: {last_24h/7d...(USD)}}        —— Steam 价(美元)
比例在同一美元口径下计算(FX 约掉)，并用同件 BUFF 的"实时CNY/美元"推出该件隐含汇率，
把 Steam 价折算成 CNY 展示，与实时 CNY 成本同币种、口径一致。
"""
from __future__ import annotations

import gzip
import json
import threading
import time
import urllib.error
import urllib.request
from typing import Any

import config

KINDS = {"buff": "buff163.json", "steam": "steam.json"}

_lock = threading.Lock()
_cache: dict[str, dict[str, Any]] = {"buff": {}, "steam": {}}
_fetched_at: dict[str, float] = {"buff": 0.0, "steam": 0.0}
_status: dict[str, Any] = {"state": "idle", "message": "", "updated_at": None}


def get_status() -> dict:
    return dict(_status)


def _path(kind: str) -> str:
    import os

    return os.path.join(config.get_config().agg_dir, KINDS[kind])


def _download(kind: str) -> dict | None:
    import os

    cfg = config.get_config()
    url = f"{cfg.agg_base}/{KINDS[kind]}"
    req = urllib.request.Request(url, headers={"User-Agent": cfg.user_agent})
    try:
        with urllib.request.urlopen(req, timeout=cfg.request_timeout_sec * 4) as resp:
            raw = resp.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        _status.update(state="error", message=f"聚合源下载失败:{type(exc).__name__}")
        return None
    if raw[:2] == b"\x1f\x8b":  # gzip
        raw = gzip.decompress(raw)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        _status.update(state="error", message="聚合源 JSON 解析失败")
        return None
    os.makedirs(cfg.agg_dir, exist_ok=True)
    with open(_path(kind), "wb") as f:
        f.write(raw)
    return data


def _load_local(kind: str) -> dict | None:
    import os

    p = _path(kind)
    if not os.path.isfile(p):
        return None
    with open(p, "rb") as f:
        raw = f.read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def refresh_if_stale(force: bool = False) -> dict:
    """按 TTL 刷新两份快照到内存。缓存文件可离线复用。"""
    cfg = config.get_config()
    now = time.time()
    with _lock:
        for kind in KINDS:
            if not force and _cache[kind] and (now - _fetched_at[kind]) < cfg.agg_ttl_sec:
                continue
            data = _download(kind) if (force or True) else None
            if data is None:
                data = _load_local(kind)  # 下载失败退回本地旧快照
            if data is not None:
                _cache[kind] = data
                _fetched_at[kind] = now
        ok = bool(_cache["steam"]) and bool(_cache["buff"])
        _status.update(
            state="ready" if ok else "error",
            message="聚合快照就绪(全球区/约每小时)" if ok else "聚合快照不可用",
            updated_at=_fetched_at.get("steam"),
        )
        return get_status()


def ensure_loaded() -> None:
    if not _cache["steam"] or not _cache["buff"]:
        with _lock:
            for kind in KINDS:
                if not _cache[kind]:
                    local = _load_local(kind)
                    if local is not None:
                        _cache[kind] = local
                        _fetched_at[kind] = 0.0  # 标记为旧,允许后台刷新
        refresh_if_stale(force=False)


def lookup_usd(market_hash_name: str) -> dict | None:
    """返回 {steam_usd, buff_usd}，任一缺失即 None。"""
    st = _cache["steam"].get(market_hash_name)
    bu = _cache["buff"].get(market_hash_name)
    steam_usd = None
    if isinstance(st, dict):
        steam_usd = st.get("last_24h") or st.get("last_7d")
    buff_usd = None
    if isinstance(bu, dict):
        buff_usd = (bu.get("starting_at") or {}).get("price")
    if steam_usd is None or buff_usd is None:
        return None
    return {"steam_usd": float(steam_usd), "buff_usd": float(buff_usd)}


def snapshot_updated_at() -> float | None:
    return _fetched_at.get("steam")
