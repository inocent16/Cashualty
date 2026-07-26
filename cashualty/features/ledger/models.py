from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import JSON, BigInteger, DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from cashualty.core.db import Base


class Currency(StrEnum):
    EUR = "EUR"
    USD = "USD"


class TransactionType(StrEnum):
    INCOME = "income"
    EXPENSE = "expense"


class TransactionSource(StrEnum):
    MANUAL = "manual"
    KOFI = "kofi"


class TransactionStatus(StrEnum):
    PENDING = "pending"
    PUBLISHED = "published"
    CORRECTED = "corrected"


class LedgerGuildConfig(Base):
    """Per-guild settings for the ledger feature. Row is created lazily on first use."""

    __tablename__ = "ledger_guild_config"

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    admin_role_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    user_role_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    ledger_channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    audit_channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    grace_period_minutes: Mapped[int] = mapped_column(Integer, default=10)
    base_currency: Mapped[Currency] = mapped_column(Enum(Currency), default=Currency.EUR)


class LedgerTransaction(Base):
    """A single income/expense entry, or a correction linked to one via correction_of_id.

    amount_minor is signed (integer minor units, e.g. cents): positive for income,
    negative for expense, and whatever signed delta is needed for a correction row.
    This keeps every total a plain SUM(amount_minor), with no per-row sign-casing.
    """

    __tablename__ = "ledger_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ledger_guild_config.guild_id"), index=True
    )
    type: Mapped[TransactionType] = mapped_column(Enum(TransactionType))
    amount_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[Currency] = mapped_column(Enum(Currency))
    description: Mapped[str] = mapped_column(String(500))
    source: Mapped[TransactionSource] = mapped_column(
        Enum(TransactionSource), default=TransactionSource.MANUAL
    )
    created_by: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    status: Mapped[TransactionStatus] = mapped_column(
        Enum(TransactionStatus), default=TransactionStatus.PENDING
    )
    publish_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    published_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    correction_of_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("ledger_transactions.id"), nullable=True
    )


class LedgerAuditLog(Base):
    """Append-only record of every action taken, regardless of whether the
    underlying transaction was ever shown publicly. Satisfies "still visible
    in the logs" even for entries hard-deleted during the grace window."""

    __tablename__ = "ledger_audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True)
    transaction_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("ledger_transactions.id"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(50))
    actor_id: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class LedgerExchangeRate(Base):
    """Daily-refreshed cache of conversion rates. Conversion falls back to the
    latest cached row for a pair if a live refresh fails."""

    __tablename__ = "ledger_exchange_rates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    base_currency: Mapped[Currency] = mapped_column(Enum(Currency))
    quote_currency: Mapped[Currency] = mapped_column(Enum(Currency))
    rate: Mapped[float] = mapped_column()
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
