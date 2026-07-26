from __future__ import annotations

import logging

import discord
from discord.ext import commands

from cashualty.config import settings

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

        if settings.dev_guild_id is not None:
            # Guild-scoped syncs are instant; global syncs can take up to an
            # hour to propagate to clients. Set DEV_GUILD_ID while developing.
            guild = discord.Object(id=settings.dev_guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            logger.info("Synced %d command(s) to dev guild %s", len(synced), settings.dev_guild_id)

            # Clear any stale global registration *after* copying from it,
            # otherwise a guild command and a leftover global command both
            # show up as duplicates in Discord's command picker.
            self.tree.clear_commands(guild=None)
            await self.tree.sync()
        else:
            synced = await self.tree.sync()
            logger.info("Synced %d application command(s) globally", len(synced))

    async def on_ready(self) -> None:
        user = self.user
        logger.info("Logged in as %s (id=%s)", user, user.id if user else "unknown")
