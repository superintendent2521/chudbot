"""Voice channel logging listeners."""

from __future__ import annotations

import logging
from typing import Optional, Tuple

from interactions import listen
from interactions.api.events.discord import VoiceUserJoin, VoiceUserLeave

from guild_channel_store import GuildChannelStore


def _snowflake_to_int(value) -> Optional[int]:
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


class VoiceLogStore(GuildChannelStore):
    """Backward-compatible alias for the shared guild channel store."""


async def _get_sendable_channel(client, channel_id: int, logger: logging.Logger):
    channel = client.cache.get_channel(channel_id)
    if not channel:
        try:
            channel = await client.fetch_channel(channel_id)
        except Exception as error:
            logger.warning(
                "Unable to fetch channel %s: %s",
                channel_id,
                error,
            )
            return None
    if not getattr(channel, "send", None):
        logger.warning(
            "Channel %s (%s) does not allow sending messages",
            channel_id,
            channel.__class__.__name__,
        )
        return None
    return channel


def _get_guild_id_from_event(event) -> Optional[int]:
    for candidate in (
        getattr(event, "guild_id", None),
        getattr(getattr(event, "guild", None), "id", None),
        getattr(getattr(event, "channel", None), "guild_id", None),
        getattr(getattr(event, "channel", None), "guild", None),
    ):
        guild_id = _snowflake_to_int(candidate)
        if guild_id:
            return guild_id
    return None


def create_voice_logging_listeners(store: VoiceLogStore, logger: logging.Logger) -> Tuple:
    @listen(VoiceUserJoin)
    async def on_voice_join(event: VoiceUserJoin):
        guild_id = _get_guild_id_from_event(event)
        if not guild_id:
            logger.warning("Cannot log join: missing guild id in event.")
            return
        log_channel_id = store.get_channel_id(guild_id)
        if not log_channel_id:
            return
        channel = await _get_sendable_channel(event.client, log_channel_id, logger)
        if not channel:
            logger.warning(f"Cannot log join: channel {log_channel_id} not found or is not sendable.")
            return
        await channel.send(f"🔊 **{event.author.username}** joined **{event.channel.name}**")

    @listen(VoiceUserLeave)
    async def on_voice_leave(event: VoiceUserLeave):
        guild_id = _get_guild_id_from_event(event)
        if not guild_id:
            logger.warning("Cannot log leave: missing guild id in event.")
            return
        log_channel_id = store.get_channel_id(guild_id)
        if not log_channel_id:
            return
        channel = await _get_sendable_channel(event.client, log_channel_id, logger)
        if not channel:
            logger.warning(f"Cannot log leave: channel {log_channel_id} not found or is not sendable.")
            return
        await channel.send(f"🔇 **{event.author.username}** left **{event.channel.name}**")

    return on_voice_join, on_voice_leave
