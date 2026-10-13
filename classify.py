"""饰品分类：判定某件是否属于"箱子/容器/收藏品"类，用于单独的箱子比例排行。

判定基于 market_hash_name(英文) 与 中文名 的关键词。关键词表集中在此，方便增减。
命中任一即视为 box 类（终端机/武器箱/钥匙/胶囊/音乐盒/纪念品包/印花/贴纸/喷涂/钥匙链…）。
"""
from __future__ import annotations

# 只算"容器/开箱类"：武器箱、钥匙、胶囊、纪念品包、音乐盒、终端机等。
# 不含散落贴纸/印花/喷涂/布章本身(它们不是"箱子")。英文带前导空格避免误伤。
_BOX_TERMS = (
    # 英文
    " case", " capsule", " key", " package", " music kit", "collection",
    # 中文
    "终端", "武器箱", "箱子", "胶囊", "钥匙", "音乐盒", "音乐包", "纪念品包", "收藏包",
)


# 排除：涂装名里含 "Case Hardened"(表面淬火),是皮肤不是箱子。
_NOT_BOX = ("case hardened",)


def is_box(market_hash_name: str | None, cn_name: str | None = None) -> bool:
    hay = ((market_hash_name or "") + " " + (cn_name or "")).lower()
    if any(t in hay for t in _NOT_BOX):
        return False
    return any(t in hay for t in _BOX_TERMS)


def category(market_hash_name: str | None, cn_name: str | None = None) -> str | None:
    return "box" if is_box(market_hash_name, cn_name) else None
