from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from cashualty.features.ledger.models import Currency, LedgerExchangeRate
from cashualty.features.ledger.services import currency as currency_service


async def test_convert_same_currency_is_identity(session: AsyncSession) -> None:
    result = await currency_service.convert(session, 1000, Currency.EUR, Currency.EUR)
    assert result == 1000


async def test_convert_uses_cached_rate(session: AsyncSession) -> None:
    session.add(LedgerExchangeRate(base_currency=Currency.EUR, quote_currency=Currency.USD, rate=1.1))
    await session.commit()

    result = await currency_service.convert(session, 1000, Currency.EUR, Currency.USD)

    assert result == 1100


async def test_convert_uses_most_recently_fetched_rate(session: AsyncSession) -> None:
    now = datetime.now(UTC)
    session.add_all(
        [
            LedgerExchangeRate(
                base_currency=Currency.EUR,
                quote_currency=Currency.USD,
                rate=1.0,
                fetched_at=now - timedelta(days=1),
            ),
            LedgerExchangeRate(
                base_currency=Currency.EUR, quote_currency=Currency.USD, rate=1.2, fetched_at=now
            ),
        ]
    )
    await session.commit()

    result = await currency_service.convert(session, 1000, Currency.EUR, Currency.USD)

    assert result == 1200
