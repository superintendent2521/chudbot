"""Guild session, player, track, and filter coordination."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

import lavalink

from chudbot.music.errors import MusicError
from chudbot.music.filters import AudioNormalization
from chudbot.music.session import GuildMusicSession
from chudbot.music.tracks import TrackLoader


class MusicManager:
    """Coordinate per-guild state while delegating specialized behavior."""

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime
        self.sessions: Dict[int, GuildMusicSession] = {}
        self.track_loader = TrackLoader(runtime, self.sessions)

    def get_session(self, guild_id: int) -> GuildMusicSession:
        session = self.sessions.get(guild_id)
        if session:
            return session
        session = GuildMusicSession(guild_id, self.runtime, self._cleanup_session)
        self.sessions[guild_id] = session
        return session

    def _cleanup_session(self, guild_id: int) -> None:
        self.sessions.pop(guild_id, None)

    def active_session(self, guild_id: int) -> Optional[GuildMusicSession]:
        return self.sessions.get(guild_id)

    def get_player(self, guild_id: int) -> Any:
        client = self.runtime.get_lavalink_client()
        if not client:
            raise MusicError("Music playback isn't configured.")
        try:
            return client.player_manager.create(guild_id)
        except lavalink.errors.ClientError as error:
            if self.runtime._is_no_available_nodes_error(error):
                self.runtime.trigger_no_available_nodes_reconnect("creating player")
                raise MusicError("Lavalink is connected, but no node is ready yet. Try again in a few seconds.")
            raise

    async def ensure_audio_normalization(self, player: Any) -> None:
        runtime = self.runtime
        if not runtime.audio_normalization or runtime._normalization_unavailable:
            return
        if player.fetch("audio_normalization_applied", False):
            return

        normalizer = AudioNormalization(
            max_amplitude=runtime.normalization_max_amplitude,
            adaptive=True,
        )
        try:
            await player.set_filter(normalizer)
        except Exception as error:
            filters = getattr(player, "filters", None)
            if isinstance(filters, dict):
                filters.pop(type(normalizer).__name__.lower(), None)
            runtime._normalization_unavailable = True
            runtime.logger.warning(
                "Audio normalization is enabled, but the Lavalink node rejected the "
                "LavaDSPX normalization filter. Playback will continue without it: %s",
                error,
            )
            return

        player.store("audio_normalization_applied", True)
        runtime.logger.info(
            "Enabled audio normalization for guild %s (max amplitude %.2f)",
            player.guild_id,
            runtime.normalization_max_amplitude,
        )

    async def wait_for_player_connection(self, guild_id: int) -> None:
        player = self.get_player(guild_id)
        deadline = asyncio.get_running_loop().time() + self.runtime.voice_connect_timeout
        while asyncio.get_running_loop().time() < deadline:
            if bool(getattr(player, "is_connected", False)):
                return
            await asyncio.sleep(0.25)

        raise MusicError("I joined voice chat, but Lavalink never finished connecting to Discord voice.")

    async def load_tracks(self, query: str, guild_id: Optional[int] = None) -> lavalink.LoadResult:
        return await self.track_loader.load_tracks(query, guild_id)

    async def schedule_idle(self, guild_id: int) -> None:
        session = self.sessions.get(guild_id)
        if session:
            session.start_idle_timer()

    def cancel_idle(self, guild_id: int) -> None:
        session = self.sessions.get(guild_id)
        if session:
            session.cancel_idle_timer()
