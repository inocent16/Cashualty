from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands

from cashualty.core.db import session_scope
from cashualty.features.ledger.models import Currency, TransactionStatus, TransactionType
from cashualty.features.ledger.services import guild_config as guild_config_service
from cashualty.features.ledger.services import permissions as permissions_service
from cashualty.features.ledger.services import scheduler
from cashualty.features.ledger.services import transactions as transaction_service
from cashualty.features.ledger.services.transactions import CorrectionResult, TransactionError
from cashualty.features.ledger.utils import embeds
from cashualty.features.ledger.utils.money import format_minor, to_minor

logger = logging.getLogger(__name__)


def _period_bounds(
    period: Literal["week", "month", "year"], reference: datetime
) -> tuple[datetime, datetime, str]:
    reference = reference.astimezone(UTC)
    if period == "week":
        start = (reference - timedelta(days=reference.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        end = start + timedelta(days=7)
        label = f"week of {start:%Y-%m-%d}"
    elif period == "month":
        start = reference.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        label = f"{start:%B %Y}"
    else:
        start = reference.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        end = start.replace(year=start.year + 1)
        label = f"{start:%Y}"
    return start, end, label


@app_commands.guild_only()
class LedgerCog(
    commands.GroupCog,
    group_name="ledger",
    description="Transparent community income/expense tracking",
):
    """The cash-transparency feature: income/expense tracking with a
    configurable public-grace-window and correction-based edits."""

    config_group = app_commands.Group(name="config", description="View or change ledger settings")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._publish_loop = scheduler.make_publish_loop(bot)

    async def cog_load(self) -> None:
        self._publish_loop.start()

    async def cog_unload(self) -> None:
        self._publish_loop.cancel()

    # -- shared guards -----------------------------------------------------

    async def _require_admin(self, interaction: discord.Interaction, config) -> bool:
        assert isinstance(interaction.user, discord.Member)
        if permissions_service.can_administer(interaction.user, config):
            return True
        await interaction.response.send_message(
            "You don't have permission to use this command. A server admin can grant it via "
            "`/ledger config admin-roles`.",
            ephemeral=True,
        )
        return False

    async def _require_view(self, interaction: discord.Interaction, config) -> bool:
        assert isinstance(interaction.user, discord.Member)
        if permissions_service.can_view(interaction.user, config):
            return True
        await interaction.response.send_message(
            "You don't have permission to use this command. A server admin can grant it via "
            "`/ledger config user-roles`.",
            ephemeral=True,
        )
        return False

    async def _require_manage_guild(self, interaction: discord.Interaction) -> bool:
        assert isinstance(interaction.user, discord.Member)
        if interaction.user.guild_permissions.manage_guild:
            return True
        await interaction.response.send_message(
            "Only someone with this server's Manage Server permission can change ledger settings.",
            ephemeral=True,
        )
        return False

    async def _transaction_choices(
        self, interaction: discord.Interaction, statuses: list[TransactionStatus], current: str
    ) -> list[app_commands.Choice[int]]:
        if interaction.guild_id is None:
            return []
        async with session_scope() as session:
            rows = await transaction_service.list_recent(
                session, guild_id=interaction.guild_id, statuses=statuses, limit=25
            )
        needle = current.lower()
        choices: list[app_commands.Choice[int]] = []
        for row in rows:
            # Match typed text against id/description/amount, but only ever
            # display the id -- the user just wants "#10", not a preview.
            search_text = f"{row.id} {row.description} {format_minor(row.amount_minor, row.currency)}"
            if needle and needle not in search_text.lower():
                continue
            choices.append(app_commands.Choice(name=f"#{row.id}", value=row.id))
        return choices[:25]

    async def _publish_correction(
        self, interaction: discord.Interaction, result: CorrectionResult, config
    ) -> None:
        original, correction = result.original, result.correction
        channel = (
            interaction.guild.get_channel(config.ledger_channel_id)
            if interaction.guild and config.ledger_channel_id
            else None
        )
        if channel is not None:
            message = await channel.send(embed=embeds.correction_embed(original, correction))
            async with session_scope() as session:
                await transaction_service.mark_published(session, correction.id, message.id)
            if original.published_message_id:
                try:
                    original_message = await channel.fetch_message(original.published_message_id)
                    await original_message.reply(embeds.correction_notice(correction), mention_author=False)
                except discord.HTTPException:
                    logger.warning(
                        "Could not annotate original message %s for txn #%s",
                        original.published_message_id,
                        original.id,
                    )
        await interaction.response.send_message(
            f"Correction #{correction.id} recorded for #{original.id}.", ephemeral=True
        )

    # -- add-income / add-expense -------------------------------------------

    @app_commands.command(name="add-income", description="Record a new income entry")
    @app_commands.describe(
        amount="Amount received", currency="Currency of the amount", description="What this income is"
    )
    async def add_income(
        self,
        interaction: discord.Interaction,
        amount: app_commands.Range[float, 0.01],
        currency: Currency,
        description: str,
    ) -> None:
        await self._add_transaction(interaction, TransactionType.INCOME, amount, currency, description)

    @app_commands.command(name="add-expense", description="Record a new expense entry")
    @app_commands.describe(
        amount="Amount spent", currency="Currency of the amount", description="What this expense is for"
    )
    async def add_expense(
        self,
        interaction: discord.Interaction,
        amount: app_commands.Range[float, 0.01],
        currency: Currency,
        description: str,
    ) -> None:
        await self._add_transaction(interaction, TransactionType.EXPENSE, amount, currency, description)

    async def _add_transaction(
        self,
        interaction: discord.Interaction,
        type_: TransactionType,
        amount: float,
        currency: Currency,
        description: str,
    ) -> None:
        assert interaction.guild_id is not None and interaction.guild is not None
        posted_now = False
        async with session_scope() as session:
            config = await guild_config_service.get_or_create(session, interaction.guild_id)
            if not await self._require_admin(interaction, config):
                return
            if config.ledger_channel_id is None:
                await interaction.response.send_message(
                    "Set a ledger channel first with `/ledger config ledger-channel`.", ephemeral=True
                )
                return

            txn = await transaction_service.create_transaction(
                session,
                guild_id=interaction.guild_id,
                type_=type_,
                amount_minor=to_minor(amount),
                currency=currency,
                description=description,
                created_by=interaction.user.id,
                grace_period_minutes=config.grace_period_minutes,
            )
            posted_now = txn.status is TransactionStatus.PUBLISHED
            if posted_now:
                channel = interaction.guild.get_channel(config.ledger_channel_id)
                if channel is not None:
                    message = await channel.send(embed=embeds.transaction_embed(txn))
                    await transaction_service.mark_published(session, txn.id, message.id)

        grace = config.grace_period_minutes
        suffix = " — published immediately" if posted_now else f" — publishing in {grace} minute(s)"
        await interaction.response.send_message(
            embed=embeds.transaction_embed(txn, title=f"Recorded #{txn.id}{suffix}"), ephemeral=True
        )

    # -- cancel --------------------------------------------------------------

    @app_commands.command(name="cancel", description="Delete a not-yet-public entry entirely")
    @app_commands.describe(transaction="The entry to cancel", reason="Why you're cancelling it")
    async def cancel(self, interaction: discord.Interaction, transaction: int, reason: str) -> None:
        assert interaction.guild_id is not None
        async with session_scope() as session:
            config = await guild_config_service.get_or_create(session, interaction.guild_id)
            if not await self._require_admin(interaction, config):
                return
            try:
                txn = await transaction_service.cancel_transaction(
                    session,
                    transaction_id=transaction,
                    guild_id=interaction.guild_id,
                    actor_id=interaction.user.id,
                    reason=reason,
                )
            except TransactionError as exc:
                await interaction.response.send_message(str(exc), ephemeral=True)
                return
        await interaction.response.send_message(
            f"Cancelled #{txn.id} ({txn.description}). It was never made public.", ephemeral=True
        )

    @cancel.autocomplete("transaction")
    async def cancel_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        return await self._transaction_choices(interaction, [TransactionStatus.PENDING], current)

    # -- edit ------------------------------------------------------------------

    @app_commands.command(
        name="edit", description="Edit an entry: in place if still pending, otherwise via a correction"
    )
    @app_commands.describe(
        transaction="The entry to edit",
        amount="New amount",
        currency="New currency",
        description="New description (or the correction note, if already public)",
        type="New type",
    )
    async def edit(
        self,
        interaction: discord.Interaction,
        transaction: int,
        amount: app_commands.Range[float, 0.01] | None = None,
        currency: Currency | None = None,
        description: str | None = None,
        type: TransactionType | None = None,
    ) -> None:
        assert interaction.guild_id is not None
        async with session_scope() as session:
            config = await guild_config_service.get_or_create(session, interaction.guild_id)
            if not await self._require_admin(interaction, config):
                return
            try:
                result = await transaction_service.edit_transaction(
                    session,
                    transaction_id=transaction,
                    guild_id=interaction.guild_id,
                    actor_id=interaction.user.id,
                    amount_minor=to_minor(amount) if amount is not None else None,
                    currency=currency,
                    description=description,
                    type_=type,
                )
            except TransactionError as exc:
                await interaction.response.send_message(str(exc), ephemeral=True)
                return

        if isinstance(result, CorrectionResult):
            await self._publish_correction(interaction, result, config)
        else:
            await interaction.response.send_message(
                embed=embeds.transaction_embed(result, title=f"Updated #{result.id}"), ephemeral=True
            )

    @edit.autocomplete("transaction")
    async def edit_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        return await self._transaction_choices(
            interaction,
            [TransactionStatus.PENDING, TransactionStatus.PUBLISHED, TransactionStatus.CORRECTED],
            current,
        )

    # -- correction --------------------------------------------------------------

    @app_commands.command(name="correction", description="Add an explicit adjustment to a published entry")
    @app_commands.describe(
        transaction="The published entry to adjust",
        delta_amount="Signed adjustment: positive increases the balance, negative decreases it",
        currency="Currency of the adjustment",
        description="Why this adjustment is being made",
    )
    async def correction(
        self,
        interaction: discord.Interaction,
        transaction: int,
        delta_amount: float,
        currency: Currency,
        description: str,
    ) -> None:
        assert interaction.guild_id is not None
        async with session_scope() as session:
            config = await guild_config_service.get_or_create(session, interaction.guild_id)
            if not await self._require_admin(interaction, config):
                return
            try:
                result = await transaction_service.add_correction(
                    session,
                    transaction_id=transaction,
                    guild_id=interaction.guild_id,
                    actor_id=interaction.user.id,
                    delta_amount_minor=to_minor(delta_amount),
                    delta_currency=currency,
                    description=description,
                )
            except TransactionError as exc:
                await interaction.response.send_message(str(exc), ephemeral=True)
                return
        await self._publish_correction(interaction, result, config)

    @correction.autocomplete("transaction")
    async def correction_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        return await self._transaction_choices(
            interaction, list(transaction_service.PUBLIC_STATUSES), current
        )

    # -- balance / overview --------------------------------------------------

    @app_commands.command(name="balance", description="Show the current ledger balance")
    async def balance(self, interaction: discord.Interaction) -> None:
        assert interaction.guild_id is not None
        async with session_scope() as session:
            config = await guild_config_service.get_or_create(session, interaction.guild_id)
            if not await self._require_view(interaction, config):
                return
            totals = await transaction_service.compute_totals(
                session, guild_id=interaction.guild_id, base_currency=config.base_currency
            )
        await interaction.response.send_message(
            embed=embeds.balance_embed(balance_minor=totals.net_minor, base_currency=config.base_currency)
        )

    @app_commands.command(name="overview", description="Show an income/expense breakdown for a period")
    @app_commands.describe(
        period="Period to summarize", date="Any date within the period (YYYY-MM-DD); defaults to today"
    )
    async def overview(
        self,
        interaction: discord.Interaction,
        period: Literal["week", "month", "year"],
        date: str | None = None,
    ) -> None:
        assert interaction.guild_id is not None
        async with session_scope() as session:
            config = await guild_config_service.get_or_create(session, interaction.guild_id)
            if not await self._require_view(interaction, config):
                return

            reference = datetime.now(UTC)
            if date is not None:
                try:
                    reference = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=UTC)
                except ValueError:
                    await interaction.response.send_message(
                        "Date must be in YYYY-MM-DD format.", ephemeral=True
                    )
                    return

            since, until, label = _period_bounds(period, reference)
            totals = await transaction_service.compute_totals(
                session,
                guild_id=interaction.guild_id,
                base_currency=config.base_currency,
                since=since,
                until=until,
            )
        await interaction.response.send_message(
            embed=embeds.overview_embed(
                period_label=label,
                income_minor=totals.income_minor,
                expense_minor=totals.expense_minor,
                base_currency=config.base_currency,
                shortfall_minor=totals.shortfall_minor,
            )
        )

    # -- config ---------------------------------------------------------------

    @config_group.command(name="view", description="Show the current ledger configuration")
    async def config_view(self, interaction: discord.Interaction) -> None:
        assert interaction.guild_id is not None
        async with session_scope() as session:
            config = await guild_config_service.get_or_create(session, interaction.guild_id)
        await interaction.response.send_message(embed=embeds.config_view_embed(config))

    @config_group.command(name="admin-roles", description="Add or remove a role from the ledger admin list")
    @app_commands.describe(role="Role to add or remove", action="Add or remove this role")
    async def config_admin_roles(
        self, interaction: discord.Interaction, role: discord.Role, action: Literal["add", "remove"]
    ) -> None:
        assert interaction.guild_id is not None
        if not await self._require_manage_guild(interaction):
            return
        async with session_scope() as session:
            config = await guild_config_service.get_or_create(session, interaction.guild_id)
            role_ids = set(config.admin_role_ids)
            if action == "add":
                role_ids.add(role.id)
            else:
                role_ids.discard(role.id)
            config.admin_role_ids = sorted(role_ids)
            await session.commit()
            await session.refresh(config)
        await interaction.response.send_message(embed=embeds.config_view_embed(config), ephemeral=True)

    @config_group.command(name="user-roles", description="Add or remove a role from the ledger viewer list")
    @app_commands.describe(role="Role to add or remove", action="Add or remove this role")
    async def config_user_roles(
        self, interaction: discord.Interaction, role: discord.Role, action: Literal["add", "remove"]
    ) -> None:
        assert interaction.guild_id is not None
        if not await self._require_manage_guild(interaction):
            return
        async with session_scope() as session:
            config = await guild_config_service.get_or_create(session, interaction.guild_id)
            role_ids = set(config.user_role_ids)
            if action == "add":
                role_ids.add(role.id)
            else:
                role_ids.discard(role.id)
            config.user_role_ids = sorted(role_ids)
            await session.commit()
            await session.refresh(config)
        await interaction.response.send_message(embed=embeds.config_view_embed(config), ephemeral=True)

    @config_group.command(
        name="ledger-channel", description="Set the channel where public entries are posted"
    )
    @app_commands.describe(channel="Channel for public ledger posts")
    async def config_ledger_channel(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ) -> None:
        assert interaction.guild_id is not None
        if not await self._require_manage_guild(interaction):
            return
        async with session_scope() as session:
            config = await guild_config_service.get_or_create(session, interaction.guild_id)
            config.ledger_channel_id = channel.id
            await session.commit()
            await session.refresh(config)
        await interaction.response.send_message(embed=embeds.config_view_embed(config), ephemeral=True)

    @config_group.command(
        name="audit-channel", description="Set the channel where the internal audit log is posted"
    )
    @app_commands.describe(channel="Channel for internal audit-log messages")
    async def config_audit_channel(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ) -> None:
        assert interaction.guild_id is not None
        if not await self._require_manage_guild(interaction):
            return
        async with session_scope() as session:
            config = await guild_config_service.get_or_create(session, interaction.guild_id)
            config.audit_channel_id = channel.id
            await session.commit()
            await session.refresh(config)
        await interaction.response.send_message(embed=embeds.config_view_embed(config), ephemeral=True)

    @config_group.command(
        name="grace-period", description="Set the delay before entries go public (0 disables it)"
    )
    @app_commands.describe(minutes="Minutes to wait before publishing; 0 publishes immediately")
    async def config_grace_period(
        self, interaction: discord.Interaction, minutes: app_commands.Range[int, 0]
    ) -> None:
        assert interaction.guild_id is not None
        if not await self._require_manage_guild(interaction):
            return
        async with session_scope() as session:
            config = await guild_config_service.get_or_create(session, interaction.guild_id)
            config.grace_period_minutes = minutes
            await session.commit()
            await session.refresh(config)
        await interaction.response.send_message(embed=embeds.config_view_embed(config), ephemeral=True)

    @config_group.command(
        name="base-currency", description="Set the currency used for balance/overview totals"
    )
    @app_commands.describe(currency="Base currency")
    async def config_base_currency(self, interaction: discord.Interaction, currency: Currency) -> None:
        assert interaction.guild_id is not None
        if not await self._require_manage_guild(interaction):
            return
        async with session_scope() as session:
            config = await guild_config_service.get_or_create(session, interaction.guild_id)
            config.base_currency = currency
            await session.commit()
            await session.refresh(config)
        await interaction.response.send_message(embed=embeds.config_view_embed(config), ephemeral=True)
