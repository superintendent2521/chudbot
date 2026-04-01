"""Delete non-webhook messages from #github channels."""

from __future__ import annotations

import inspect
import logging
import os
from typing import Any, Iterable, Optional, Set, Tuple

from interactions import listen
from interactions.api.events import WebsocketReady
from interactions.api.events.discord import MessageCreate

GITHUB_CHANNEL_NAME = "github"


def _parse_optional_int(raw_value: str) -> Optional[int]:
    raw_value = raw_value.strip()
    if not raw_value:
        return None
    digits = "".join(ch for ch in raw_value if ch.isdigit())
    if not digits:
        return None
    return int(digits)


def _snowflake_to_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    candidate = getattr(value, "id", None)
    if candidate is not None:
        try:
            return int(candidate)
        except (TypeError, ValueError):
            return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_github_channel(channel: Any) -> bool:
    return str(getattr(channel, "name", "")).strip().lower() == GITHUB_CHANNEL_NAME


def _is_allowed_github_message(message: Any, allowed_bot_id: Optional[int]) -> bool:
    if getattr(message, "webhook_id", None):
        return True
    author = getattr(message, "author", None)
    author_id = _snowflake_to_int(author)
    return allowed_bot_id is not None and author_id == allowed_bot_id


async def _await_if_needed(result: Any) -> Any:
    if inspect.isawaitable(result):
        return await result
    return result


async def _delete_message(message: Any, logger: logging.Logger) -> None:
    delete_method = getattr(message, "delete", None)
    if not callable(delete_method):
        return
    try:
        await _await_if_needed(delete_method())
    except Exception as error:
        logger.warning("Failed to delete message %s from #github: %s", getattr(message, "id", "?"), error)


async def _iter_channel_messages(channel: Any, logger: logging.Logger):
    fetch_messages = getattr(channel, "fetch_messages", None)
    if callable(fetch_messages):
        before = None
        while True:
            try:
                kwargs = {"limit": 100}
                if before is not None:
                    kwargs["before"] = before
                batch = await fetch_messages(**kwargs)
            except TypeError:
                try:
                    batch = await fetch_messages(limit=100)
                except Exception as error:
                    logger.warning("Unable to fetch #github history for channel %s: %s", getattr(channel, "id", "?"), error)
                    return
            except Exception as error:
                logger.warning("Unable to fetch #github history for channel %s: %s", getattr(channel, "id", "?"), error)
                return

            if not batch:
                return

            for message in batch:
                yield message

            if len(batch) < 100:
                return
            before = getattr(batch[-1], "id", None)
            if before is None:
                return
        return

    history = getattr(channel, "history", None)
    if callable(history):
        try:
            result = history(limit=500)
            resolved = await _await_if_needed(result)
            if hasattr(resolved, "__aiter__"):
                async for message in resolved:
                    yield message
            elif isinstance(resolved, Iterable):
                for message in resolved:
                    yield message
        except Exception as error:
            logger.warning("Unable to read #github history for channel %s: %s", getattr(channel, "id", "?"), error)


def _iter_cached_channels(client: Any) -> Iterable[Any]:
    cache = getattr(client, "cache", None)
    channel_cache = getattr(cache, "channel_cache", None)
    if isinstance(channel_cache, dict):
        return channel_cache.values()
    if hasattr(channel_cache, "values"):
        return channel_cache.values()
    return []


def _iter_github_channels(client: Any) -> Iterable[Any]:
    seen: Set[int] = set()
    for channel in _iter_cached_channels(client):
        channel_id = _snowflake_to_int(channel)
        if channel_id is None or channel_id in seen or not _is_github_channel(channel):
            continue
        seen.add(channel_id)
        yield channel

    guilds = getattr(client, "guilds", None) or []
    for guild in guilds:
        channels = getattr(guild, "channels", None) or []
        for channel in channels:
            channel_id = _snowflake_to_int(channel)
            if channel_id is None or channel_id in seen or not _is_github_channel(channel):
                continue
            seen.add(channel_id)
            yield channel


def create_github_channel_guard(logger: logging.Logger) -> Tuple:
    allowed_bot_id = _parse_optional_int(os.getenv("GITHUB_WEBHOOK_BOT_ID", ""))

    async def enforce_message(message: Any) -> None:
        if not message:
            return
        if _is_allowed_github_message(message, allowed_bot_id):
            return
        channel = getattr(message, "channel", None)
        if channel is None and getattr(message, "channel_id", None) and getattr(message, "_client", None):
            try:
                channel = await message._client.fetch_channel(int(message.channel_id))
            except Exception:
                channel = None
        if not channel or not _is_github_channel(channel):
            return
        await _delete_message(message, logger)

    @listen(MessageCreate)
    async def on_message_create(event: MessageCreate):
        try:
            await enforce_message(getattr(event, "message", None))
        except Exception as error:
            logger.error("Failed to enforce #github webhook-only messages: %s", error, exc_info=True)

    @listen(WebsocketReady)
    async def on_ready(event: WebsocketReady):
        client = getattr(event, "bot", None) or getattr(event, "client", None)
        if client is None:
            return

        try:
            for channel in _iter_github_channels(client):
                async for message in _iter_channel_messages(channel, logger):
                    if _is_allowed_github_message(message, allowed_bot_id):
                        continue
                    await _delete_message(message, logger)
        except Exception as error:
            logger.error("Failed to sweep #github channels on startup: %s", error, exc_info=True)

    return (on_message_create, on_ready)
