from __future__ import annotations

import discord

from cashualty.core.permissions import has_any_role, is_manage_guild
from cashualty.features.ledger.models import LedgerGuildConfig


def can_administer(member: discord.Member, config: LedgerGuildConfig) -> bool:
    """Admin commands (add-income/add-expense/cancel/edit/correction/config set).
    Discord's real Manage Guild permission always works, so nobody can lock
    themselves out; configured admin roles work in addition to that."""
    return is_manage_guild(member) or has_any_role(member, config.admin_role_ids)


def can_view(member: discord.Member, config: LedgerGuildConfig) -> bool:
    """Non-admin commands (balance/overview). Admins can always view. Everyone
    else needs an explicitly configured user role -- never open to @everyone
    by default."""
    return can_administer(member, config) or has_any_role(member, config.user_role_ids)
