"""Silent, buffered economy rewards for guild messages."""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

from interactions import listen
from interactions.api.events.discord import MessageCreate

from chudbot.economy.reward_buffer import MessageRewardBuffer


MESSAGE_BATCH_SIZE = 10
MIN_REWARD_MILLI = 100
MAX_REWARD_MILLI = 400
REWARD_QUEUE_SIZE = 1_000


def _snowflake(value: Any) -> int | None:
    candidate = getattr(value, "id", value)
    try:
        return None if candidate is None else int(candidate)
    except (TypeError, ValueError):
        return None


def create_economy_message_reward_listener(store: Any, logger: logging.Logger):
    """Create a listener that writes one rounded reward after every ten messages."""
    rewards = MessageRewardBuffer(MESSAGE_BATCH_SIZE)
    rng = random.SystemRandom()
    deposits: asyncio.Queue[tuple[int, int, int, int]] = asyncio.Queue(
        maxsize=REWARD_QUEUE_SIZE
    )
    worker_task: asyncio.Task[None] | None = None

    async def deposit_rewards() -> None:
        while True:
            guild_id, user_id, reward, batch_milli_coins = await deposits.get()
            try:
                await store.credit_message_reward(guild_id, user_id, reward)
            except Exception:
                rewards.restore(guild_id, user_id, MESSAGE_BATCH_SIZE, batch_milli_coins)
                logger.exception("Unable to deposit buffered message economy reward")
            finally:
                deposits.task_done()

    def ensure_worker() -> None:
        nonlocal worker_task
        if worker_task is None or worker_task.done():
            worker_task = asyncio.create_task(
                deposit_rewards(), name="message-reward-writer"
            )

    @listen(MessageCreate)
    async def on_economy_message(event: MessageCreate):
        message = getattr(event, "message", None)
        author = getattr(message, "author", None)
        if message is None or author is None or getattr(author, "bot", False):
            return

        guild_id = _snowflake(getattr(message, "_guild_id", None))
        if guild_id is None:
            guild_id = _snowflake(getattr(message, "guild_id", None))
        if guild_id is None:
            guild_id = _snowflake(getattr(message, "guild", None))
        user_id = _snowflake(author)
        if guild_id is None or user_id is None:
            return

        milli_coins = rng.randint(MIN_REWARD_MILLI, MAX_REWARD_MILLI)
        deposit = rewards.add(guild_id, user_id, milli_coins)
        if deposit is None:
            return
        reward, batch_milli_coins = deposit
        ensure_worker()
        try:
            deposits.put_nowait((guild_id, user_id, reward, batch_milli_coins))
        except asyncio.QueueFull:
            rewards.restore(guild_id, user_id, MESSAGE_BATCH_SIZE, batch_milli_coins)
            logger.warning("Message reward queue is full; restored reward batch")

    return (on_economy_message,)
