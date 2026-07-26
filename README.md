# Cashualty

A multi-feature Discord bot. Written in Python with [discord.py](https://discordpy.readthedocs.io/),
using Cogs/Extensions as the mechanism for adding independent features over time.

**First feature: `ledger`** — makes a community's cash flow transparent. Record income
(e.g. Ko-fi donations) and expenses (e.g. game servers, cloud hosting) with `/ledger`,
and let the whole server see where the money goes.

## Add Cashualty to your server

If you just want to use the already-running instance (hosted by this project's
maintainer) in your own Discord server, no setup needed — invite it:

**[Add Cashualty to your server](https://discord.com/oauth2/authorize?client_id=1530993618603741349&permissions=92160&integration_type=0&scope=bot+applications.commands)**

Once it's in, run `/ledger config ledger-channel` first — `/ledger add-income` and
`/ledger add-expense` refuse to run until a ledger channel is set, since there'd be
nowhere to post to. Data is fully isolated per server, so your settings/entries never mix
with anyone else's on the same shared instance.

The rest of this README is for anyone who wants to run their **own separate copy**
instead (a different community, a fork, local development, etc.) — you don't need any of
it just to use the invite link above.

## Requirements

- Python 3.11+
- Your own Discord application + bot token from the [Discord Developer Portal](https://discord.com/developers/applications)
  (the invite link above is tied to the maintainer's own application — running your own
  instance means creating your own application and token, not reusing that one)

## Inviting your own instance to a server

In the Developer Portal, under **Installation**: enable **Guild Install** only (not User
Install — the ledger feature is inherently per-server: roles, channels, and settings are
all guild-scoped). Under **OAuth2 → URL Generator**, pick scopes `bot` and
`applications.commands`, and at minimum the `Send Messages`, `Embed Links`, and
`Read Message History` bot permissions (nothing higher — Cashualty does its own
role-based permission checks, it doesn't need `Manage Roles` or `Administrator`). Open the
generated URL and select the server to invite it to.

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
single test guild instead — set `DEV_GUILD_ID` in `.env` (see `.env.example`) to your
test server's ID for instant, guild-scoped syncing. Leave it unset in production.

## Deploying with Docker

On the server (tested against plain Debian + Docker/Docker Compose — this does not go
through Pterodactyl's own container management, just the Docker CLI directly):

```bash
git clone https://github.com/inocent16/Cashualty.git
cd Cashualty
cp .env.example .env
# edit .env: set a real DISCORD_TOKEN, and DATABASE_PATH=/app/data/cashualty.db
# (leave DEV_GUILD_ID unset for production -- global command sync)
mkdir -p data
docker compose up -d --build
```

The container's entrypoint (`docker-entrypoint.sh`) always runs `alembic upgrade head`
before starting the bot, so schema migrations apply automatically on every restart —
never edit `data/cashualty.db` directly, and never restart the container mid-migration
(i.e. don't `docker compose restart` and immediately hard-kill it).

Useful commands:

```bash
docker compose logs -f          # tail bot output
docker compose down              # stop
git pull && docker compose up -d --build   # deploy an update
```

The SQLite database lives in `./data/` on the host (bind-mounted into the container), so
it survives image rebuilds. Back that directory up if you care about the ledger history.

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

### Commands

| Command | Who | What |
|---|---|---|
| `/ledger add-income` | admin | Record an income entry |
| `/ledger add-expense` | admin | Record an expense entry |
| `/ledger cancel` | admin | Hard-delete an entry — only while it's still pending (not yet public) |
| `/ledger edit` | admin | Change a pending entry in place, or open a correction if it's already public |
| `/ledger correction` | admin | Add an explicit signed adjustment to an already-public entry |
| `/ledger balance` | admin or user role | Current net balance in the guild's base currency |
| `/ledger overview` | admin or user role | Income/expense/net breakdown for `week`/`month`/`year`, plus owner-covered shortfall if applicable |
| `/ledger config view` | anyone | Show current settings — always public, no permission check |
| `/ledger config admin-roles` | Manage Server | Add/remove a role from the admin list |
| `/ledger config user-roles` | Manage Server | Add/remove a role from the viewer list |
| `/ledger config ledger-channel` | Manage Server | Set where public entries get posted (required before recording anything) |
| `/ledger config audit-channel` | Manage Server | **Not implemented yet** — see note below |
| `/ledger config grace-period` | Manage Server | Minutes before an entry publishes; `0` publishes immediately |
| `/ledger config base-currency` | Manage Server | Currency used for `/ledger balance` / `/ledger overview` totals |

> **`audit-channel` is currently a dead setting.** Every create/cancel/correction is
> recorded in the internal `ledger_audit_log` database table (so "still visible in the
> logs" holds even for entries hard-deleted pre-publish), but nothing yet posts those
> events into the configured Discord channel. Setting it today has no visible effect.

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

**Locale note:** Discord's number-input parsing depends on the client's language, not the
bot. In English-locale clients, `.` is the decimal separator (`25.50`). In German (and
other) locales, `.` is a *thousands* separator, so typing `25.50` is parsed as `2550`
before it ever reaches the bot — use a comma (`25,50`) instead. This is entirely a Discord
client behavior; there's nothing the bot can do to detect or correct it.

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
