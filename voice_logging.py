"""Voice channel logging listeners."""

from __future__ import annotations

import json
import logging
from typing import Dict, Optional, Tuple

from interactions import listen
from interactions.api.events.discord import VoiceUserJoin, VoiceUserLeave


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


class VoiceLogStore:
    def __init__(
        self,
        path: str,
        logger: logging.Logger,
        *,
        default_channel_id: Optional[int] = None,
    ) -> None:
        self.path = path
        self.logger = logger
        self.default_channel_id = default_channel_id
        self.entries: Dict[int, int] = {}
        self.load()

    def load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as file:
                raw = json.load(file)
        except FileNotFoundError:
            self.entries = {}
            return
        except json.JSONDecodeError as error:
            self.logger.error("Failed to parse voice log file %s: %s", self.path, error)
            self.entries = {}
            return

        loaded: Dict[int, int] = {}
        if isinstance(raw, dict):
            for raw_guild_id, raw_channel_id in raw.items():
                try:
                    guild_id = int(raw_guild_id)
                    channel_id = int(raw_channel_id)
                except (TypeError, ValueError):
                    self.logger.warning("Ignoring malformed voice log entry %s", raw_guild_id)
                    continue
                loaded[guild_id] = channel_id
        self.entries = loaded

    def save(self) -> None:
        serializable = {str(guild_id): channel_id for guild_id, channel_id in self.entries.items()}
        try:
            with open(self.path, "w", encoding="utf-8") as file:
                json.dump(serializable, file, indent=2)
        except Exception as error:
            self.logger.error("Failed to persist voice log data: %s", error)

    def set_channel(self, guild_id: int, channel_id: int) -> None:
        self.entries[guild_id] = channel_id
        self.save()

    def clear_channel(self, guild_id: int) -> None:
        if guild_id in self.entries:
            self.entries.pop(guild_id)
            self.save()

    def get_channel_id(self, guild_id: int) -> Optional[int]:
        return self.entries.get(guild_id, self.default_channel_id)


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
