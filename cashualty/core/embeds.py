from __future__ import annotations

import discord

BRAND_COLOR = discord.Color.from_str("#2ecc71")
BRAND_FOOTER = "Cashualty"


def base_embed(
    title: str,
    *,
    description: str | None = None,
    color: discord.Color | None = None,
) -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=color or BRAND_COLOR)
    embed.set_footer(text=BRAND_FOOTER)
    return embed
