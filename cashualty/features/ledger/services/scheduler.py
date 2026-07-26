from __future__ import annotations

import logging
from datetime import UTC, datetime

from discord.ext import commands, tasks
from sqlalchemy import select

from cashualty.core.db import session_scope
from cashualty.features.ledger.models import LedgerTransaction, TransactionStatus
from cashualty.features.ledger.services import guild_config as guild_config_service
from cashualty.features.ledger.services import transactions as transaction_service
from cashualty.features.ledger.utils.embeds import transaction_embed

logger = logging.getLogger(__name__)


def make_publish_loop(bot: commands.Bot) -> tasks.Loop:
    """A polling loop (not a per-transaction timer) that promotes pending
    transactions to published once their grace window has elapsed. Polling
    keeps this simple and crash-safe on a single long-running process --
    nothing is lost if the bot restarts mid-window."""

    @tasks.loop(seconds=30)
    async def publish_pending() -> None:
        now = datetime.now(UTC)
        async with session_scope() as session:
            stmt = select(LedgerTransaction).where(
                LedgerTransaction.status == TransactionStatus.PENDING,
                LedgerTransaction.publish_at <= now,
            )
            due = (await session.execute(stmt)).scalars().all()
            for txn in due:
                config = await guild_config_service.get_or_create(session, txn.guild_id)
                if config.ledger_channel_id is None:
                    continue
                channel = bot.get_channel(config.ledger_channel_id)
                if channel is None:
                    logger.warning(
                        "Ledger channel %s not found for guild %s; leaving #%s pending",
                        config.ledger_channel_id,
                        txn.guild_id,
                        txn.id,
                    )
                    continue
                message = await channel.send(embed=transaction_embed(txn))
                await transaction_service.mark_published(session, txn.id, message.id)

    @publish_pending.before_loop
    async def _wait_ready() -> None:
        await bot.wait_until_ready()

    return publish_pending
