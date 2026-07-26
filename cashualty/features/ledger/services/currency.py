from __future__ import annotations

import logging
from datetime import UTC, datetime

import aiohttp
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cashualty.features.ledger.models import Currency, LedgerExchangeRate

logger = logging.getLogger(__name__)

FRANKFURTER_URL = "https://api.frankfurter.app/latest"


async def refresh_rates(session: AsyncSession) -> None:
    """Fetch the current EUR<->USD rate from Frankfurter.app (free, keyless,
    ECB-based) and cache both directions. Never raises -- a failed refresh
    just leaves whatever was last cached in place for convert() to use."""
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as http:
            async with http.get(FRANKFURTER_URL, params={"from": "EUR", "to": "USD"}) as resp:
                resp.raise_for_status()
                data = await resp.json()
        eur_to_usd = float(data["rates"]["USD"])
    except Exception:
        logger.warning("Failed to refresh exchange rates; keeping last cached value", exc_info=True)
        return

    now = datetime.now(UTC)
    session.add(
        LedgerExchangeRate(
            base_currency=Currency.EUR, quote_currency=Currency.USD, rate=eur_to_usd, fetched_at=now
        )
    )
    session.add(
        LedgerExchangeRate(
            base_currency=Currency.USD, quote_currency=Currency.EUR, rate=1 / eur_to_usd, fetched_at=now
        )
    )
    await session.commit()


async def _latest_rate(session: AsyncSession, base: Currency, quote: Currency) -> float | None:
    stmt = (
        select(LedgerExchangeRate.rate)
        .where(LedgerExchangeRate.base_currency == base, LedgerExchangeRate.quote_currency == quote)
        .order_by(LedgerExchangeRate.fetched_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def convert(
    session: AsyncSession, amount_minor: int, from_currency: Currency, to_currency: Currency
) -> int:
    if from_currency == to_currency:
        return amount_minor
    rate = await _latest_rate(session, from_currency, to_currency)
    if rate is None:
        await refresh_rates(session)
        rate = await _latest_rate(session, from_currency, to_currency)
    if rate is None:
        raise RuntimeError(f"No exchange rate available for {from_currency.value}->{to_currency.value}")
    return round(amount_minor * rate)
