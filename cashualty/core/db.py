from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from cashualty.config import settings


class Base(DeclarativeBase):
    """Shared declarative base. Every feature's models import this so all
    tables live in one SQLite file and one Alembic migration history."""


engine = create_async_engine(f"sqlite+aiosqlite:///{settings.database_path}")
_session_factory = async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    async with _session_factory() as session:
        yield session
