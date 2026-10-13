from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Callable
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class BrokerPosition:
    symbol: str
    qty: float
    market_value: float
    current_price: float


@dataclass(frozen=True)
class BrokerLimits:
    max_order_notional: float = 100.0
    max_total_notional: float = 500.0
    rebalance_threshold: float = 0.03
    max_target_weight: float = 0.18
    max_gross_target: float = 0.90


@dataclass(frozen=True)
class PlannedOrder:
    symbol: str
    side: str
    client_order_id: str
    estimated_notional: float
    qty: float | None = None
    notional: float | None = None

    def alpaca_payload(self) -> dict[str, str]:
        payload = {
            "symbol": self.symbol,
            "side": self.side,
            "type": "market",
            "time_in_force": "day",
            "client_order_id": self.client_order_id,
        }
        if self.qty is not None:
            payload["qty"] = f"{self.qty:.6f}".rstrip("0").rstrip(".")
        if self.notional is not None:
            payload["notional"] = f"{self.notional:.2f}"
        return payload


def _order_id(signal_date: str, symbol: str, side: str) -> str:
    material = f"autotrader-v2:{signal_date}:{symbol}:{side}"
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:14]
    return f"at2-{signal_date.replace('-', '')}-{symbol.lower()}-{side[0]}-{digest}"[:48]


def validate_targets(targets: list[dict[str, Any]], limits: BrokerLimits) -> dict[str, float]:
    if not (
        limits.max_order_notional > 0
        and limits.max_total_notional > 0
        and 0 <= limits.rebalance_threshold < 1
        and 0 < limits.max_target_weight <= 1
        and 0 < limits.max_gross_target <= 1
    ):
        raise ValueError("invalid broker safety limits")
    result: dict[str, float] = {}
    for row in targets:
        symbol = str(row["symbol"]).upper().strip()
        weight = float(row["weight"])
        if not re.fullmatch(r"[A-Z0-9.\-]{1,15}", symbol) or not 0 <= weight <= limits.max_target_weight + 1e-12:
            raise ValueError(f"unsafe target for {symbol or '<empty>'}: {weight}")
        if symbol in result:
            raise ValueError(f"duplicate target symbol: {symbol}")
        result[symbol] = weight
    if sum(result.values()) > limits.max_gross_target + 1e-12:
        raise ValueError(f"gross target exceeds limit: {sum(result.values()):.4f}")
    return result


def plan_rebalance(
    *,
    equity: float,
    cash: float,
    positions: list[BrokerPosition],
    targets: list[dict[str, Any]],
    signal_date: str,
    limits: BrokerLimits,
) -> list[PlannedOrder]:
    """Create a long-only, cash-limited order plan without assuming sell proceeds."""
    if equity <= 0 or cash < 0:
        raise ValueError("broker returned invalid equity or cash")
    weights = validate_targets(targets, limits)
    if len({position.symbol for position in positions}) != len(positions):
        raise ValueError("broker returned duplicate positions")
    if any(position.qty < 0 or position.market_value < 0 for position in positions):
        raise ValueError("short or negative broker positions are not allowed")
    held = {position.symbol: position for position in positions}
    symbols = set(held) | set(weights)
    remaining_total = limits.max_total_notional
    orders: list[PlannedOrder] = []

    for symbol in sorted(symbols):
        position = held.get(symbol)
        current = position.market_value if position else 0.0
        desired = equity * weights.get(symbol, 0.0)
        delta = desired - current
        if delta >= 0 or abs(delta) / equity < limits.rebalance_threshold:
            continue
        if not position or position.qty <= 0 or position.current_price <= 0:
            continue
        notional = min(abs(delta), limits.max_order_notional, remaining_total, current)
        qty = min(position.qty, notional / position.current_price)
        if qty <= 0:
            continue
        estimated = qty * position.current_price
        orders.append(PlannedOrder(
            symbol=symbol,
            side="sell",
            qty=qty,
            notional=None,
            estimated_notional=estimated,
            client_order_id=_order_id(signal_date, symbol, "sell"),
        ))
        remaining_total -= estimated
        if remaining_total <= 0:
            return orders

    available_cash = cash
    for symbol in sorted(weights, key=weights.get, reverse=True):
        position = held.get(symbol)
        current = position.market_value if position else 0.0
        desired = equity * weights[symbol]
        delta = desired - current
        if delta <= 0 or delta / equity < limits.rebalance_threshold:
            continue
        notional = min(delta, limits.max_order_notional, remaining_total, available_cash)
        notional = float(int(notional * 100)) / 100.0
        if notional < 1.0:
            continue
        orders.append(PlannedOrder(
            symbol=symbol,
            side="buy",
            qty=None,
            notional=notional,
            estimated_notional=notional,
            client_order_id=_order_id(signal_date, symbol, "buy"),
        ))
        remaining_total -= notional
        available_cash -= notional
        if remaining_total <= 0 or available_cash < 1.0:
            break
    return orders


class AlpacaClient:
    def __init__(
        self,
        key_id: str,
        secret_key: str,
        *,
        paper: bool = True,
        transport: Callable[[str, str, dict[str, Any] | None], Any] | None = None,
    ) -> None:
        if not key_id or not secret_key:
            raise ValueError("Alpaca credentials are required")
        self.base_url = "https://paper-api.alpaca.markets" if paper else "https://api.alpaca.markets"
        self.key_id = key_id
        self.secret_key = secret_key
        self._transport = transport

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        if self._transport:
            return self._transport(method, path, payload)
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = Request(
            self.base_url + path,
            data=body,
            method=method,
            headers={
                "APCA-API-KEY-ID": self.key_id,
                "APCA-API-SECRET-KEY": self.secret_key,
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "PkLavc-Autotrader/2",
            },
        )
        try:
            with urlopen(request, timeout=30) as response:
                raw = response.read()
                return json.loads(raw) if raw else None
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
            raise RuntimeError(f"Alpaca API {exc.code} for {method} {path}: {detail}") from exc

    def account(self) -> dict[str, Any]:
        return self._request("GET", "/v2/account")

    def clock(self) -> dict[str, Any]:
        return self._request("GET", "/v2/clock")

    def positions(self) -> list[BrokerPosition]:
        rows = self._request("GET", "/v2/positions")
        return [
            BrokerPosition(
                symbol=str(row["symbol"]).upper(),
                qty=float(row["qty"]),
                market_value=float(row["market_value"]),
                current_price=float(row["current_price"]),
            )
            for row in rows
        ]

    def order_by_client_id(self, client_order_id: str) -> dict[str, Any] | None:
        path = "/v2/orders:by_client_order_id?" + urlencode({"client_order_id": client_order_id})
        try:
            return self._request("GET", path)
        except RuntimeError as exc:
            if "Alpaca API 404" in str(exc):
                return None
            raise

    def submit(self, order: PlannedOrder) -> dict[str, Any]:
        return self._request("POST", "/v2/orders", order.alpaca_payload())

    def order(self, order_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v2/orders/{order_id}")
