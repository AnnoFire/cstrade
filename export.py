"""静态导出 worker（GitHub Actions 定时跑这个，产出到 site/，Pages 只读）。

产出物：
- site/data.json —— 全量快照：ID-Mapper 目录 + 聚合(A档估算比例)，关注列表(hotlist)覆盖为实时(B档精确)。
- site/index.html / app.js / styles.css —— 从 web/ 复制一份纯静态前端（改读 data.json）。

设计（对应方案：B 精确给关注列表、A 估算兜全量）：
- A 全量：用聚合快照(美元)按 calculate_after_fee 算比例，FX 仅用于把¥绝对值显示出来。
- B 精确：hotlist 里的件现抓 BUFF sell_order(实时CNY价/在售/挂单时间戳) 与 Steam priceoverview(国服CNY)；
  Steam 在境外 runner 可直连，本机被墙则该行回落为"BUFF实时 + 全球估Steam"。
- 无凭据：只用 ID-Mapper 公开数据集 + 免登录 sell_order + 可选 Steam 直连；不需要 BUFF cookie。
"""
from __future__ import annotations

import json
import os
import shutil
import time

import agg
import buff
import config
import steam
import steam_fee

_ROOT = os.path.dirname(os.path.abspath(__file__))


def _load_json_local(rel: str):
    p = os.path.join(_ROOT, rel)
    if not os.path.isfile(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _ensure_datasets():
    """确保 ID-Mapper 与聚合快照在本地存在(缺则从公开源下载)，供干净 CI 环境使用。
    返回 (buff_id_map, steam_meta_map, agg_steam, agg_buff)。"""
    import idmap

    agg.ensure_loaded()
    # ID-Mapper：export 需要 buff(goods_id 映射) 与 steam(中文名)；缺文件则下载。
    for kind in ("buff", "steam"):
        if idmap._ensure_file(kind) is None:
            raise SystemExit(f"ID-Mapper 下载失败({kind})：无法生成快照")
    buff_id_map = _load_json_local(os.path.join(config.get_config().idmap_dir, "buff.730.json"))
    steam_meta = _load_json_local(os.path.join(config.get_config().idmap_dir, "steam.730.json"))
    if buff_id_map is None or steam_meta is None:
        raise SystemExit("ID-Mapper 文件缺失/损坏")
    return buff_id_map, steam_meta, agg._cache["steam"], agg._cache["buff"]


def _read_hotlist() -> list[str]:
    p = os.path.join(_ROOT, config.get_config().hotlist_file)
    if not os.path.isfile(p):
        return []
    with open(p, encoding="utf-8") as f:
        return [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]


def _row(hash_name, cn, goods_id, agg_steam_map, agg_buff_map, fx, mode="auto",
         buff_min=None, steam_cny=None, sell_num=None, listing=None, src_ts=None):
    # 缺失侧用聚合快照(美元→CNY)兜底，兜底即标 global。
    g = agg_steam_map.get(hash_name)
    b = agg_buff_map.get(hash_name)
    s_usd = (g.get("last_24h") or g.get("last_7d")) if isinstance(g, dict) else None
    b_usd = (b.get("starting_at") or {}).get("price") if isinstance(b, dict) else None
    if buff_min is None:
        if not b_usd:
            return None
        buff_min, mode = round(b_usd * fx, 2), "global"
    if steam_cny is None:
        if not s_usd:
            return None
        steam_cny, mode = round(s_usd * fx, 2), "global"
    net = steam_fee.calculate_after_fee(steam_cny)
    ratio = steam_fee.balance_ratio(net, buff_min)
    return {
        # 与 server._build_row 同键，便于前端双模式复用
        "goods_id": goods_id,
        "market_hash_name": hash_name,
        "name_cn": cn,
        "buff_min_sell": buff_min,
        "sell_num": sell_num,
        "listing_count": listing,
        "source_updated_at": src_ts,
        "steam_ref_cny": steam_cny,
        "steam_net_est": net,
        "ratio_pct": ratio,
        "steam_mode": mode,  # exact | global
    }


def build() -> int:
    cfg = config.get_config()
    fx = cfg.fx_usd_cny
    buff_id_map, steam_meta, agg_steam, agg_buff = _ensure_datasets()
    hot = _read_hotlist()
    hotset = set(hot)

    out_dir = os.path.join(_ROOT, cfg.export_dir)
    os.makedirs(out_dir, exist_ok=True)

    # 先处理 hotlist：实时精确(或 BUFF实时+全球估回落)
    by_hash = {}
    for hn in hot:
        gid = buff_id_map.get(hn)
        if not isinstance(gid, int) or gid <= 0:
            continue
        cn = (steam_meta.get(hn) or {}).get("cn_name")
        so = buff.fetch_sell_order(gid)
        if not so.get("ok") or not so["data"]["buff_min_sell"]:
            continue  # 实时取不到就留给全量兜底
        d = so["data"]
        st = steam.fetch_priceoverview(hn)  # 境外 runner 可得国服价
        if st.get("ok") and st.get("lowest_sell") is not None:
            row = _row(hn, cn, gid, agg_steam, agg_buff, fx, "exact",
                       buff_min=d["buff_min_sell"], sell_num=d["sell_num"],
                       listing=d["listing_count"], src_ts=d["source_updated_at"],
                       steam_cny=st["lowest_sell"])
        else:
            row = _row(hn, cn, gid, agg_steam, agg_buff, fx, "global",
                       buff_min=d["buff_min_sell"], sell_num=d["sell_num"],
                       listing=d["listing_count"], src_ts=d["source_updated_at"])
        if row:
            by_hash[hn] = row

    # 再处理全量(跳过已在 hotlist 精确档的)
    count = len(by_hash)
    for hn, gid in buff_id_map.items():
        if not isinstance(gid, int) or gid <= 0 or hn in by_hash:
            continue
        g = agg_steam.get(hn)
        b = agg_buff.get(hn)
        if not (g and b):
            continue
        cn = (steam_meta.get(hn) or {}).get("cn_name")
        row = _row(hn, cn, gid, agg_steam, agg_buff, fx, "global")
        if row:
            by_hash[hn] = row
            count += 1

    data = {
        "generated_at": time.time(),
        "fx_usd_cny": fx,
        "steam_mode": "proxy_or_runner" if any(r["steam_mode"] == "exact" for r in by_hash.values()) else "global",
        "hotlist_size": len(hot),
        "count": len(by_hash),
        "items": list(by_hash.values()),
    }
    with open(os.path.join(out_dir, "data.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))

    # 复制纯静态前端
    web = os.path.join(_ROOT, "web")
    for fn in ("index.html", "app.js", "styles.css"):
        src = os.path.join(web, fn)
        if os.path.isfile(src):
            shutil.copy(src, os.path.join(out_dir, fn))
    # GitHub Pages 需要；自定义域名则放 CNAME
    open(os.path.join(out_dir, ".nojekyll"), "w").close()
    domain = os.environ.get("SITE_DOMAIN", "").strip()
    if domain:
        with open(os.path.join(out_dir, "CNAME"), "w") as f:
            f.write(domain + "\n")
    return count


if __name__ == "__main__":
    n = build()
    print(f"[export] 生成 {config.get_config().export_dir}/data.json，共 {n} 条")
