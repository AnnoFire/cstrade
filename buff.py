"""BUFF 只读行情适配器（免登录核心）。

只用一个路由：/api/market/goods/sell_order —— 实测免登录返回实时挂单，
足以给出：最低卖价(实价 CNY)、在售挂单数、最新挂单 updated_at(源时间戳)。

饰品目录/检索不再走 BUFF 列表（那需 cookie），改由 ID-Mapper 公开数据集提供（见 idmap.py）。

安全（方案 §6）：无任何凭据、只读、日志绝不输出请求头/Cookie。
失败语义：返回 {ok: bool, reason}，上层只信 ok，不抛给核心逻辑。
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import config


class BuffResult(dict):
    """{ok: bool, data?: ..., reason?: str}。"""


def _http_get_json(url: str) -> BuffResult:
    cfg = config.get_config()
    req = urllib.request.Request(
        url, headers={"User-Agent": cfg.user_agent, "Accept": "application/json"}
    )
    last_reason = "unknown"
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=cfg.request_timeout_sec) as resp:
                status = resp.getcode()
                body = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            status = exc.code
            body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_reason = f"network:{type(exc).__name__}"
            time.sleep(cfg.backoff_base_sec * (attempt + 1))
            continue

        if status == 429:
            last_reason = "rate_limited"
            time.sleep(cfg.backoff_base_sec * (attempt + 1))
            continue
        if status >= 500:
            last_reason = f"http_{status}"
            time.sleep(cfg.backoff_base_sec * (attempt + 1))
            continue
        if status != 200:
            return BuffResult(ok=False, reason=f"http_{status}")

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return BuffResult(ok=False, reason="bad_json")

        if payload.get("code") == "OK":
            return BuffResult(ok=True, data=payload.get("data") or {})
        return BuffResult(ok=False, reason=f"biz:{payload.get('code')}")

    return BuffResult(ok=False, reason=last_reason)


def fetch_sell_order(goods_id: int) -> BuffResult:
    """免登录取单件实时挂单：最低卖价(CNY)、在售挂单数、最新挂单源时间戳。"""
    cfg = config.get_config()
    params = urllib.parse.urlencode({"game": "csgo", "goods_id": goods_id, "page": 1})
    url = f"{cfg.buff_base}/api/market/goods/sell_order?{params}"
    res = _http_get_json(url)
    if not res.get("ok"):
        return res
    data = res["data"]
    listings = data.get("items") or []

    prices: list[float] = []
    latest_ts: int | None = None
    for raw in listings:
        p = raw.get("price")
        try:
            prices.append(float(p))
        except (TypeError, ValueError):
            continue
        ts = raw.get("updated_at") or raw.get("created_at")
        try:
            ts_i = int(ts)
        except (TypeError, ValueError):
            ts_i = None
        if ts_i and (latest_ts is None or ts_i > latest_ts):
            latest_ts = ts_i

    # 在售量优先用上游 total_count（总卖单数），缺失则退化为本页条数。
    sell_num = None
    try:
        sell_num = int(data.get("total_count"))
    except (TypeError, ValueError):
        sell_num = len(listings) if listings else None

    return BuffResult(
        ok=True,
        data={
            "buff_min_sell": min(prices) if prices else None,
            "sell_num": sell_num,
            "listing_count": len(listings),
            # 源站报价更新时间；缺失即 None，界面显示"源站未提供时间"，不拿拉取时间冒充。
            "source_updated_at": latest_ts,
        },
    )
