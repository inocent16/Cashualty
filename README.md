# Cashualty

A multi-feature Discord bot. Written in Python with [discord.py](https://discordpy.readthedocs.io/),
using Cogs/Extensions as the mechanism for adding independent features over time.

**First feature: `ledger`** — makes a community's cash flow transparent. Record income
(e.g. Ko-fi donations) and expenses (e.g. game servers, cloud hosting) with `/ledger`,
and let the whole server see where the money goes.

## Requirements

- Python 3.11+
- A Discord application + bot token from the [Discord Developer Portal](https://discord.com/developers/applications)

## Setup

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows; use `source .venv/bin/activate` on macOS/Linux
pip install -e ".[dev]"
copy .env.example .env        # then fill in DISCORD_TOKEN
alembic upgrade head
python -m cashualty
```

Slash commands sync automatically every time the bot starts (`setup_hook` calls
`bot.tree.sync()`), so there's no separate "deploy commands" step. Global syncs can take
up to an hour to show up everywhere; while developing, it's often faster to sync to a
single test guild instead (see discord.py's `CommandTree.copy_global_to` /
guild-scoped `sync(guild=...)` if you want that).

## Project layout

```
cashualty/
  core/               # bot bootstrap, shared DB engine, permission primitives -- feature-agnostic
  features/
    ledger/           # the cash-transparency feature (this repo's first feature)
      cog.py          # all /ledger slash commands
      models.py       # SQLAlchemy models (tables prefixed ledger_)
      services/       # business logic: transactions, currency conversion, scheduler, permissions
      utils/          # embeds, money formatting
migrations/           # Alembic migration history
tests/                # pytest unit tests for the trickiest logic (currency, correction state machine)
```

Adding a second, unrelated feature later: create `cashualty/features/<name>/` with its own
`models.py` (pick a distinct table prefix) and its own `Cog`, then add one
`"cashualty.features.<name>"` line to `FEATURE_EXTENSIONS` in
[`cashualty/core/bot.py`](cashualty/core/bot.py). Nothing in `core/` or `features/ledger/`
needs to change, and `commands.GroupCog` gives the new feature its own top-level slash
command for free.

## The `ledger` feature

### How "public" works

Every `/ledger add-income` / `/ledger add-expense` entry can sit in a configurable grace
window (`/ledger config grace-period`, default 10 minutes) before it's posted to the
public ledger channel. Within that window:

- `/ledger cancel` **hard-deletes** the entry outright (it was never shown publicly);
  still recorded in the internal audit log.
- `/ledger edit` mutates the entry in place.

Once an entry is public (grace period elapsed, or immediately if the grace period is set
to `0`), it can never be silently changed or deleted:

- `/ledger edit` on a public entry computes the difference between old and new values and
  posts a linked **correction** instead of rewriting the original.
- `/ledger correction` does the same thing more directly, for adjustments that aren't "I
  mistyped it" (e.g. a later partial refund) — you state the adjustment (positive or
  negative) rather than the new absolute values.

The original entry is never deleted or hidden once public; corrections always point back
to it, so the full history stays visible.

### Permissions

Two configurable role lists per server:

- **Admin roles** (`/ledger config admin-roles`) — can add/cancel/edit/correct entries.
  Discord's real "Manage Server" permission always works too, so a real admin can never be
  locked out.
- **User roles** (`/ledger config user-roles`) — can view `/ledger balance` and
  `/ledger overview`. Until configured, these commands are admin-only — never open to
  `@everyone` by default.

`/ledger config view` is public with no permission check: anyone can see the current
settings (including whether a grace period is active and how long it is), since
configuration transparency is part of the point.

### Currency

EUR and USD are supported. Each entry is stored in the currency it was entered in; a
guild-wide base currency (`/ledger config base-currency`) is used for `/ledger balance`
and `/ledger overview` totals, converted using daily-cached rates from
[Frankfurter.app](https://www.frankfurter.app/) (free, no API key, ECB-based). If a live
rate fetch fails, the last cached rate is used instead.

### Owner-covered shortfall

`/ledger overview` shows income, expenses, net, and — if expenses exceeded income for that
period — a derived "owner-covered shortfall" (`max(0, expenses - income)`). It's never
stored as its own transaction, so it can't be double-counted; it's always computed fresh
from the real entries.

### Ko-fi

Not implemented yet. The schema reserves a `source` column (`manual` | `kofi`) on
transactions so webhook ingestion can be added later without reshaping existing data.

## Running tests

```bash
pytest
```

## Database migrations

This project uses Alembic. After changing `cashualty/features/*/models.py`:

```bash
alembic revision --autogenerate -m "describe the change"
alembic upgrade head
```
