from __future__ import annotations

import logging

import discord
from discord.ext import commands

logger = logging.getLogger(__name__)

# Every feature is a discord.py extension (a package exposing an async
# setup(bot) function, see cashualty/features/ledger/__init__.py). Adding a
# new, unrelated feature later means writing that package and adding its
# import path here -- nothing else in core/ needs to change.
FEATURE_EXTENSIONS: list[str] = [
    "cashualty.features.ledger",
]


class Cashualty(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)

    async def setup_hook(self) -> None:
        for extension in FEATURE_EXTENSIONS:
            await self.load_extension(extension)
            logger.info("Loaded feature extension: %s", extension)
        synced = await self.tree.sync()
        logger.info("Synced %d application command(s)", len(synced))

    async def on_ready(self) -> None:
        user = self.user
        logger.info("Logged in as %s (id=%s)", user, user.id if user else "unknown")
