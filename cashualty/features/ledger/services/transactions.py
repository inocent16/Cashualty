from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cashualty.features.ledger.models import (
    Currency,
    LedgerAuditLog,
    LedgerTransaction,
    TransactionSource,
    TransactionStatus,
    TransactionType,
)
from cashualty.features.ledger.services import currency as currency_service

PUBLIC_STATUSES = (TransactionStatus.PUBLISHED, TransactionStatus.CORRECTED)


class TransactionError(Exception):
    """Domain-level error; the cog turns this into an ephemeral reply."""


@dataclass
class CorrectionResult:
    original: LedgerTransaction
    correction: LedgerTransaction


@dataclass
class Totals:
    income_minor: int
    expense_minor: int

    @property
    def net_minor(self) -> int:
        return self.income_minor - self.expense_minor

    @property
    def shortfall_minor(self) -> int:
        return max(0, self.expense_minor - self.income_minor)


async def _get_owned(session: AsyncSession, transaction_id: int, guild_id: int) -> LedgerTransaction:
    txn = await session.get(LedgerTransaction, transaction_id)
    if txn is None or txn.guild_id != guild_id:
        raise TransactionError(f"No transaction #{transaction_id} found in this server.")
    return txn


def _signed(amount_minor: int, type_: TransactionType) -> int:
    return amount_minor if type_ is TransactionType.INCOME else -amount_minor


async def create_transaction(
    session: AsyncSession,
    *,
    guild_id: int,
    type_: TransactionType,
    amount_minor: int,
    currency: Currency,
    description: str,
    created_by: int,
    grace_period_minutes: int,
    source: TransactionSource = TransactionSource.MANUAL,
) -> LedgerTransaction:
    now = datetime.now(UTC)
    immediate = grace_period_minutes <= 0
    txn = LedgerTransaction(
        guild_id=guild_id,
        type=type_,
        amount_minor=_signed(amount_minor, type_),
        currency=currency,
        description=description,
        source=source,
        created_by=created_by,
        status=TransactionStatus.PUBLISHED if immediate else TransactionStatus.PENDING,
        publish_at=now if immediate else now + timedelta(minutes=grace_period_minutes),
    )
    session.add(txn)
    await session.flush()
    session.add(
        LedgerAuditLog(
            guild_id=guild_id,
            transaction_id=txn.id,
            action="create",
            actor_id=created_by,
            details={"immediate": immediate},
        )
    )
    await session.commit()
    await session.refresh(txn)
    return txn


async def mark_published(session: AsyncSession, transaction_id: int, message_id: int) -> None:
    """Takes an id rather than an ORM instance so it works regardless of which
    session (this one, a later one) originally loaded the transaction."""
    txn = await session.get(LedgerTransaction, transaction_id)
    if txn is None:
        return
    txn.status = TransactionStatus.PUBLISHED
    txn.published_message_id = message_id
    await session.commit()


async def cancel_transaction(
    session: AsyncSession, *, transaction_id: int, guild_id: int, actor_id: int, reason: str
) -> LedgerTransaction:
    txn = await _get_owned(session, transaction_id, guild_id)
    if txn.status is not TransactionStatus.PENDING:
        raise TransactionError(
            "That entry is already public and can no longer be cancelled outright — "
            "use `/ledger edit` or `/ledger correction` instead."
        )
    session.add(
        LedgerAuditLog(
            guild_id=guild_id,
            transaction_id=txn.id,
            action="delete-before-publish",
            actor_id=actor_id,
            details={"reason": reason},
        )
    )
    await session.delete(txn)
    await session.commit()
    return txn


async def _create_correction(
    session: AsyncSession,
    *,
    original: LedgerTransaction,
    delta_minor: int,
    currency: Currency,
    description: str,
    actor_id: int,
) -> CorrectionResult:
    if original.status not in PUBLIC_STATUSES:
        raise TransactionError("Only a published entry can be corrected.")
    correction = LedgerTransaction(
        guild_id=original.guild_id,
        type=original.type,
        amount_minor=delta_minor,
        currency=currency,
        description=description,
        source=TransactionSource.MANUAL,
        created_by=actor_id,
        status=TransactionStatus.PUBLISHED,
        publish_at=datetime.now(UTC),
        correction_of_id=original.id,
    )
    original.status = TransactionStatus.CORRECTED
    session.add(correction)
    await session.flush()
    session.add(
        LedgerAuditLog(
            guild_id=original.guild_id,
            transaction_id=original.id,
            action="correct",
            actor_id=actor_id,
            details={"correction_id": correction.id, "delta_minor": delta_minor},
        )
    )
    await session.commit()
    await session.refresh(correction)
    await session.refresh(original)
    return CorrectionResult(original=original, correction=correction)


