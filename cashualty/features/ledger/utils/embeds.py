from __future__ import annotations

from datetime import UTC

import discord

from cashualty.core.embeds import base_embed
from cashualty.features.ledger.models import (
    Currency,
    LedgerGuildConfig,
    LedgerTransaction,
    TransactionType,
)
from cashualty.features.ledger.utils.money import format_minor


def _aware(dt):
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def transaction_embed(txn: LedgerTransaction, *, title: str | None = None) -> discord.Embed:
    kind = "Income" if txn.type is TransactionType.INCOME else "Expense"
    embed = base_embed(title or f"{kind} #{txn.id}", description=txn.description)
    embed.add_field(name="Amount", value=format_minor(txn.amount_minor, txn.currency))
    embed.add_field(name="Recorded by", value=f"<@{txn.created_by}>")
    embed.timestamp = _aware(txn.created_at)
    return embed


def correction_embed(original: LedgerTransaction, correction: LedgerTransaction) -> discord.Embed:
    embed = base_embed(
        f"⚠️ Correction to #{original.id}",
        description=f"{correction.description}\n\nOriginal: {original.description}",
    )
    embed.add_field(name="Adjustment", value=format_minor(correction.amount_minor, correction.currency))
    embed.add_field(name="Recorded by", value=f"<@{correction.created_by}>")
    embed.timestamp = _aware(correction.created_at)
    return embed


def correction_notice(correction: LedgerTransaction) -> str:
    amount = format_minor(correction.amount_minor, correction.currency)
    return f"⚠️ This entry was corrected — see #{correction.id} ({amount})."


def overview_embed(
    *,
    period_label: str,
    income_minor: int,
    expense_minor: int,
    base_currency: Currency,
    shortfall_minor: int,
) -> discord.Embed:
    embed = base_embed(f"Ledger overview — {period_label}")
    embed.add_field(name="Income", value=format_minor(income_minor, base_currency))
    embed.add_field(name="Expenses", value=format_minor(expense_minor, base_currency))
    embed.add_field(name="Net", value=format_minor(income_minor - expense_minor, base_currency))
    if shortfall_minor > 0:
        shortfall = format_minor(shortfall_minor, base_currency)
        embed.add_field(
            name="Owner-covered shortfall",
            value=f"{shortfall} — expenses exceeded income for this period.",
            inline=False,
        )
    return embed


def balance_embed(*, balance_minor: int, base_currency: Currency) -> discord.Embed:
    embed = base_embed("Current balance")
    embed.add_field(name="Balance", value=format_minor(balance_minor, base_currency))
    return embed


def config_view_embed(config: LedgerGuildConfig) -> discord.Embed:
    embed = base_embed("Ledger configuration")
    embed.add_field(name="Admin roles", value=_role_list(config.admin_role_ids), inline=False)
    embed.add_field(name="User roles", value=_role_list(config.user_role_ids), inline=False)
    embed.add_field(name="Ledger channel", value=_channel_mention(config.ledger_channel_id))
    embed.add_field(name="Audit channel", value=_channel_mention(config.audit_channel_id))
    embed.add_field(
        name="Grace period",
        value=(
            f"{config.grace_period_minutes} minute(s)"
            if config.grace_period_minutes > 0
            else "Disabled — entries publish immediately"
        ),
        inline=False,
    )
    embed.add_field(name="Base currency", value=config.base_currency.value)
    return embed


def _role_list(role_ids: list[int]) -> str:
    if not role_ids:
        return "*(none configured)*"
    return ", ".join(f"<@&{r}>" for r in role_ids)


def _channel_mention(channel_id: int | None) -> str:
    return f"<#{channel_id}>" if channel_id else "*(not set)*"
