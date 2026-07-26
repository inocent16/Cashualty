from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from cashualty.features.ledger.models import LedgerGuildConfig


async def get_or_create(session: AsyncSession, guild_id: int) -> LedgerGuildConfig:
    config = await session.get(LedgerGuildConfig, guild_id)
    if config is None:
        config = LedgerGuildConfig(guild_id=guild_id)
        session.add(config)
        await session.commit()
        await session.refresh(config)
    return config
