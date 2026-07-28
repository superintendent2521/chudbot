"""Per-guild voice session lifecycle."""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Optional

from interactions import Client

from music_errors import MusicError


class GuildMusicSession:
    """Own the Discord voice connection and idle timer for one guild."""

    def __init__(self, guild_id: int, runtime: Any, cleanup_callback: Callable[[int], None]) -> None:
        self.guild_id = guild_id
        self.runtime = runtime
        self.idle_task: Optional[asyncio.Task] = None
        self._cleanup_callback = cleanup_callback
        self._client: Optional[Client] = None
        self._channel_id: Optional[int] = None

    async def ensure_connected(self, channel: Any) -> None:
        client = getattr(channel, "_client", None)
        if not client:
            self.runtime.logger.error(
                "Voice channel %s in guild %s is missing a client reference",
                getattr(channel, "id", "unknown"),
                self.guild_id,
            )
            raise MusicError("I couldn't figure out how to join that voice chat. Please try again.")

        self._client = client
        target_id = int(channel.id)
        player = self.runtime.manager.get_player(self.guild_id)
        player.channel_id = target_id
        if self._channel_id == target_id and bool(getattr(player, "is_connected", False)):
            return

        self.runtime.logger.info("Requesting voice connection to channel %s in guild %s", target_id, self.guild_id)
        await self.runtime.issue_voice_state_update(client, self.guild_id, target_id, deafened=False)
        self._channel_id = target_id

    async def reconnect_voice_state(self) -> None:
        if not self._client or self._channel_id is None:
            return

        self.runtime.logger.warning(
            "Refreshing voice connection for guild %s after Lavalink reconnect",
            self.guild_id,
        )
        await self.runtime.issue_voice_state_update(self._client, self.guild_id, None, deafened=False)
        await self.runtime.issue_voice_state_update(
            self._client,
            self.guild_id,
            self._channel_id,
            deafened=False,
        )

    async def disconnect(self) -> None:
        self.cancel_idle_timer()
        if self._client and self._channel_id is not None:
            try:
                await self.runtime.issue_voice_state_update(self._client, self.guild_id, None, deafened=False)
            except Exception as error:
                self.runtime.logger.warning(
                    "Failed to disconnect voice session in guild %s: %s",
                    self.guild_id,
                    error,
                )
        self._channel_id = None
        self._client = None
        self.runtime.voice_channel_ids.pop(self.guild_id, None)
        self.runtime.voice_session_ids.pop(self.guild_id, None)
        lavalink_client = self.runtime.get_lavalink_client()
        if lavalink_client:
            player = lavalink_client.player_manager.get(self.guild_id)
            if player:
                player.channel_id = None
                try:
                    await player.stop()
                except Exception:
                    pass
                lavalink_client.player_manager.remove(self.guild_id)
        self._cleanup_callback(self.guild_id)

    def start_idle_timer(self) -> None:
        if self.idle_task and not self.idle_task.done():
            return
        self.idle_task = asyncio.create_task(self._disconnect_when_idle())

    def cancel_idle_timer(self) -> None:
        if self.idle_task and not self.idle_task.done():
            current = asyncio.current_task()
            if current is not self.idle_task:
                self.idle_task.cancel()
        self.idle_task = None

    async def _disconnect_when_idle(self) -> None:
        try:
            try:
                await asyncio.sleep(self.runtime.idle_timeout)
            except asyncio.CancelledError:
                return

            player = None
            lavalink_client = self.runtime.get_lavalink_client()
            if lavalink_client:
                player = lavalink_client.player_manager.get(self.guild_id)

            if player and (player.is_playing or player.queue):
                self.runtime.logger.info(
                    "Idle timer aborted for guild %s because playback resumed.",
                    self.guild_id,
                )
                return

            await self.disconnect()
        finally:
            self.idle_task = None
