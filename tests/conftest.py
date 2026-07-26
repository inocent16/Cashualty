from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from cashualty.core.db import Base
from cashualty.features.ledger.models import LedgerGuildConfig

GUILD_ID = 12345


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    # StaticPool keeps every checkout on the same underlying connection, which
    # is required for a SQLite ":memory:" database -- otherwise each new
    # connection from the pool would see a separate, empty database.
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db_session:
        yield db_session

    await engine.dispose()


@pytest_asyncio.fixture
async def guild_id(session: AsyncSession) -> int:
    session.add(LedgerGuildConfig(guild_id=GUILD_ID))
    await session.commit()
    return GUILD_ID
