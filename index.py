"""Compatibility entry point for running the bot from the repository root."""

import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(
        asyncio.WindowsSelectorEventLoopPolicy()
    )

from chudbot.app import main


if __name__ == "__main__":
    main()