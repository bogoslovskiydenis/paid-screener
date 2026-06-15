"""Order Book Depth — анализ стакана заявок."""
from typing import Dict, Any, Optional, List

try:
    from ..utils.logger import setup_logger
except ImportError:
    import logging
    def setup_logger(name: str):
        return logging.getLogger(name)

logger = setup_logger(__name__)


# ---------------------------------------------------------------------------
# Вспомогательные метрики
# ---------------------------------------------------------------------------

def _weighted_mid_pressure(bids: list, asks: list) -> float:
    """
    Взвешенная середина vs простая середина стакана.

    Формула:
        simple_mid  = (best_bid + best_ask) / 2
        weighted_mid = (best_bid × ask_qty + best_ask × bid_qty) / (bid_qty + ask_qty)
        pressure    = (weighted_mid - simple_mid) / simple_mid × 100  [%]

    Положительное значение → покупатели тянут цену вверх.
    Отрицательное → продавцы давят вниз.
    """
    if not bids or not asks:
        return 0.0
    best_bid, bid_qty = bids[0][0], bids[0][1]
    best_ask, ask_qty = asks[0][0], asks[0][1]
    simple_mid = (best_bid + best_ask) / 2
    total_qty = bid_qty + ask_qty
    if total_qty == 0 or simple_mid == 0:
        return 0.0
    weighted_mid = (best_bid * ask_qty + best_ask * bid_qty) / total_qty
    return (weighted_mid - simple_mid) / simple_mid * 100


def _order_flow_imbalance(
    bids: list, asks: list,
    prev_bids: list, prev_asks: list,
) -> Optional[float]:
    """
    Order Flow Imbalance (OFI) на лучших уровнях стакана.

    Формула Cont, Kukanov, Stoikov (2013):
        e_bid = +bid_qty_t  если bid_price_t > bid_price_{t-1}
              = bid_qty_t - bid_qty_{t-1}  если цена та же
              = -bid_qty_{t-1}  если bid_price_t < bid_price_{t-1}

        e_ask = зеркально для ask

        OFI = e_bid - e_ask

    OFI > 0 → давление покупателей → цена скорее вырастет.
    OFI < 0 → давление продавцов  → цена скорее упадёт.
    """
    if not bids or not asks or not prev_bids or not prev_asks:
        return None

    bid_p, bid_q = bids[0][0], bids[0][1]
    prev_bid_p, prev_bid_q = prev_bids[0][0], prev_bids[0][1]
    ask_p, ask_q = asks[0][0], asks[0][1]
    prev_ask_p, prev_ask_q = prev_asks[0][0], prev_asks[0][1]

    if bid_p > prev_bid_p:
        e_bid = bid_q
    elif bid_p == prev_bid_p:
        e_bid = bid_q - prev_bid_q
    else:
        e_bid = -prev_bid_q

    if ask_p < prev_ask_p:
        e_ask = ask_q
    elif ask_p == prev_ask_p:
        e_ask = ask_q - prev_ask_q
    else:
        e_ask = -prev_ask_q

    return e_bid - e_ask


def _trade_imbalance(recent_trades: list) -> Dict[str, Any]:
    """
    Дисбаланс агрессивных сделок (тейкеры).

    Формула:
        buy_vol  = сумма (price × qty) по сделкам где side='buy'
        sell_vol = сумма (price × qty) по сделкам где side='sell'
        imbalance = (buy_vol - sell_vol) / (buy_vol + sell_vol)

    imbalance > +0.2 → агрессивные покупки доминируют → бычий сигнал.
    imbalance < -0.2 → агрессивные продажи → медвежий сигнал.
    """
    if not recent_trades:
        return {"buy_vol": 0, "sell_vol": 0, "imbalance": 0, "signal": "NEUTRAL"}

    buy_vol = sum(t.get("cost", t.get("price", 0) * t.get("amount", 0))
                  for t in recent_trades if t.get("side") == "buy")
    sell_vol = sum(t.get("cost", t.get("price", 0) * t.get("amount", 0))
                   for t in recent_trades if t.get("side") == "sell")
    total = buy_vol + sell_vol
    imbalance = (buy_vol - sell_vol) / total if total > 0 else 0.0

    if imbalance > 0.2:
        signal = "BULLISH"
    elif imbalance < -0.2:
        signal = "BEARISH"
    else:
        signal = "NEUTRAL"

    return {
        "buy_vol": round(buy_vol, 2),
        "sell_vol": round(sell_vol, 2),
        "imbalance": round(imbalance, 3),
        "signal": signal,
    }


