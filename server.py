"""本机行情服务（仅绑定 127.0.0.1）。

免登录核心：ID-Mapper 提供全目录与 BUFF goods_id；sell_order 免登录给实时价/在售量/源时间戳。
Steam 参考价（比例分母）走官方 priceoverview，被墙时可选海外 HTTP 代理；无代理则比例如实缺省。
安全（方案 §6）：只绑回环、无凭据、日志只打方法/路径/状态码。失败返回 {ok,reason} 结构化结果。
"""
from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

import agg
import buff
import catalog
import config
import idmap
import steam
import steam_fee
import store

_WEB_DIR = os.path.join(os.path.dirname(__file__), "web")
_STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
}


def _hydrate_buff(goods_id: int) -> dict:
    cfg = config.get_config()
    db = store.get_store()
    now = time.time()
    cached = db.get_buff_price(goods_id, now - cfg.buff_ttl_sec)
    if cached:
        return cached
    res = buff.fetch_sell_order(goods_id)
    if not res.get("ok"):
        return {"_error": res["reason"]}
    d = res["data"]
    db.put_buff_price(goods_id, d, now)
    return d


def _hydrate_steam(hash_name: str) -> dict | None:
    cfg = config.get_config()
    if not cfg.steam_proxy:
        return None  # 无代理：多半被墙，不反复徒劳请求
    db = store.get_store()
    now = time.time()
    cached = db.get_steam_price(hash_name, now - cfg.steam_ttl_sec)
    if cached:
        return cached
    res = steam.fetch_priceoverview(hash_name)
    if not res.get("ok"):
        return {"_error": res["reason"]}
    db.put_steam_price(hash_name, res, now)
    return res


def _steam_listing_url(hash_name: str) -> str:
    cfg = config.get_config()
    return f"{cfg.steam_base}/market/listings/730/{quote(hash_name)}"


def _resolve_steam(hash_name: str, buff_cny: float | None) -> dict:
    """给出 Steam 参考价(CNY)与口径标记。优先国服(代理),否则聚合全球估算,再否则缺省。

    返回 {steam_ref_cny, steam_mode}，mode: 'proxy'|'global'|'none'。
    """
    s = _hydrate_steam(hash_name)
    if s is not None and not s.get("_error"):
        ref = s.get("lowest_sell") or s.get("median")
        if ref is not None:
            return {"steam_ref_cny": ref, "steam_mode": "proxy"}

    # 国服不可得：用聚合快照(美元)按"同件实时CNY/美元"推隐含汇率折成 CNY，比例与成本同币种。
    pair = agg.lookup_usd(hash_name)
    if pair and buff_cny and pair["buff_usd"] > 0:
        fx = buff_cny / pair["buff_usd"]
        return {"steam_ref_cny": round(pair["steam_usd"] * fx, 2), "steam_mode": "global"}

    return {"steam_ref_cny": None, "steam_mode": "none"}


def _build_row(cat: dict, _ignored=None) -> dict:
    row = {
        "goods_id": cat["goods_id"],
        "market_hash_name": cat["market_hash_name"],
        "name_cn": cat["cn_name"],
        "uuyp_id": cat.get("uuyp_id"),
        "buff_url": f"https://buff.163.com/market/s/{cat['goods_id']}",
        "steam_url": _steam_listing_url(cat["market_hash_name"]),
    }
    b = _hydrate_buff(cat["goods_id"])
    if b.get("_error"):
        row.update(buff_min_sell=None, sell_num=None, listing_count=None,
                   source_updated_at=None, buff_status=b["_error"])
    else:
        row.update(
            buff_min_sell=b.get("buff_min_sell"),
            sell_num=b.get("sell_num"),
            listing_count=b.get("listing_count"),
            source_updated_at=b.get("source_updated_at"),
            buff_status="ok",
        )
    st = _resolve_steam(cat["market_hash_name"], row.get("buff_min_sell"))
    row["steam_ref_cny"] = st["steam_ref_cny"]
    row["steam_mode"] = st["steam_mode"]
    if st["steam_mode"] == "global":
        row["steam_ref_time"] = agg.snapshot_updated_at()
    steam_fee.decorate(row, None)
    return row


def _search_payload(keyword: str, sort: str) -> dict:
    cfg = config.get_config()
    cats = store.get_store().search_catalog(keyword, cfg.search_limit)
    rows = []
    first = True
    for cat in cats:
        if not first:
            time.sleep(0.3)  # 保守限速，避免瞬时打击 BUFF
        first = False
        rows.append(_build_row(cat, None))

    rows.sort(
        key=lambda r: (r["ratio_pct"] is None, -(r["ratio_pct"] or 0))
        if sort == "ratio"
        else (r["buff_min_sell"] is None, r["buff_min_sell"] or 0)
    )
    return {
        "ok": True,
        "keyword": keyword,
        "sort": sort,
        "count": len(rows),
        "steam_available": cfg.steam_proxy is not None or agg.get_status().get("state") == "ready",
        "items": rows,
    }


