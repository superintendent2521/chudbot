"""Voice channel logging listeners."""

from __future__ import annotations

import json
import logging
from typing import Awaitable, Callable, Optional, Set, Tuple

from interactions import listen
from interactions.api.events.discord import VoiceUserJoin, VoiceUserLeave

from chudbot.storage.guild_channels import GuildChannelStore


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

    def __init__(self, path: str, logger: logging.Logger, ignored_path: Optional[str] = None) -> None:
        super().__init__(path, logger)
        self.ignored_path = ignored_path
        self.ignored_channels: dict[int, Set[int]] = {}
        self._load_ignored_channels()

    def _load_ignored_channels(self) -> None:
        if not self.ignored_path:
            return
        try:
            with open(self.ignored_path, "r", encoding="utf-8") as file:
                raw = json.load(file)
        except FileNotFoundError:
            return
        except json.JSONDecodeError as error:
            self.logger.error("Failed to parse ignored voice channel file %s: %s", self.ignored_path, error)
            return
        if not isinstance(raw, dict):
            return
        for raw_guild_id, raw_channel_ids in raw.items():
            if not isinstance(raw_channel_ids, list):
                continue
            try:
                guild_id = int(raw_guild_id)
                self.ignored_channels[guild_id] = {int(channel_id) for channel_id in raw_channel_ids}
            except (TypeError, ValueError):
                self.logger.warning("Ignoring malformed ignored voice channel entry %s", raw_guild_id)

    def _save_ignored_channels(self) -> None:
        if not self.ignored_path:
            return
        try:
            with open(self.ignored_path, "w", encoding="utf-8") as file:
                json.dump(
                    {str(guild_id): sorted(channel_ids) for guild_id, channel_ids in self.ignored_channels.items()},
                    file,
                    indent=2,
                )
        except Exception as error:
            self.logger.error("Failed to persist ignored voice channel data: %s", error)

    def set_channel_logging(self, guild_id: int, channel_id: int, enabled: bool) -> None:
        channels = self.ignored_channels.setdefault(guild_id, set())
        if enabled:
            channels.discard(channel_id)
        else:
            channels.add(channel_id)
        if not channels:
            self.ignored_channels.pop(guild_id, None)
        self._save_ignored_channels()

    def is_channel_logging_enabled(self, guild_id: int, channel_id: Optional[int]) -> bool:
        # If Discord gives us no source channel, keep the old logging behavior;
        # only an explicitly configured channel should be suppressed.
        return channel_id is None or channel_id not in self.ignored_channels.get(guild_id, set())


def create_music_leave_logger(store: VoiceLogStore, logger: logging.Logger) -> Callable[[object, int, int], Awaitable[None]]:
    """Create the fallback used when Discord does not emit a normal leave event for the bot."""

    async def log_music_leave(client, guild_id: int, channel_id: int) -> None:
        log_channel_id = store.get_channel_id(guild_id)
        if not log_channel_id or not store.is_channel_logging_enabled(guild_id, channel_id):
            return
        log_channel = await _get_sendable_channel(client, log_channel_id, logger)
        if not log_channel:
            return
        source_channel = client.cache.get_channel(channel_id)
        channel_name = getattr(source_channel, "name", f"<#{channel_id}>")
        username = getattr(getattr(client, "user", None), "username", "Brunk")
        await log_channel.send(f"🔇 **{username}** left **{channel_name}**")

    return log_music_leave


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
        if not log_channel_id or not store.is_channel_logging_enabled(guild_id, _snowflake_to_int(event.channel)):
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
        if not log_channel_id or not store.is_channel_logging_enabled(guild_id, _snowflake_to_int(event.channel)):
            return
        channel = await _get_sendable_channel(event.client, log_channel_id, logger)
        if not channel:
            logger.warning(f"Cannot log leave: channel {log_channel_id} not found or is not sendable.")
            return
        await channel.send(f"🔇 **{event.author.username}** left **{event.channel.name}**")

    return on_voice_join, on_voice_leave