def _spoofing_candidates(
    bids: list, asks: list,
    current_price: float,
    proximity_pct: float = 0.3,
    size_multiplier: float = 5.0,
) -> Dict[str, list]:
    """
    Поиск подозрительно крупных заявок вблизи текущей цены.

    Логика:
        1. Берём заявки в радиусе proximity_pct% от цены.
        2. Считаем средний размер всех заявок в стакане.
        3. Заявка считается кандидатом на spoofing если её размер
           превышает средний в size_multiplier раз.

    Это не гарантированный spoofing, но аномально крупные заявки
    вблизи цены часто используются для манипуляции.
    """
    all_sizes = (
        [p * q for p, q in bids[:30]] +
        [p * q for p, q in asks[:30]]
    )
    if not all_sizes:
        return {"bid_spoof": [], "ask_spoof": []}

    avg_size = sum(all_sizes) / len(all_sizes)
    threshold = avg_size * size_multiplier
    close_range = current_price * proximity_pct / 100

    bid_spoof = [
        {
            "price": price,
            "size_usd": round(price * qty, 2),
            "distance_pct": round((current_price - price) / current_price * 100, 3),
            "size_vs_avg": round(price * qty / avg_size, 1),
        }
        for price, qty in bids
        if abs(price - current_price) <= close_range and price * qty >= threshold
    ]

    ask_spoof = [
        {
            "price": price,
            "size_usd": round(price * qty, 2),
            "distance_pct": round((price - current_price) / current_price * 100, 3),
            "size_vs_avg": round(price * qty / avg_size, 1),
        }
        for price, qty in asks
        if abs(price - current_price) <= close_range and price * qty >= threshold
    ]

    return {"bid_spoof": bid_spoof[:3], "ask_spoof": ask_spoof[:3]}


# ---------------------------------------------------------------------------
# Основная функция
# ---------------------------------------------------------------------------

def analyze_orderbook(
    bids: List[List[float]],
    asks: List[List[float]],
    current_price: float,
    depth_pct: float = 2.0,
    prev_bids: Optional[List[List[float]]] = None,
    prev_asks: Optional[List[List[float]]] = None,
    recent_trades: Optional[list] = None,
) -> Dict[str, Any]:
    """
    Полный анализ стакана заявок.

    Метрики:
        imbalance        — дисбаланс объёма bid/ask в depth_pct% от цены
        mid_pressure     — взвешенная середина vs простая середина [%]
        ofi              — Order Flow Imbalance (требует prev_bids/prev_asks)
        trade_imbalance  — дисбаланс агрессивных сделок (требует recent_trades)
        bid/ask_walls    — стенки (заявки > 3× среднего)
        spoofing         — подозрительно крупные заявки вблизи цены

    Parameters
    ----------
    bids          : [[price, qty], ...] убывающие по цене
    asks          : [[price, qty], ...] возрастающие по цене
    current_price : текущая цена
    depth_pct     : глубина анализа в % от цены (default 2%)
    prev_bids     : предыдущий снимок bids (для OFI)
    prev_asks     : предыдущий снимок asks (для OFI)
    recent_trades : список последних сделок ccxt (для trade imbalance)
    """
    if not bids or not asks or current_price <= 0:
        return {
            "bid_volume": 0, "ask_volume": 0, "imbalance": 0,
            "signal": "NEUTRAL", "bid_walls": [], "ask_walls": [],
            "mid_pressure": 0, "ofi": None,
            "trade_imbalance": None, "spoofing": None,
        }

    price_low = current_price * (1 - depth_pct / 100)
    price_high = current_price * (1 + depth_pct / 100)

    bid_vol, bid_sizes = 0.0, []
    for price, qty in bids:
        if price < price_low:
            break
        bid_vol += price * qty
        bid_sizes.append(price * qty)

    ask_vol, ask_sizes = 0.0, []
    for price, qty in asks:
        if price > price_high:
            break
        ask_vol += price * qty
        ask_sizes.append(price * qty)

    total = bid_vol + ask_vol
    imbalance = (bid_vol - ask_vol) / total if total > 0 else 0.0

    if imbalance > 0.20:
        signal = "BULLISH"
    elif imbalance < -0.20:
        signal = "BEARISH"
    else:
        signal = "NEUTRAL"

    avg_bid = bid_vol / len(bid_sizes) if bid_sizes else 0
    avg_ask = ask_vol / len(ask_sizes) if ask_sizes else 0

    bid_walls = [
        {"price": p, "size_usd": round(p * q, 2)}
        for p, q in bids
        if price_low <= p and avg_bid > 0 and p * q > avg_bid * 3
    ][:3]

    ask_walls = [
        {"price": p, "size_usd": round(p * q, 2)}
        for p, q in asks
        if p <= price_high and avg_ask > 0 and p * q > avg_ask * 3
    ][:3]

    # Новые метрики
    mid_pressure = _weighted_mid_pressure(bids, asks)
    ofi = _order_flow_imbalance(bids, asks, prev_bids, prev_asks) if prev_bids else None
    trade_imb = _trade_imbalance(recent_trades) if recent_trades else None
    spoofing = _spoofing_candidates(bids, asks, current_price)

    return {
        "bid_volume": round(bid_vol, 2),
        "ask_volume": round(ask_vol, 2),
        "imbalance": round(imbalance, 3),
        "signal": signal,
        "bid_walls": bid_walls,
        "ask_walls": ask_walls,
        "mid_pressure": round(mid_pressure, 4),
        "ofi": round(ofi, 4) if ofi is not None else None,
        "trade_imbalance": trade_imb,
        "spoofing": spoofing,
    }
