"""集中解析本机配置。启动时校验，必填项缺失或非法直接 fail fast。

安全约束（方案 §6）：
- 服务地址固定回环 127.0.0.1，不提供环境变量改写。
- 核心取数（ID-Mapper 目录 + BUFF sell_order 实时价）完全免登录，无任何凭据字段。
- 可选的海外 HTTP 代理仅用于访问 Steam（Steam 在中国大陆被墙），不涉及账号。
"""
from __future__ import annotations

import os


class ConfigError(Exception):
    """启动期配置错误。fail fast，不带着坏配置继续运行。"""


def _get_str(name: str, default: str | None = None, required: bool = False) -> str:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        if required:
            raise ConfigError(f"必填配置 {name} 缺失")
        if default is None:
            raise ConfigError(f"配置 {name} 缺失且无默认值")
        return default
    return raw.strip()


def _get_opt_str(name: str) -> str | None:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return None
    return raw.strip()


def _get_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw.strip())
    except ValueError as exc:
        raise ConfigError(f"配置 {name} 必须是整数，当前为 {raw!r}") from exc
    if not (minimum <= value <= maximum):
        raise ConfigError(f"配置 {name}={value} 超出允许区间 [{minimum}, {maximum}]")
    return value


def _get_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw.strip())
    except ValueError as exc:
        raise ConfigError(f"配置 {name} 必须是数字，当前为 {raw!r}") from exc
    if not (minimum <= value <= maximum):
        raise ConfigError(f"配置 {name}={value} 超出允许区间 [{minimum}, {maximum}]")
    return value


def _no_dotdot(name: str, value: str) -> str:
    if ".." in value:
        raise ConfigError(f"{name} 路径不得包含 ..")
    return value


class Config:
    # 安全红线：只绑定回环，不提供环境变量覆盖。
    bind_host: str = "127.0.0.1"

    def __init__(self) -> None:
        self.bind_port = _get_int("CSTRADE_PORT", default=8756, minimum=1, maximum=65535)

        # BUFF 只读行情基址（仅访问 sell_order，免登录）。
        self.buff_base = _get_str("BUFF_BASE", default="https://buff.163.com").rstrip("/")
        self.steam_base = _get_str("STEAM_BASE", default="https://steamcommunity.com").rstrip("/")

        # ID-Mapper（原站发布的公开映射数据集，MIT）本地缓存目录与来源。
        self.idmap_dir = _no_dotdot(
            "CSTRADE_IDMAP_DIR", _get_str("CSTRADE_IDMAP_DIR", default="data/idmap")
        )
        self.idmap_base = _get_str(
            "IDMAP_BASE",
            default="https://raw.githubusercontent.com/EricZhu-42/SteamTradingSite-ID-Mapper/main",
        )

        # 零配置 Steam 价回退源：公开聚合快照(全球区/美元)，无代理时用它算估算比例。
        self.agg_base = _get_str(
            "AGG_BASE", default="https://prices.csgotrader.app/latest"
        ).rstrip("/")
        self.agg_dir = _no_dotdot(
            "AGG_DIR", _get_str("AGG_DIR", default="data/agg")
        )
        self.agg_ttl_sec = _get_int("AGG_TTL_SEC", default=3600, minimum=600, maximum=86400)

        # A 全量估算把聚合美元价折成人民币展示用的近似汇率(比例本身与汇率无关，仅影响¥绝对值显示)。
        self.fx_usd_cny = _get_float("FX_USD_CNY", default=7.20, minimum=1.0, maximum=15.0)

        # 部署导出：静态站目录 + 关注列表(这些件走实时精确抓取)。
        self.export_dir = _no_dotdot(
            "EXPORT_DIR", _get_str("EXPORT_DIR", default="site")
        )
        self.hotlist_file = _no_dotdot(
            "HOTLIST_FILE", _get_str("HOTLIST_FILE", default="hotlist.txt")
        )

        # 单次搜索返回并水合的条数上限（控制对 BUFF/Steam 的访问强度）。
        self.search_limit = _get_int("SEARCH_LIMIT", default=20, minimum=5, maximum=50)

        # 实时价缓存 TTL（秒）：搜索/自动刷新时，缓存在此时间内直接复用，不重复请求上游。
        self.buff_ttl_sec = _get_int("BUFF_PRICE_TTL", default=60, minimum=15, maximum=3600)
        self.steam_ttl_sec = _get_int("STEAM_PRICE_TTL", default=600, minimum=60, maximum=86400)

        # Steam 市场币种：23 = CNY（与 BUFF 人民币价同币种，比例无需换算）。
        self.steam_currency = _get_int("STEAM_CURRENCY", default=23, minimum=1, maximum=40)

        # 可选海外 HTTP 代理（仅用于访问 Steam），格式 host:port。缺省即视为无代理。
        self.steam_proxy = _get_opt_str("CSTRADE_STEAM_PROXY")

        self.request_timeout_sec = _get_int(
            "REQUEST_TIMEOUT_SEC", default=15, minimum=3, maximum=60
        )
        self.backoff_base_sec = _get_int(
            "BACKOFF_BASE_SEC", default=10, minimum=1, maximum=120
        )
        # Steam 官方对匿名高频较敏感，单次搜索内的请求间隔保守一些。
        self.steam_delay_sec = _get_int("STEAM_DELAY_SEC", default=3, minimum=1, maximum=30)

        self.user_agent = _get_str(
            "CSTRADE_UA",
            default="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
        )

        self.db_path = _no_dotdot(
            "CSTRADE_DB", _get_str("CSTRADE_DB", default="data/catalog.sqlite3")
        )


_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config()
    return _config
