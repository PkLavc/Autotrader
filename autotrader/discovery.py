"""Automatic, bounded discovery from a daily-updated S&P 500 constituent snapshot.

Only today's constituents are used for live paper research. Historical backtests
must use point-in-time membership snapshots, not this current-members CSV.
"""
from __future__ import annotations

import csv
from datetime import date
import hashlib
import io
import re
from urllib.request import Request, urlopen

from .config import StrategyConfig
from .portfolio import sector_of

S_AND_P_SOURCE = (
    "https://raw.githubusercontent.com/datasets/"
    "s-and-p-500-companies/main/data/constituents.csv"
)
GICS_TO_SECTOR = {
    "Information Technology": "technology",
    "Consumer Discretionary": "discretionary",
    "Communication Services": "communication",
    "Financials": "financials",
    "Health Care": "healthcare",
    "Consumer Staples": "staples",
    "Energy": "energy",
    "Industrials": "industrials",
    "Utilities": "utilities",
    "Materials": "materials",
    "Real Estate": "real_estate",
}
TICKER = re.compile(r"^[A-Z][A-Z0-9-]{0,9}$")


def parse_constituents(csv_text: str) -> dict[str, str]:
    reader = csv.DictReader(io.StringIO(csv_text))
    if not {"Symbol", "GICS Sector"}.issubset(reader.fieldnames or []):
        raise ValueError("Constituent CSV lacks required columns")
    sectors = {}
    for row in reader:
        symbol = str(row.get("Symbol", "")).strip().upper().replace(".", "-")
        sector = GICS_TO_SECTOR.get(str(row.get("GICS Sector", "")).strip())
        if TICKER.fullmatch(symbol) and sector:
            sectors[symbol] = sector
    if not 400 <= len(sectors) <= 600:
        raise ValueError(f"Constituent count outside safe bounds: {len(sectors)}")
    return sectors


def download_constituents(*, timeout: int = 12) -> dict[str, str]:
    request = Request(S_AND_P_SOURCE, headers={
        "Accept": "text/csv",
        "User-Agent": "PkLavc-Autotrader-Research/3.0",
    })
    with urlopen(request, timeout=timeout) as response:
        payload = response.read(200_000)
        if len(payload) >= 200_000:
            raise ValueError("Unexpectedly large constituent feed")
    return parse_constituents(payload.decode("utf-8-sig"))


def discover_market(
    market_date: str,
    cfg: StrategyConfig,
    *,
    constituents: dict[str, str] | None = None,
) -> tuple[list[str], dict[str, str], dict]:
    """Rotate through the entire discovery pool with bounded daily downloads."""
    fallback = list(dict.fromkeys(cfg.symbols))
    sectors = {symbol: sector_of(symbol) for symbol in fallback}
    status = "fresh"
    if constituents is None:
        try:
            constituents = download_constituents()
        except (OSError, ValueError, UnicodeError) as exc:
            constituents = {}
            status = f"fallback:{type(exc).__name__}"

    # Data source can be down; never remove research holdings because of it.
    if constituents:
        sectors.update(constituents)
        outside = sorted(set(constituents) - set(fallback))
        # A different deterministic slice each market date; no future information.
        # Traverse a pseudo-shuffled ordering to avoid alphabetical size/sector bias.
        cycle = sorted(outside, key=lambda s: hashlib.sha256(s.encode()).digest())
        offset = (date.fromisoformat(market_date).toordinal() * cfg.daily_discovery_count) % max(len(cycle), 1)
        count = min(max(cfg.daily_discovery_count, 0), len(cycle))
        discoveries = (cycle[offset:] + cycle[:offset])[:count]
    else:
        discoveries = []
    selected = fallback + discoveries
    return selected, sectors, {
        "source": S_AND_P_SOURCE,
        "status": status,
        "constituents": len(constituents),
        "monitored_today": len(selected),
        "discovered_today": discoveries,
        "candidate_pool_size": len(set(fallback) | set(constituents)),
    }