def _item_payload(goods_id: int) -> dict:
    db = store.get_store()
    rec = db.get_catalog_item(goods_id)
    if not rec:
        return {"ok": False, "reason": "not_in_catalog"}

    # 详情强制回源 BUFF，拿最新挂单与源时间戳。
    db.invalidate_buff_price(goods_id)
    res = buff.fetch_sell_order(goods_id)
    if not res.get("ok"):
        return {"ok": False, "reason": res["reason"]}
    d = res["data"]
    db.put_buff_price(goods_id, d, time.time())

    row = {
        "goods_id": goods_id,
        "market_hash_name": rec["market_hash_name"],
        "name_cn": rec["cn_name"],
        "uuyp_id": rec.get("uuyp_id"),
        "buff_url": f"https://buff.163.com/market/s/{goods_id}",
        "steam_url": _steam_listing_url(rec["market_hash_name"]),
        "buff_min_sell": d["buff_min_sell"],
        "sell_num": d["sell_num"],
        "listing_count": d["listing_count"],
        "source_updated_at": d["source_updated_at"],
        "buff_status": "ok",
    }
    r = _resolve_steam(rec["market_hash_name"], row["buff_min_sell"])
    row.update(steam_ref_cny=r["steam_ref_cny"], steam_mode=r["steam_mode"])
    steam_fee.decorate(row, None)
    return {"ok": True, "item": row}


def _status_payload() -> dict:
    db = store.get_store()
    st = catalog.get_status()
    cfg = config.get_config()
    imported = db.get_meta("catalog_imported_at")
    agg_st = agg.get_status()
    steam_mode = "proxy" if cfg.steam_proxy else ("global" if agg_st.get("state") == "ready" else "none")
    return {
        "ok": True,
        "catalog_state": st["state"],
        "catalog_message": st["message"],
        "catalog_imported_at": float(imported) if imported else None,
        "indexed_items": db.catalog_count(),
        "steam_proxy_configured": cfg.steam_proxy is not None,
        "steam_currency": cfg.steam_currency,
        "steam_mode": steam_mode,
        "agg_state": agg_st.get("state"),
        "agg_updated_at": agg_st.get("updated_at"),
        "server_time": time.time(),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "cstrade/0.2"

    def log_message(self, fmt, *args):
        print(f"[{time.strftime('%H:%M:%S')}] {self.address_string()} {fmt % args}")

    def _send_json(self, obj, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_static(self, path: str) -> None:
        entry = _STATIC.get(path)
        if not entry:
            self._send_json({"ok": False, "reason": "not_found"}, 404)
            return
        filename, ctype = entry
        full = os.path.join(_WEB_DIR, filename)
        if not os.path.isfile(full):
            self._send_json({"ok": False, "reason": "missing_web_asset"}, 500)
            return
        with open(full, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _query(self) -> dict[str, str]:
        return {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}

    def do_GET(self) -> None:  # noqa: N802
        route = urlparse(self.path).path
        q = self._query()

        if route in _STATIC:
            self._send_static(route)
            return
        if route == "/api/status":
            self._send_json(_status_payload())
            return
        if route == "/api/search":
            keyword = q.get("q", "").strip()
            if not keyword:
                self._send_json({"ok": False, "reason": "empty_keyword"}, 400)
                return
            self._send_json(_search_payload(keyword, q.get("sort", "ratio")))
            return
        if route == "/api/item":
            raw = q.get("goods_id", "")
            if not raw.isdigit():
                self._send_json({"ok": False, "reason": "bad_goods_id"}, 400)
                return
            self._send_json(_item_payload(int(raw)))
            return
        if route == "/api/refresh":
            threading.Thread(target=idmap.import_catalog, daemon=True).start()
            self._send_json({"ok": True, "started": True})
            return
        self._send_json({"ok": False, "reason": "not_found"}, 404)


def _agg_loop() -> None:
    agg.ensure_loaded()
    cfg = config.get_config()
    while True:
        time.sleep(cfg.agg_ttl_sec)
        agg.refresh_if_stale(force=True)


def main() -> None:
    cfg = config.get_config()
    threading.Thread(target=catalog.ensure_ready, daemon=True).start()
    threading.Thread(target=_agg_loop, daemon=True).start()
    httpd = ThreadingHTTPServer((cfg.bind_host, cfg.bind_port), Handler)
    print(f"CS2 挂刀行情(本机) 已启动: http://{cfg.bind_host}:{cfg.bind_port}")
    print("免登录核心(ID-Mapper+BUFF sell_order)；Steam 价需可选海外代理。只绑回环，无凭据。")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()


if __name__ == "__main__":
    main()
