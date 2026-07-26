from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from cashualty.features.ledger.models import Currency, LedgerTransaction, TransactionStatus, TransactionType
from cashualty.features.ledger.services import transactions as txn_service

USER_ID = 999


async def test_create_transaction_with_grace_period_is_pending(session: AsyncSession, guild_id: int) -> None:
    txn = await txn_service.create_transaction(
        session,
        guild_id=guild_id,
        type_=TransactionType.INCOME,
        amount_minor=1000,
        currency=Currency.EUR,
        description="Ko-fi donation",
        created_by=USER_ID,
        grace_period_minutes=10,
    )
    assert txn.status is TransactionStatus.PENDING
    assert txn.amount_minor == 1000


async def test_create_transaction_with_zero_grace_period_publishes_immediately(
    session: AsyncSession, guild_id: int
) -> None:
    txn = await txn_service.create_transaction(
        session,
        guild_id=guild_id,
        type_=TransactionType.EXPENSE,
        amount_minor=500,
        currency=Currency.EUR,
        description="Game server",
        created_by=USER_ID,
        grace_period_minutes=0,
    )
    assert txn.status is TransactionStatus.PUBLISHED
    assert txn.amount_minor == -500  # expenses are stored as a negative signed amount


async def test_cancel_pending_transaction_hard_deletes(session: AsyncSession, guild_id: int) -> None:
    txn = await txn_service.create_transaction(
        session,
        guild_id=guild_id,
        type_=TransactionType.INCOME,
        amount_minor=1000,
        currency=Currency.EUR,
        description="test",
        created_by=USER_ID,
        grace_period_minutes=10,
    )

    await txn_service.cancel_transaction(
        session, transaction_id=txn.id, guild_id=guild_id, actor_id=USER_ID, reason="typo"
    )

    assert await session.get(LedgerTransaction, txn.id) is None


async def test_cancel_published_transaction_is_rejected(session: AsyncSession, guild_id: int) -> None:
    txn = await txn_service.create_transaction(
        session,
        guild_id=guild_id,
        type_=TransactionType.INCOME,
        amount_minor=1000,
        currency=Currency.EUR,
        description="test",
        created_by=USER_ID,
        grace_period_minutes=0,
    )

    with pytest.raises(txn_service.TransactionError):
        await txn_service.cancel_transaction(
            session, transaction_id=txn.id, guild_id=guild_id, actor_id=USER_ID, reason="oops"
        )

    # rejected, not deleted
    assert await session.get(LedgerTransaction, txn.id) is not None


async def test_edit_pending_transaction_mutates_in_place(session: AsyncSession, guild_id: int) -> None:
    txn = await txn_service.create_transaction(
        session,
        guild_id=guild_id,
        type_=TransactionType.INCOME,
        amount_minor=1000,
        currency=Currency.EUR,
        description="test",
        created_by=USER_ID,
        grace_period_minutes=10,
    )

    result = await txn_service.edit_transaction(
        session, transaction_id=txn.id, guild_id=guild_id, actor_id=USER_ID, amount_minor=2000
    )

    assert isinstance(result, LedgerTransaction)
    assert result.status is TransactionStatus.PENDING
    assert result.amount_minor == 2000


async def test_edit_published_transaction_creates_correction_not_a_silent_edit(
    session: AsyncSession, guild_id: int
) -> None:
    txn = await txn_service.create_transaction(
        session,
        guild_id=guild_id,
        type_=TransactionType.EXPENSE,
        amount_minor=1000,
        currency=Currency.EUR,
        description="Cloud hosting",
        created_by=USER_ID,
        grace_period_minutes=0,
    )

    result = await txn_service.edit_transaction(
        session, transaction_id=txn.id, guild_id=guild_id, actor_id=USER_ID, amount_minor=1500
    )

    assert isinstance(result, txn_service.CorrectionResult)
    assert result.original.id == txn.id
    assert result.original.status is TransactionStatus.CORRECTED
    assert result.original.amount_minor == -1000  # original row is never rewritten
    assert result.correction.amount_minor == -500  # delta needed to reach -1500 total
    assert result.correction.correction_of_id == txn.id


async def test_add_correction_applies_explicit_signed_delta(session: AsyncSession, guild_id: int) -> None:
    txn = await txn_service.create_transaction(
        session,
        guild_id=guild_id,
        type_=TransactionType.EXPENSE,
        amount_minor=1000,
        currency=Currency.EUR,
        description="Cloud hosting",
        created_by=USER_ID,
        grace_period_minutes=0,
    )

    result = await txn_service.add_correction(
        session,
        transaction_id=txn.id,
        guild_id=guild_id,
        actor_id=USER_ID,
        delta_amount_minor=300,
        delta_currency=Currency.EUR,
        description="partial refund from vendor",
    )

    assert result.correction.amount_minor == 300
    assert result.original.status is TransactionStatus.CORRECTED


async def test_compute_totals_and_owner_covered_shortfall(session: AsyncSession, guild_id: int) -> None:
    await txn_service.create_transaction(
        session,
        guild_id=guild_id,
        type_=TransactionType.INCOME,
        amount_minor=1000,
        currency=Currency.EUR,
        description="donations",
        created_by=USER_ID,
        grace_period_minutes=0,
    )
    await txn_service.create_transaction(
        session,
        guild_id=guild_id,
        type_=TransactionType.EXPENSE,
        amount_minor=1500,
        currency=Currency.EUR,
        description="server bill",
        created_by=USER_ID,
        grace_period_minutes=0,
    )

    totals = await txn_service.compute_totals(session, guild_id=guild_id, base_currency=Currency.EUR)

    assert totals.income_minor == 1000
    assert totals.expense_minor == 1500
    assert totals.net_minor == -500
    assert totals.shortfall_minor == 500


async def test_compute_totals_excludes_pending_transactions(session: AsyncSession, guild_id: int) -> None:
    await txn_service.create_transaction(
        session,
        guild_id=guild_id,
        type_=TransactionType.INCOME,
        amount_minor=1000,
        currency=Currency.EUR,
        description="not yet public",
        created_by=USER_ID,
        grace_period_minutes=10,
    )

    totals = await txn_service.compute_totals(session, guild_id=guild_id, base_currency=Currency.EUR)

    assert totals.income_minor == 0
    assert totals.expense_minor == 0
