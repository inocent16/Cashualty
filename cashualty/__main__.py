from __future__ import annotations

import asyncio
import logging

from cashualty.config import settings
from cashualty.core.bot import Cashualty


def main() -> None:
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    bot = Cashualty()
    asyncio.run(bot.start(settings.discord_token))


if __name__ == "__main__":
    main()
