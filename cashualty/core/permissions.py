from __future__ import annotations

from collections.abc import Iterable

import discord


def has_any_role(member: discord.Member, role_ids: Iterable[int]) -> bool:
    """True if member holds at least one of the given role ids. Feature-agnostic:
    features decide *which* role ids matter (admin roles, user roles, ...)."""
    role_ids = set(role_ids)
    if not role_ids:
        return False
    return any(role.id in role_ids for role in member.roles)


def is_manage_guild(member: discord.Member) -> bool:
    """Discord's real 'Manage Guild' permission, independent of any configured
    role lists. Used as a fallback so a real server admin can never be locked out."""
    return member.guild_permissions.manage_guild
