"""本机 SQLite：ID-Mapper 目录索引 + BUFF/Steam 实时价短缓存。

只存公开行情与映射，无任何凭据（方案 §6）。搜索在本地按 market_hash_name / 中文名匹配；
实时价按需拉取并带 TTL 缓存，自动刷新时到点才回源，避免每次轮询重复打击上游。
"""
from __future__ import annotations

import os
import sqlite3
from typing import Any

import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS catalog (
    goods_id          INTEGER PRIMARY KEY,
    market_hash_name  TEXT NOT NULL,
    cn_name           TEXT,
    steam_name_id     INTEGER,
    uuyp_id           INTEGER
);
CREATE INDEX IF NOT EXISTS idx_cat_hash ON catalog(market_hash_name);
CREATE INDEX IF NOT EXISTS idx_cat_cn   ON catalog(cn_name);

CREATE TABLE IF NOT EXISTS buff_price (
    goods_id          INTEGER PRIMARY KEY,
    buff_min_sell     REAL,
    sell_num          INTEGER,
    listing_count     INTEGER,
    source_updated_at INTEGER,
    fetched_at        REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS steam_price (
    market_hash_name  TEXT PRIMARY KEY,
    lowest_sell       REAL,
    median            REAL,
    currency          INTEGER,
    fetched_at        REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


class Store:
    def __init__(self, path: str) -> None:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ---- catalog（来自 ID-Mapper） ----
    def replace_catalog(self, rows: list[tuple]) -> int:
        cur = self._conn.cursor()
        cur.execute("DELETE FROM catalog")
        cur.executemany(
            "INSERT INTO catalog(goods_id, market_hash_name, cn_name, steam_name_id, uuyp_id) "
            "VALUES (?,?,?,?,?)",
            rows,
        )
        self._conn.commit()
        return cur.rowcount if cur.rowcount and cur.rowcount > 0 else len(rows)

    def catalog_count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) c FROM catalog").fetchone()["c"]

    def get_catalog_item(self, goods_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM catalog WHERE goods_id=?", (goods_id,)
        ).fetchone()
        return dict(row) if row else None

    def invalidate_buff_price(self, goods_id: int) -> None:
        self._conn.execute("DELETE FROM buff_price WHERE goods_id=?", (goods_id,))
        self._conn.commit()

    def search_catalog(self, keyword: str, limit: int) -> list[dict[str, Any]]:
        kw = keyword.strip()
        if not kw:
            return []
        like = f"%{kw}%"
        rows = self._conn.execute(
            "SELECT * FROM catalog WHERE market_hash_name LIKE ? OR cn_name LIKE ? LIMIT ?",
            (like, like, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- BUFF 实时价缓存 ----
    def get_buff_price(self, goods_id: int, ttl: float) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM buff_price WHERE goods_id=?", (goods_id,)
        ).fetchone()
        if not row:
            return None
        if row["fetched_at"] < ttl:
            return None  # 过期，需回源
        return dict(row)

    def put_buff_price(self, goods_id: int, d: dict, now: float) -> None:
        self._conn.execute(
            "INSERT INTO buff_price(goods_id,buff_min_sell,sell_num,listing_count,source_updated_at,fetched_at)"
            " VALUES(?,?,?,?,?,?)"
            " ON CONFLICT(goods_id) DO UPDATE SET buff_min_sell=excluded.buff_min_sell,"
            " sell_num=excluded.sell_num, listing_count=excluded.listing_count,"
            " source_updated_at=excluded.source_updated_at, fetched_at=excluded.fetched_at",
            (
                goods_id,
                d.get("buff_min_sell"),
                d.get("sell_num"),
                d.get("listing_count"),
                d.get("source_updated_at"),
                now,
            ),
        )
        self._conn.commit()

    # ---- Steam 实时价缓存 ----
    def get_steam_price(self, hash_name: str, ttl: float) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM steam_price WHERE market_hash_name=?", (hash_name,)
        ).fetchone()
        if not row:
            return None
        if row["fetched_at"] < ttl:
            return None
        return dict(row)

    def put_steam_price(self, hash_name: str, d: dict, now: float) -> None:
        self._conn.execute(
            "INSERT INTO steam_price(market_hash_name,lowest_sell,median,currency,fetched_at)"
            " VALUES(?,?,?,?,?) ON CONFLICT(market_hash_name) DO UPDATE SET"
            " lowest_sell=excluded.lowest_sell, median=excluded.median,"
            " currency=excluded.currency, fetched_at=excluded.fetched_at",
            (hash_name, d.get("lowest_sell"), d.get("median"), d.get("currency"), now),
        )
        self._conn.commit()

    # ---- meta ----
    def set_meta(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self._conn.commit()

    def get_meta(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None


_store: Store | None = None


def get_store() -> Store:
    global _store
    if _store is None:
        _store = Store(config.get_config().db_path)
    return _store
