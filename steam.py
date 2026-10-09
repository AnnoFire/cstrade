"""Steam 市场参考价适配器（挂刀比例的分母）。

用官方 priceoverview：currency=23（=CNY），拿最低在售/近期成交中位价，与 BUFF 人民币价同币种。

Steam 在中国大陆被墙，故本机直连通常失败；可选海外 HTTP 代理（CSTRADE_STEAM_PROXY=host:port）。
无代理时如实返回 {ok:False, reason:'steam_unreachable'|'no_proxy'}，上层把比例显示为"待 Steam 价"，
绝不拿别处价或估算值凑数（方案 §3/§6）。只读、无账号、日志不出请求头。
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import config

_NUM = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")


def _parse_money(value: Any) -> float | None:
    """从 '¥ 1,234.56' / '1,234.56' 之类字符串鲁棒提取数值。"""
    if value is None or value is False:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = _NUM.search(str(value))
    if not m:
        return None
    return float(m.group(0).replace(",", ""))


def _opener(proxy: str | None):
    cfg = config.get_config()
    handlers: list[Any] = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    return urllib.request.build_opener(*handlers)


def fetch_priceoverview(market_hash_name: str) -> dict:
    """取单件 Steam 参考价。返回 {ok, lowest_sell, median, reason}。"""
    cfg = config.get_config()
    params = urllib.parse.urlencode(
        {
            "appid": 730,
            "currency": cfg.steam_currency,
            "market_hash_name": market_hash_name,
        }
    )
    url = f"{cfg.steam_base}/market/priceoverview/?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": cfg.user_agent})
    opener = _opener(cfg.steam_proxy)

    last_reason = "unknown"
    for attempt in range(2):
        try:
            with opener.open(req, timeout=cfg.request_timeout_sec) as resp:
                payload = json.loads(resp.read().decode("utf-8", errors="replace"))
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            last_reason = f"steam_unreachable:{type(exc).__name__}"
            if attempt == 0:
                time.sleep(cfg.backoff_base_sec)
            continue

        if not payload.get("success"):
            # 无挂单/低流动性：明确失败语义，不返回 0 价。
            return {"ok": False, "reason": "no_market_data"}

        return {
            "ok": True,
            "lowest_sell": _parse_money(payload.get("lowest_price")),
            "median": _parse_money(payload.get("median_price")),
            "currency": cfg.steam_currency,
        }

    if not cfg.steam_proxy:
        last_reason = "no_proxy"  # 更贴切地提示：未配代理时多为被墙
    return {"ok": False, "reason": last_reason}


def proxy_configured() -> bool:
    return bool(config.get_config().steam_proxy)