async def edit_transaction(
    session: AsyncSession,
    *,
    transaction_id: int,
    guild_id: int,
    actor_id: int,
    amount_minor: int | None = None,
    currency: Currency | None = None,
    description: str | None = None,
    type_: TransactionType | None = None,
) -> LedgerTransaction | CorrectionResult:
    """Edit a transaction. Pending entries are mutated in place (never shown
    publicly, so no correction is needed). Published/corrected entries are
    left untouched and a linked correction is created instead, since a public
    record must never be silently rewritten."""
    txn = await _get_owned(session, transaction_id, guild_id)

    if txn.status is TransactionStatus.PENDING:
        if amount_minor is None and currency is None and description is None and type_ is None:
            raise TransactionError("Nothing to change.")
        new_type = type_ or txn.type
        if amount_minor is not None:
            txn.amount_minor = _signed(amount_minor, new_type)
        elif type_ is not None:
            txn.amount_minor = _signed(abs(txn.amount_minor), new_type)
        txn.type = new_type
        if currency is not None:
            txn.currency = currency
        if description is not None:
            txn.description = description
        session.add(
            LedgerAuditLog(
                guild_id=guild_id,
                transaction_id=txn.id,
                action="edit-before-publish",
                actor_id=actor_id,
                details={},
            )
        )
        await session.commit()
        await session.refresh(txn)
        return txn

    new_type = type_ or txn.type
    new_currency = currency or txn.currency
    new_signed = txn.amount_minor if amount_minor is None else _signed(amount_minor, new_type)
    delta = new_signed - txn.amount_minor
    if delta == 0 and description is None:
        raise TransactionError("Nothing to change.")
    note = description or f"Correction of #{txn.id}"
    return await _create_correction(
        session,
        original=txn,
        delta_minor=delta,
        currency=new_currency,
        description=note,
        actor_id=actor_id,
    )


async def add_correction(
    session: AsyncSession,
    *,
    transaction_id: int,
    guild_id: int,
    actor_id: int,
    delta_amount_minor: int,
    delta_currency: Currency,
    description: str,
) -> CorrectionResult:
    """The explicit sibling of edit_transaction's post-publish path: the admin
    states the adjustment directly (positive = increases the balance, negative
    = decreases it) instead of restating new absolute values."""
    txn = await _get_owned(session, transaction_id, guild_id)
    return await _create_correction(
        session,
        original=txn,
        delta_minor=delta_amount_minor,
        currency=delta_currency,
        description=description,
        actor_id=actor_id,
    )


async def list_recent(
    session: AsyncSession, *, guild_id: int, statuses: Sequence[TransactionStatus], limit: int = 25
) -> list[LedgerTransaction]:
    stmt = (
        select(LedgerTransaction)
        .where(LedgerTransaction.guild_id == guild_id, LedgerTransaction.status.in_(statuses))
        .order_by(LedgerTransaction.created_at.desc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())


async def compute_totals(
    session: AsyncSession,
    *,
    guild_id: int,
    base_currency: Currency,
    since: datetime | None = None,
    until: datetime | None = None,
) -> Totals:
    """Sums every published/corrected transaction (never pending -- not public
    yet) in the given window, converting each row into base_currency."""
    stmt = select(LedgerTransaction).where(
        LedgerTransaction.guild_id == guild_id,
        LedgerTransaction.status.in_(PUBLIC_STATUSES),
    )
    if since is not None:
        stmt = stmt.where(LedgerTransaction.created_at >= since)
    if until is not None:
        stmt = stmt.where(LedgerTransaction.created_at < until)
    rows = (await session.execute(stmt)).scalars().all()

    income_minor = 0
    expense_minor = 0
    for row in rows:
        converted = await currency_service.convert(session, row.amount_minor, row.currency, base_currency)
        if converted > 0:
            income_minor += converted
        elif converted < 0:
            expense_minor += -converted
    return Totals(income_minor=income_minor, expense_minor=expense_minor)
