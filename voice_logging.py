"""Voice channel logging listeners."""

from __future__ import annotations

import logging
from typing import Tuple

from interactions import listen
from interactions.api.events.discord import VoiceUserJoin, VoiceUserLeave


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


def create_voice_logging_listeners(log_channel_id: int, logger: logging.Logger) -> Tuple:
    @listen(VoiceUserJoin)
    async def on_voice_join(event: VoiceUserJoin):
        channel = await _get_sendable_channel(event.client, log_channel_id, logger)
        if not channel:
            print(f"�s��,? Cannot log join: channel {log_channel_id} not found.")
            return
        await channel.send(
            f"dYZT�,? **{event.author.username}** joined **{event.channel.name}**"
        )

    @listen(VoiceUserLeave)
    async def on_voice_leave(event: VoiceUserLeave):
        channel = await _get_sendable_channel(event.client, log_channel_id, logger)
        if not channel:
            print(f"�s��,? Cannot log leave: channel {log_channel_id} not found.")
            return
        await channel.send(
            f"�?O **{event.author.username}** left **{event.channel.name}**"
        )

    return on_voice_join, on_voice_leave

