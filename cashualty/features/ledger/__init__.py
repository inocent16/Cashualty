from __future__ import annotations

from discord.ext import commands


async def setup(bot: commands.Bot) -> None:
    from .cog import LedgerCog

    await bot.add_cog(LedgerCog(bot))
