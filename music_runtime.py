"""Music runtime utilities and listeners."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional, Tuple

import lavalink
from interactions import Client, Member, SlashContext, User, listen
from interactions.api.events import RawGatewayEvent, WebsocketReady

from music_errors import MusicError
from music_events import LavalinkEvents
from music_manager import MusicManager


LAVALINK_MAX_RECONNECT_SECONDS = 30.0


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
        audio_normalization: bool,
        normalization_max_amplitude: float,
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
        self.audio_normalization = audio_normalization
        self.normalization_max_amplitude = normalization_max_amplitude
        self._normalization_unavailable = False
        self.lavalink_client: Optional[lavalink.Client] = None
        self._lavalink_user_id: Optional[int] = None
        self._lavalink_node_ready = False
        self._lavalink_reconnect_lock = asyncio.Lock()
        self._no_nodes_retry_count = 0
        self._no_nodes_retry_after = 0.0
        self._node_disconnect_retry_count = 0
        self._node_reconnect_task: Optional[asyncio.Task] = None
        self._node_reconnect_deadline: Optional[float] = None
        self.manager = MusicManager(self)
        self.voice_channel_ids: Dict[int, int] = {}
        self.voice_session_ids: Dict[int, str] = {}

    def get_lavalink_client(self) -> Optional[lavalink.Client]:
        return self.lavalink_client

    def lavalink_ready(self) -> bool:
        return self.music_available and self.lavalink_client is not None and self._lavalink_node_ready

    @staticmethod
    def _is_no_available_nodes_error(error: Exception) -> bool:
        return isinstance(error, lavalink.errors.ClientError) and "No available nodes" in str(error)

    def _can_retry_no_nodes_reconnect(self) -> bool:
        return asyncio.get_running_loop().time() >= self._no_nodes_retry_after

    def _record_no_nodes_reconnect_failure(self) -> float:
        self._no_nodes_retry_count += 1
        backoff_seconds = min(
            LAVALINK_MAX_RECONNECT_SECONDS,
            float(2 ** min(self._no_nodes_retry_count, 5)),
        )
        self._no_nodes_retry_after = asyncio.get_running_loop().time() + backoff_seconds
        return backoff_seconds

    def _reset_no_nodes_reconnect_backoff(self) -> None:
        self._no_nodes_retry_count = 0
        self._no_nodes_retry_after = 0.0

    def _remaining_no_nodes_backoff(self) -> float:
        return max(0.0, self._no_nodes_retry_after - asyncio.get_running_loop().time())

    def trigger_no_available_nodes_reconnect(self, reason: str) -> None:
        if not self._can_retry_no_nodes_reconnect():
            return
        self.logger.warning("Lavalink reported no available nodes while %s", reason)
        asyncio.create_task(self.reconnect_lavalink(reason=f"no available nodes {reason}"))

    def _record_node_disconnect_failure(self) -> float:
        self._node_disconnect_retry_count += 1
        return min(
            LAVALINK_MAX_RECONNECT_SECONDS,
            float(5 * (2 ** min(self._node_disconnect_retry_count - 1, 4))),
        )

    def _reset_node_disconnect_backoff(self) -> None:
        self._node_disconnect_retry_count = 0

    async def _rebind_voice_sessions(self) -> None:
        for guild_id, session in list(self.manager.sessions.items()):
            try:
                await session.reconnect_voice_state()
            except Exception as error:
                self.logger.warning(
                    "Failed to refresh voice state for guild %s after Lavalink reconnect: %s",
                    guild_id,
                    error,
                )

    async def _delayed_lavalink_reconnect(self, *, delay: float, reason: str) -> None:
        try:
            loop = asyncio.get_running_loop()
            deadline = self._node_reconnect_deadline
            remaining = deadline - loop.time() if deadline is not None else 0.0
            if remaining <= 0:
                self.logger.error(
                    "Stopped retrying Lavalink after %.0fs (%s)",
                    LAVALINK_MAX_RECONNECT_SECONDS,
                    reason,
                )
                return

            await asyncio.sleep(min(delay, remaining))
            if loop.time() >= deadline:
                self.logger.error(
                    "Stopped retrying Lavalink after %.0fs (%s)",
                    LAVALINK_MAX_RECONNECT_SECONDS,
                    reason,
                )
                return

            connected = await self.connect_lavalink(self._lavalink_user_id) if self._lavalink_user_id is not None else False
            if not connected:
                next_delay = self._record_node_disconnect_failure()
                self.logger.warning(
                    "Lavalink reconnect failed after %s; retrying in %.1fs",
                    reason,
                    next_delay,
                )
                self._node_reconnect_task = asyncio.create_task(
                    self._delayed_lavalink_reconnect(delay=next_delay, reason=reason)
                )
                return

            await self._rebind_voice_sessions()
        finally:
            current_task = asyncio.current_task()
            if self._node_reconnect_task is current_task:
                self._node_reconnect_task = None

    async def _close_lavalink_client(self, client: Optional[lavalink.Client]) -> None:
        if client is None:
            return

        close = getattr(client, "close", None)
        if callable(close):
            try:
                result = close()
                if asyncio.iscoroutine(result):
                    await result
            except Exception as error:
                self.logger.warning("Failed to close Lavalink client cleanly: %s", error)

    def _create_lavalink_client(self, user_id: int) -> lavalink.Client:
        client = lavalink.Client(user_id)
        port = self.lavalink_port if self.lavalink_port is not None else 2333
        client.add_node(
            host=self.lavalink_host,
            port=port,
            password=self.lavalink_password,
            region=self.lavalink_region,
            ssl=self.lavalink_ssl,
        )
        client.add_event_hooks(LavalinkEvents(self.manager))
        return client

    async def connect_lavalink(self, user_id: int) -> bool:
        if not self.music_available:
            return False

        self._lavalink_user_id = user_id
        async with self._lavalink_reconnect_lock:
            if self.lavalink_client is not None:
                return True

            try:
                self._lavalink_node_ready = False
                self.lavalink_client = self._create_lavalink_client(user_id)
                self.logger.info("Connected to Lavalink node at %s:%s", self.lavalink_host, self.lavalink_port)
                return True
            except Exception as error:
                self._lavalink_node_ready = False
                self.lavalink_client = None
                self.logger.error("Failed to connect to Lavalink: %s", error)
                return False

    async def reconnect_lavalink(self, *, reason: str) -> bool:
        if not self.music_available or self._lavalink_user_id is None:
            return False

        if "no available nodes" in reason and not self._can_retry_no_nodes_reconnect():
            self.logger.warning(
                "Skipping Lavalink reconnect after %s due to backoff (%.1fs remaining)",
                reason,
                self._remaining_no_nodes_backoff(),
            )
            return False

        async with self._lavalink_reconnect_lock:
            old_client = self.lavalink_client
            self.lavalink_client = None
            self._lavalink_node_ready = False
            await self._close_lavalink_client(old_client)

            try:
                self.lavalink_client = self._create_lavalink_client(self._lavalink_user_id)
                self.logger.warning(
                    "Reconnected to Lavalink node at %s:%s after %s",
                    self.lavalink_host,
                    self.lavalink_port,
                    reason,
                )
                self._reset_no_nodes_reconnect_backoff()
                self._reset_node_disconnect_backoff()
                return True
            except Exception as error:
                self._lavalink_node_ready = False
                self.lavalink_client = None
                if "no available nodes" in reason:
                    backoff_seconds = self._record_no_nodes_reconnect_failure()
                    self.logger.warning(
                        "Backing off Lavalink reconnects for %.1fs after %s",
                        backoff_seconds,
                        reason,
                    )
                self.logger.error("Failed to reconnect to Lavalink after %s: %s", reason, error)
                return False

    async def handle_node_disconnect(self, *, code: Any, reason: Any) -> None:
        if not self.music_available or self._lavalink_user_id is None:
            return

        if self._node_reconnect_task and not self._node_reconnect_task.done():
            self.logger.warning(
                "Ignoring additional Lavalink disconnect while reconnect is already scheduled: code=%s reason=%s",
                code,
                reason,
            )
            return

        loop = asyncio.get_running_loop()
        if self._node_reconnect_deadline is None:
            self._node_reconnect_deadline = loop.time() + LAVALINK_MAX_RECONNECT_SECONDS
        elif loop.time() >= self._node_reconnect_deadline:
            self.logger.error(
                "Stopped retrying Lavalink after %.0fs; restart or restore the node before trying again",
                LAVALINK_MAX_RECONNECT_SECONDS,
            )
            return

        delay = self._record_node_disconnect_failure()
        old_client = self.lavalink_client
        self.lavalink_client = None
        self._lavalink_node_ready = False
        await self._close_lavalink_client(old_client)
        self.logger.warning(
            "Backing off Lavalink reconnect for %.1fs after websocket disconnect code=%s reason=%s",
            delay,
            code,
            reason,
        )
        self._node_reconnect_task = asyncio.create_task(
            self._delayed_lavalink_reconnect(delay=delay, reason="node websocket disconnect")
        )

    def handle_node_ready(self) -> None:
        self._lavalink_node_ready = True
        self._reset_node_disconnect_backoff()
        self._node_reconnect_deadline = None
        reconnect_task = self._node_reconnect_task
        if reconnect_task and reconnect_task.done():
            self._node_reconnect_task = None

    async def require_lavalink(self, ctx: SlashContext) -> bool:
        if self.lavalink_ready():
            return True
        if self.music_available and self.lavalink_client is not None:
            await ctx.send("Lavalink is still connecting. Try again in a few seconds.", ephemeral=True)
        else:
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

    def has_music_control(self, member: Member | User) -> bool:
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

        async def _wait_for_state_update() -> RawGatewayEvent:
            return await client.wait_for(
                "raw_voice_state_update",
                checks=_state_check,
                timeout=self.voice_connect_timeout,
            )

        async def _wait_for_server_update() -> RawGatewayEvent:
            return await client.wait_for(
                "raw_voice_server_update",
                checks=_server_check,
                timeout=self.voice_connect_timeout,
            )

        state_waiter = asyncio.create_task(_wait_for_state_update())
        server_waiter: Optional[asyncio.Task] = None
        if channel_id is not None:
            server_waiter = asyncio.create_task(_wait_for_server_update())

        try:
            connection_state = getattr(client, "_connection_state", None)
            gateway = getattr(connection_state, "gateway", None)
            if gateway is None:
                raise MusicError("Gateway is not ready yet. Try again in a moment.")

            target_channel: Any = channel_id if channel_id is not None else None
            await gateway.voice_state_update(
                guild_id=guild_id,
                channel_id=target_channel,
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

    @staticmethod
    def _gateway_payload(event_name: str, data: dict) -> dict:
        # Lavalink.py expects the raw Discord gateway payload shape, not just {"t", "d"}.
        return {
            "op": 0,
            "t": event_name,
            "s": None,
            "d": data,
        }

    async def _forward_voice_event(self, event_name: str, payload: dict) -> None:
        if not self.lavalink_ready() or not isinstance(payload, dict):
            return
        data = payload.get("d") if isinstance(payload.get("d"), dict) else None
        if not data:
            self.logger.debug("Ignored %s because payload data was %r", event_name, payload)
            return

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

    async def _send_voice_server_update(self, guild_id: int, data: dict) -> bool:
        lavalink_client = self.get_lavalink_client()
        if not lavalink_client:
            return False

        try:
            player = lavalink_client.player_manager.create(guild_id)
        except lavalink.errors.ClientError as error:
            if self._is_no_available_nodes_error(error):
                self.trigger_no_available_nodes_reconnect("applying voice server update")
                return False
            raise
        channel_id = self.voice_channel_ids.get(guild_id)
        if channel_id is None:
            channel_id = getattr(player, "channel_id", None)
        if channel_id is None:
            self.logger.warning(
                "Skipping direct voice update for guild %s because no channel id is available.",
                guild_id,
            )
            return False

        session_id = self.voice_session_ids.get(guild_id)
        if not session_id:
            self.logger.warning(
                "Skipping direct voice update for guild %s because no session id is available.",
                guild_id,
            )
            return False

        voice_payload = {
            "sessionId": session_id,
            "endpoint": data["endpoint"],
            "token": data["token"],
            "channelId": str(channel_id),
        }

        node = getattr(player, "node", None)
        update_player = getattr(node, "update_player", None) if node else None
        if callable(update_player):
            attempts = (
                {"guild_id": guild_id, "voice_state": voice_payload},
                {"guild_id": guild_id, "voice": voice_payload},
                {"guild_id": str(guild_id), "voice_state": voice_payload},
                {"guild_id": str(guild_id), "voice": voice_payload},
            )
            for kwargs in attempts:
                try:
                    await update_player(**kwargs)
                    self.logger.info(
                        "Applied direct voice update for guild %s with channelId=%s",
                        guild_id,
                        channel_id,
                    )
                    return True
                except TypeError:
                    continue
                except Exception as error:
                    self.logger.warning(
                        "Direct voice update failed for guild %s via update_player(%s): %s",
                        guild_id,
                        ", ".join(kwargs.keys()),
                        error,
                    )

        return False

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

        guild_id = data.get("guild_id")
        channel_id = data.get("channel_id")
        session_id = data.get("session_id")
        if guild_id is not None:
            guild_key = int(guild_id)
            if channel_id is None:
                self.voice_channel_ids.pop(guild_key, None)
                self.voice_session_ids.pop(guild_key, None)
            else:
                self.voice_channel_ids[guild_key] = int(channel_id)
                if session_id:
                    self.voice_session_ids[guild_key] = str(session_id)

        payload = self._gateway_payload("VOICE_STATE_UPDATE", dict(data))
        await self._forward_voice_event("VOICE_STATE_UPDATE", payload)

    async def handle_raw_voice_server(self, event: RawGatewayEvent) -> None:
        if not self.lavalink_ready():
            return

        data = event.data if isinstance(event.data, dict) else None
        if not data:
            self.logger.debug("Ignored VOICE_SERVER_UPDATE because payload was %r", event.data)
            return

        missing = [key for key in ("token", "endpoint", "guild_id") if not data.get(key)]
        if missing:
            self.logger.warning(
                "VOICE_SERVER_UPDATE for guild %s is missing %s: %r",
                data.get("guild_id"),
                ", ".join(missing),
                data,
            )
            return

        payload_data = dict(data)
        guild_id = payload_data.get("guild_id")
        if guild_id is not None:
            guild_key = int(guild_id)
            channel_id = self.voice_channel_ids.get(guild_key)
            if channel_id is None:
                lavalink_client = self.get_lavalink_client()
                if lavalink_client:
                    player = lavalink_client.player_manager.get(guild_key)
                    channel_id = getattr(player, "channel_id", None) if player else None
            if channel_id is not None:
                payload_data["channel_id"] = str(channel_id)

        if await self._send_voice_server_update(guild_key, payload_data):
            return

        payload = self._gateway_payload("VOICE_SERVER_UPDATE", payload_data)
        await self._forward_voice_event("VOICE_SERVER_UPDATE", payload)

    async def handle_gateway_ready(self, event: WebsocketReady) -> None:
        if not self.music_available or self.lavalink_client:
            return
        if not event.client.user:
            return
        await self.connect_lavalink(event.client.user.id)

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
