"""Music runtime utilities and listeners."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

import lavalink
from interactions import Client, Member, SlashContext, listen
from interactions.api.events import RawGatewayEvent, WebsocketReady


class MusicError(Exception):
    """Raised when the music subsystem encounters an issue."""


class MusicRuntime:
    def __init__(
        self,
        *,
        logger: logging.Logger,
        lavalink_host: str,
        lavalink_port: Optional[int],
        lavalink_password: str,
        lavalink_region: str,
        lavalink_ssl: bool,
        music_available: bool,
        music_dj_role_id: Optional[int],
        idle_timeout: int,
        voice_connect_timeout: int,
        default_player_volume: int,
    ) -> None:
        self.logger = logger
        self.lavalink_host = lavalink_host
        self.lavalink_port = lavalink_port
        self.lavalink_password = lavalink_password
        self.lavalink_region = lavalink_region
        self.lavalink_ssl = lavalink_ssl
        self.music_available = music_available
        self.music_dj_role_id = music_dj_role_id
        self.idle_timeout = idle_timeout
        self.voice_connect_timeout = voice_connect_timeout
        self.default_player_volume = default_player_volume
        self.lavalink_client: Optional[lavalink.Client] = None
        self.manager = MusicManager(self)

    def get_lavalink_client(self) -> Optional[lavalink.Client]:
        return self.lavalink_client

    def lavalink_ready(self) -> bool:
        return self.music_available and self.lavalink_client is not None

    async def require_lavalink(self, ctx: SlashContext) -> bool:
        if self.lavalink_ready():
            return True
        await ctx.send(
            "Music playback isn't configured. Set the Lavalink environment variables and restart the bot.",
            ephemeral=True,
        )
        return False

    async def require_music_permission(self, ctx: SlashContext) -> bool:
        if self.has_music_control(ctx.author):
            return True
        await ctx.send("You can't use music commands while holding the blocked DJ role.", ephemeral=True)
        return False

    def has_music_control(self, member: Member) -> bool:
        if self.music_dj_role_id is None:
            return True
        try:
            return all(role.id != self.music_dj_role_id for role in getattr(member, "roles", []))
        except Exception:
            return True

    def get_voice_channel(self, member: Member):
        voice_state = getattr(member, "voice", None)
        if voice_state and getattr(voice_state, "channel", None):
            return voice_state.channel
        return None

    @staticmethod
    def format_duration(duration_ms: Optional[int]) -> str:
        if duration_ms is None or duration_ms <= 0:
            return "LIVE"
        seconds = duration_ms // 1000
        minutes, seconds = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes}:{seconds:02d}"

    @staticmethod
    def format_bytes(num_bytes: Optional[int]) -> str:
        value = float(max(num_bytes or 0, 0))
        units = ["B", "KiB", "MiB", "GiB", "TiB"]
        for unit in units:
            if value < 1024 or unit == units[-1]:
                if unit == "B":
                    return f"{int(value)} {unit}"
                return f"{value:.1f} {unit}"
            value /= 1024
        return "0 B"

    @staticmethod
    def format_uptime(uptime_ms: Optional[int]) -> str:
        total_seconds = max(int((uptime_ms or 0) // 1000), 0)
        days, remainder = divmod(total_seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, seconds = divmod(remainder, 60)
        parts = []
        if days:
            parts.append(f"{days}d")
        if hours or days:
            parts.append(f"{hours}h")
        if minutes or hours or days:
            parts.append(f"{minutes}m")
        parts.append(f"{seconds}s")
        return " ".join(parts)

    async def issue_voice_state_update(
        self,
        client: Client,
        guild_id: int,
        channel_id: Optional[int],
        *,
        deafened: bool = False,
    ) -> None:
        if not client.user:
            raise MusicError("Bot user is not ready yet. Try again in a moment.")

        expected_guild = str(guild_id)
        expected_user = str(client.user.id)
        expected_channel = str(channel_id) if channel_id is not None else None

        def _state_check(event: RawGatewayEvent) -> bool:
            data = event.data if isinstance(event.data, dict) else None
            if not data:
                return False
            if str(data.get("guild_id")) != expected_guild:
                return False
            if str(data.get("user_id")) != expected_user:
                return False
            current_channel = data.get("channel_id")
            if expected_channel is None:
                return current_channel is None
            return str(current_channel) == expected_channel

        def _server_check(event: RawGatewayEvent) -> bool:
            data = event.data if isinstance(event.data, dict) else None
            return bool(data and str(data.get("guild_id")) == expected_guild)

        state_waiter = asyncio.create_task(
            client.wait_for(
                "raw_voice_state_update",
                checks=_state_check,
                timeout=self.voice_connect_timeout,
            )
        )
        server_waiter: Optional[asyncio.Task] = None
        if channel_id is not None:
            server_waiter = asyncio.create_task(
                client.wait_for(
                    "raw_voice_server_update",
                    checks=_server_check,
                    timeout=self.voice_connect_timeout,
                )
            )

        try:
            await client._connection_state.gateway.voice_state_update(
                guild_id=guild_id,
                channel_id=channel_id,
                muted=False,
                deafened=deafened,
            )
            await state_waiter
            if server_waiter:
                await server_waiter
        finally:
            for waiter in (state_waiter, server_waiter):
                if waiter and not waiter.done():
                    waiter.cancel()

    async def _forward_voice_event(self, event_name: str, data: dict) -> None:
        if not self.lavalink_ready() or not isinstance(data, dict):
            return

        payload = {"t": event_name, "d": data}

        if event_name == "VOICE_STATE_UPDATE":
            self.logger.info(
                "Forwarding %s for guild %s (channel=%s, session=%s)",
                event_name,
                data.get("guild_id"),
                data.get("channel_id"),
                data.get("session_id"),
            )
        else:
            self.logger.info(
                "Forwarding %s for guild %s (endpoint=%s)",
                event_name,
                data.get("guild_id"),
                data.get("endpoint"),
            )

        try:
            assert self.lavalink_client is not None
            await self.lavalink_client.voice_update_handler(payload)  # type: ignore[arg-type]
        except Exception as error:
            self.logger.error("Error forwarding %s to Lavalink: %s", event_name, error)

    async def handle_raw_voice_state(self, event: RawGatewayEvent) -> None:
        if not self.lavalink_ready():
            return

        data = event.data if isinstance(event.data, dict) else None
        if not data:
            self.logger.debug("Ignored VOICE_STATE_UPDATE because payload was %r", event.data)
            return

        target_user = str(getattr(self.lavalink_client, "_user_id", "")) if self.lavalink_client else ""
        if target_user and str(data.get("user_id")) != target_user:
            self.logger.debug(
                "Skipping VOICE_STATE_UPDATE for guild %s (user %s != bot %s)",
                data.get("guild_id"),
                data.get("user_id"),
                target_user,
            )
            return

        await self._forward_voice_event("VOICE_STATE_UPDATE", data)

    async def handle_raw_voice_server(self, event: RawGatewayEvent) -> None:
        if not self.lavalink_ready():
            return

        data = event.data if isinstance(event.data, dict) else None
        if not data:
            self.logger.debug("Ignored VOICE_SERVER_UPDATE because payload was %r", event.data)
            return

        await self._forward_voice_event("VOICE_SERVER_UPDATE", data)

    async def handle_gateway_ready(self, event: WebsocketReady) -> None:
        if not self.music_available or self.lavalink_client:
            return
        try:
            self.lavalink_client = lavalink.Client(event.client.user.id)
            self.lavalink_client.add_node(
                host=self.lavalink_host,
                port=self.lavalink_port,
                password=self.lavalink_password,
                region=self.lavalink_region,
                ssl=self.lavalink_ssl,
            )
            self.lavalink_client.add_event_hooks(LavalinkEvents(self.manager))
            self.logger.info("Connected to Lavalink node at %s:%s", self.lavalink_host, self.lavalink_port)
        except Exception as error:
            self.lavalink_client = None
            self.logger.error("Failed to connect to Lavalink: %s", error)

    def create_gateway_listeners(self) -> Tuple:
        runtime = self

        @listen("raw_voice_state_update")
        async def on_raw_voice_state_update(event: RawGatewayEvent):
            await runtime.handle_raw_voice_state(event)

        @listen("raw_voice_server_update")
        async def on_raw_voice_server_update(event: RawGatewayEvent):
            await runtime.handle_raw_voice_server(event)

        @listen(WebsocketReady)
        async def on_gateway_ready(event: WebsocketReady):
            await runtime.handle_gateway_ready(event)

        return on_raw_voice_state_update, on_raw_voice_server_update, on_gateway_ready


class GuildMusicSession:
    def __init__(self, guild_id: int, runtime: MusicRuntime, cleanup_callback: Callable[[int], None]) -> None:
        self.guild_id = guild_id
        self.runtime = runtime
        self.idle_task: Optional[asyncio.Task] = None
        self._cleanup_callback = cleanup_callback
        self._client: Optional[Client] = None
        self._channel_id: Optional[int] = None

    async def ensure_connected(self, channel) -> None:
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
        if self._channel_id == target_id:
            return

        self.runtime.logger.info("Requesting voice connection to channel %s in guild %s", target_id, self.guild_id)
        await self.runtime.issue_voice_state_update(client, self.guild_id, target_id, deafened=False)
        self._channel_id = target_id

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
        lavalink_client = self.runtime.get_lavalink_client()
        if lavalink_client:
            player = lavalink_client.player_manager.get(self.guild_id)
            if player:
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
            except asyncio.CancelledError:  # pragma: no cover
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


class MusicManager:
    def __init__(self, runtime: MusicRuntime) -> None:
        self.runtime = runtime
        self.sessions: Dict[int, GuildMusicSession] = {}

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

    def get_player(self, guild_id: int):
        client = self.runtime.get_lavalink_client()
        if not client:
            raise MusicError("Music playback isn't configured.")
        return client.player_manager.create(guild_id)

    async def load_tracks(self, query: str) -> lavalink.LoadResult:
        client = self.runtime.get_lavalink_client()
        if not client:
            raise MusicError("Music playback isn't configured.")
        normalized = query.strip()
        if not normalized:
            raise MusicError("Please provide a search term or link.")
        if not normalized.startswith(("http://", "https://")):
            normalized = f"ytsearch:{normalized}"
        result = await client.get_tracks(normalized)
        if result.load_type == lavalink.LoadType.ERROR:
            raise MusicError(f"Lavalink error: {result.error}")
        if result.load_type == lavalink.LoadType.EMPTY or not result.tracks:
            raise MusicError("No matches found for that query.")
        return result

    async def schedule_idle(self, guild_id: int) -> None:
        session = self.sessions.get(guild_id)
        if session:
            session.start_idle_timer()

    def cancel_idle(self, guild_id: int) -> None:
        session = self.sessions.get(guild_id)
        if session:
            session.cancel_idle_timer()


class LavalinkEvents:
    def __init__(self, manager: MusicManager) -> None:
        self.manager = manager

    @lavalink.listener(lavalink.TrackStartEvent)
    async def track_start(self, event: lavalink.TrackStartEvent) -> None:
        logger = self.manager.runtime.logger
        logger.info(
            "TrackStartEvent in guild %s: %s",
            event.player.guild_id,
            getattr(event.track, "title", "Unknown title"),
        )
        self.manager.cancel_idle(event.player.guild_id)

    @lavalink.listener(lavalink.TrackEndEvent)
    async def track_end(self, event: lavalink.TrackEndEvent) -> None:
        if event.player.queue or event.player.is_playing:
            return
        await self.manager.schedule_idle(event.player.guild_id)

    @lavalink.listener(lavalink.QueueEndEvent)
    async def queue_end(self, event: lavalink.QueueEndEvent) -> None:
        await self.manager.schedule_idle(event.player.guild_id)

    @lavalink.listener(lavalink.TrackExceptionEvent)
    async def track_exception(self, event: lavalink.TrackExceptionEvent) -> None:
        logger = self.manager.runtime.logger
        logger.warning("Track exception in guild %s: %s", event.player.guild_id, event.exception)
        if event.player.queue:
            await event.player.play()
        else:
            await self.manager.schedule_idle(event.player.guild_id)
