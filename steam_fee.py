"""Steam 余额比例。手续费按 Steam 实际舍入规则精确反解（移植自原站 utils.py）。

原站口径：买家支付价 amount = 卖家到手钱包 w + steam_fee + publisher_fee，其中
    steam_fee    = floor(max(w*0.05, 1))  分
    publisher_fee= floor(max(w*0.10, 1))  分
（单位：分；各费用最低 1 分）。给定挂牌价反解 w，就是卖家真正到手金额。

本项目的"余额比例"沿用方案 §3 定义（净入账 / 最低购买价 ×100%，越高越划算）：
    预计净入账 = calculate_after_fee(Steam 参考价)
    余额比例   = 预计净入账 / 平台最低购买价 × 100

任一价格缺失返回 None，界面显示"暂无报价"，绝不以 0 或另一平台价补齐。
"""
from __future__ import annotations

from math import floor
from typing import Union

STEAM_FEE_RATE = 0.05      # Steam 交易手续费
PUBLISHER_FEE_RATE = 0.10  # 游戏(CS2)发行商手续费


def _fee_helper(received_amount_cents: float) -> dict:
    """给定到手钱包(分)，算出需要买家支付的总额(含两项手续费)。"""
    steam_fee = floor(max(received_amount_cents * STEAM_FEE_RATE, 1))
    publisher_fee = floor(max(received_amount_cents * PUBLISHER_FEE_RATE, 1))
    return {
        "steam_fee": steam_fee,
        "publisher_fee": publisher_fee,
        "fees": steam_fee + publisher_fee,
        "amount": received_amount_cents + steam_fee + publisher_fee,
    }


def calculate_after_fee(amount: Union[int, float, str]) -> float | None:
    """给定买家支付价(CNY 元)，反解卖家实际到手金额(CNY 元)。无法计算返回 None。"""
    if amount is None:
        return None
    if isinstance(amount, str):
        amount = amount.replace(",", "").strip()
        if not amount:
            return None
        amount = round(float(amount))
    cents = float(amount) * 100
    if cents <= 0:
        return None

    # 初估：amount ≈ w * 1.15
    w = floor(cents / (1 + STEAM_FEE_RATE + PUBLISHER_FEE_RATE))
    ever_undershot = False
    fees = _fee_helper(w)
    iteration = 0
    while fees["amount"] != cents and iteration < 10:
        if fees["amount"] > cents:
            if ever_undershot:
                fees = _fee_helper(w - 1)
                fees["fees"] += cents - fees["amount"]
                fees["amount"] = cents
                break
            w -= 1
        else:
            ever_undershot = True
            w += 1
        fees = _fee_helper(w)
        iteration += 1

    return round((cents - fees["fees"]) / 100, 2)


def balance_ratio(steam_net: float | None, platform_min_buy: float | None) -> float | None:
    """余额比例(%) = 净入账 / 最低购买价 ×100，越高越划算。缺值返回 None。"""
    if steam_net is None or platform_min_buy is None or platform_min_buy <= 0:
        return None
    return round(steam_net / platform_min_buy * 100.0, 2)


def decorate(item: dict, fee_rate: float | None = None) -> dict:
    """为标准化 item 附加精确净入账与余额比例。fee_rate 参数保留但不再使用(改用舍入规则)。"""
    net = calculate_after_fee(item.get("steam_ref_cny"))
    item["steam_net_est"] = net
    item["ratio_pct"] = balance_ratio(net, item.get("buff_min_sell"))
    item["ratio_estimate"] = False  # 现为按 Steam 舍入规则的精确估算
    return item
