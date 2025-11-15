import interactions
import os
import aiohttp
import asyncio
import logging
from typing import Callable, Dict, List, Optional
from dotenv import load_dotenv
from interactions import (
    Client,
    Intents,
    OptionType,
    SlashContext,
    listen,
    slash_command,
    slash_option,
)
from interactions.api.events import RawGatewayEvent, WebsocketReady
from interactions.api.events.discord import MessageCreate, VoiceUserJoin, VoiceUserLeave
import lavalink

# Load environment variables
load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("chuds.bot")

MUSIC_IDLE_TIMEOUT = 90
VOICE_CONNECT_TIMEOUT = 15
DEFAULT_PLAYER_VOLUME = 50

# Environment switch (change to 'dev' for development)
ENVIRONMENT = 'main'  # or 'dev'

# Get appropriate token based on environment
BOT_TOKEN = os.getenv(f'BOT_TOKEN_{ENVIRONMENT.upper()}')
LOG_CHANNEL_ID_RAW = os.getenv('LOG_CHANNEL_ID')
if LOG_CHANNEL_ID_RAW is None:
    raise RuntimeError("LOG_CHANNEL_ID is missing from environment.")
LOG_CHANNEL_ID_SANITIZED = "".join(ch for ch in LOG_CHANNEL_ID_RAW if ch.isdigit())
if not LOG_CHANNEL_ID_SANITIZED:
    raise ValueError(
        f"LOG_CHANNEL_ID must contain digits, got {LOG_CHANNEL_ID_RAW!r}"
    )
try:
    LOG_CHANNEL_ID = int(LOG_CHANNEL_ID_SANITIZED)
except ValueError as exc:
    raise ValueError(
        f"LOG_CHANNEL_ID must be numeric, got {LOG_CHANNEL_ID_RAW!r}"
    ) from exc
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')
OPENROUTER_SITE_URL = os.getenv('OPENROUTER_SITE_URL', '')
OPENROUTER_APP_NAME = os.getenv('OPENROUTER_APP_NAME', 'Chuds Discord Bot')
OPENROUTER_API_URL = 'https://openrouter.ai/api/v1/chat/completions'
AI_MODEL_ID = 'z-ai/glm-4.5-air:free'
SYSTEM_PROMPT = (
    "You are a bot that is replicating jessie pinkman from the show breaking bad, talk like him, use his slang and mannerisms. if you dont know an answer, say a joke as a response, you must use yo in every sentence, yo"
    "Keep answers concise when possible and follow Discord formatting rules."
)
MAX_MEMORY_MESSAGES = 20
MUSIC_DJ_ROLE_ID_RAW = os.getenv("MUSIC_DJ_ROLE_ID")
MUSIC_DJ_ROLE_ID: Optional[int] = None
if MUSIC_DJ_ROLE_ID_RAW:
    music_role_digits = "".join(ch for ch in MUSIC_DJ_ROLE_ID_RAW if ch.isdigit())
    if music_role_digits:
        MUSIC_DJ_ROLE_ID = int(music_role_digits)
    else:
        logger.warning("MUSIC_DJ_ROLE_ID is set but does not contain digits. Ignoring value.")
LAVALINK_HOST = os.getenv("LAVALINK_HOST", "").strip()
LAVALINK_PORT_RAW = os.getenv("LAVALINK_PORT", "").strip()
LAVALINK_PASSWORD = os.getenv("LAVALINK_PASSWORD", "").strip()
LAVALINK_REGION = os.getenv("LAVALINK_REGION", "global").strip() or "global"
LAVALINK_SSL = os.getenv("LAVALINK_SSL", "false").strip().lower() in {"1", "true", "yes"}
try:
    LAVALINK_PORT = int(LAVALINK_PORT_RAW) if LAVALINK_PORT_RAW else None
except ValueError:
    LAVALINK_PORT = None
    logger.warning("LAVALINK_PORT must be numeric, got %s", LAVALINK_PORT_RAW)
MUSIC_AVAILABLE = all([LAVALINK_HOST, LAVALINK_PORT, LAVALINK_PASSWORD])

if not OPENROUTER_API_KEY:
    logger.warning("OPENROUTER_API_KEY not set. AI chat feature disabled.")
if not MUSIC_AVAILABLE:
    logger.warning("Lavalink connection info missing. Music commands are disabled.")

user_memories: Dict[int, List[dict]] = {}
lavalink_client: Optional[lavalink.Client] = None


class MusicError(Exception):
    """Raised when the music subsystem encounters an issue."""


async def _issue_voice_state_update(
    client: Client,
    guild_id: int,
    channel_id: Optional[int],
    *,
    deafened: bool = False,
) -> None:
    """Send a VOICE_STATE_UPDATE and wait for Discord to acknowledge it."""
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
            timeout=VOICE_CONNECT_TIMEOUT,
        )
    )
    server_waiter: Optional[asyncio.Task] = None
    if channel_id is not None:
        server_waiter = asyncio.create_task(
            client.wait_for(
                "raw_voice_server_update",
                checks=_server_check,
                timeout=VOICE_CONNECT_TIMEOUT,
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


class GuildMusicSession:
    def __init__(self, guild_id: int, cleanup_callback: Callable[[int], None]) -> None:
        self.guild_id = guild_id
        self.idle_task: Optional[asyncio.Task] = None
        self._cleanup_callback = cleanup_callback
        self._client: Optional[Client] = None
        self._channel_id: Optional[int] = None

    async def ensure_connected(self, channel) -> None:
        client = getattr(channel, "_client", None)
        if not client:
            logger.error(
                "Voice channel %s in guild %s is missing a client reference",
                getattr(channel, "id", "unknown"),
                self.guild_id,
            )
            raise MusicError("I couldn't figure out how to join that voice chat. Please try again.")

        self._client = client
        target_id = int(channel.id)
        if self._channel_id == target_id:
            return

        logger.info("Requesting voice connection to channel %s in guild %s", target_id, self.guild_id)
        await _issue_voice_state_update(client, self.guild_id, target_id, deafened=False)
        self._channel_id = target_id

    async def disconnect(self) -> None:
        self.cancel_idle_timer()
        if self._client and self._channel_id is not None:
            try:
                await _issue_voice_state_update(self._client, self.guild_id, None, deafened=False)
            except Exception as error:
                logger.warning(
                    "Failed to disconnect voice session in guild %s: %s",
                    self.guild_id,
                    error,
                )
        self._channel_id = None
        self._client = None
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
                await asyncio.sleep(MUSIC_IDLE_TIMEOUT)
            except asyncio.CancelledError:  # pragma: no cover
                return

            player = None
            if lavalink_client:
                player = lavalink_client.player_manager.get(self.guild_id)

            if player and (player.is_playing or player.queue):
                logger.info(
                    "Idle timer aborted for guild %s because playback resumed.",
                    self.guild_id,
                )
                return

            await self.disconnect()
        finally:
            self.idle_task = None


class MusicManager:
    def __init__(self) -> None:
        self.sessions: Dict[int, GuildMusicSession] = {}

    def _cleanup(self, guild_id: int) -> None:
        self.sessions.pop(guild_id, None)

    def get_session(self, guild_id: int) -> GuildMusicSession:
        session = self.sessions.get(guild_id)
        if not session:
            session = GuildMusicSession(guild_id, self._cleanup)
            self.sessions[guild_id] = session
        return session

    def active_session(self, guild_id: int) -> Optional[GuildMusicSession]:
        return self.sessions.get(guild_id)

    def require_client(self) -> lavalink.Client:
        if not MUSIC_AVAILABLE or not lavalink_client:
            raise MusicError("Lavalink client is not ready.")
        return lavalink_client

    def get_player(self, guild_id: int):
        client = self.require_client()
        return client.player_manager.create(guild_id)

    async def load_tracks(self, query: str) -> lavalink.LoadResult:
        client = self.require_client()
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


music_manager = MusicManager()


class LavalinkEvents:
    def __init__(self, manager: MusicManager) -> None:
        self.manager = manager

    @lavalink.listener(lavalink.TrackStartEvent)
    async def track_start(self, event: lavalink.TrackStartEvent) -> None:
        logger.info(
            "TrackStartEvent in guild %s: %s",
            event.player.guild_id,
            getattr(event.track, "title", "Unknown title"),
        )
        self.manager.cancel_idle(event.player.guild_id)
    @lavalink.listener(lavalink.TrackEndEvent)
    async def track_end(self, event: lavalink.TrackEndEvent) -> None:
        # Lavalink's DefaultPlayer already advances the queue. Only start the idle timer if nothing else is playing.
        if event.player.queue or event.player.is_playing:
            return
        await self.manager.schedule_idle(event.player.guild_id)

    @lavalink.listener(lavalink.QueueEndEvent)
    async def queue_end(self, event: lavalink.QueueEndEvent) -> None:
        await self.manager.schedule_idle(event.player.guild_id)

    @lavalink.listener(lavalink.TrackExceptionEvent)
    async def track_exception(self, event: lavalink.TrackExceptionEvent) -> None:
        logger.warning("Track exception in guild %s: %s", event.player.guild_id, event.exception)
        if event.player.queue:
            await event.player.play()
        else:
            await self.manager.schedule_idle(event.player.guild_id)


def _has_music_control(member) -> bool:
    """Return True if the member can control the music queue."""
    if MUSIC_DJ_ROLE_ID is None:
        return True
    try:
        return all(role.id != MUSIC_DJ_ROLE_ID for role in getattr(member, "roles", []))
    except Exception:
        return True


def _get_voice_channel(member):
    """Return the voice channel the member is currently in, if any."""
    voice_state = getattr(member, "voice", None)
    if voice_state and getattr(voice_state, "channel", None):
        return voice_state.channel
    return None


async def _require_music_permission(ctx: SlashContext) -> bool:
    if _has_music_control(ctx.author):
        return True
    await ctx.send("You can't use music commands while holding the blocked DJ role.", ephemeral=True)
    return False



def _lavalink_ready() -> bool:
    return MUSIC_AVAILABLE and lavalink_client is not None


async def _require_lavalink(ctx: SlashContext) -> bool:
    if _lavalink_ready():
        return True
    await ctx.send(
        "Music playback isn't configured. Set the Lavalink environment variables and restart the bot.",
        ephemeral=True,
    )
    return False


def _format_duration(duration_ms: Optional[int]) -> str:
    if duration_ms is None or duration_ms <= 0:
        return "LIVE"
    seconds = duration_ms // 1000
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def _format_bytes(num_bytes: Optional[int]) -> str:
    value = float(max(num_bytes or 0, 0))
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return "0 B"


def _format_uptime(uptime_ms: Optional[int]) -> str:
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

bot = Client(
    token=BOT_TOKEN,
    intents=Intents.DEFAULT | Intents.GUILD_VOICE_STATES,
)


async def _forward_voice_event(event_name: str, data: dict) -> None:
    """Pass VOICE_* gateway payloads to Lavalink."""
    if not _lavalink_ready() or not isinstance(data, dict):
        return

    payload = {"t": event_name, "d": data}

    if event_name == "VOICE_STATE_UPDATE":
        logger.info(
            "Forwarding %s for guild %s (channel=%s, session=%s)",
            event_name,
            data.get("guild_id"),
            data.get("channel_id"),
            data.get("session_id"),
        )
    else:
        logger.info(
            "Forwarding %s for guild %s (endpoint=%s)",
            event_name,
            data.get("guild_id"),
            data.get("endpoint"),
        )

    try:
        await lavalink_client.voice_update_handler(payload)  # type: ignore[arg-type]
    except Exception as error:
        logger.error("Error forwarding %s to Lavalink: %s", event_name, error)


@listen("raw_voice_state_update")
async def on_raw_voice_state_update(event: RawGatewayEvent):
    """Forward VOICE_STATE_UPDATE payloads coming from the gateway."""
    if not _lavalink_ready():
        return

    data = event.data if isinstance(event.data, dict) else None
    if not data:
        logger.debug("Ignored VOICE_STATE_UPDATE because payload was %r", event.data)
        return

    target_user = str(getattr(lavalink_client, "_user_id", "")) if lavalink_client else ""
    if target_user and str(data.get("user_id")) != target_user:
        logger.debug(
            "Skipping VOICE_STATE_UPDATE for guild %s (user %s != bot %s)",
            data.get("guild_id"),
            data.get("user_id"),
            target_user,
        )
        return

    await _forward_voice_event("VOICE_STATE_UPDATE", data)


@listen("raw_voice_server_update")
async def on_raw_voice_server_update(event: RawGatewayEvent):
    """Forward VOICE_SERVER_UPDATE payloads coming from the gateway."""
    if not _lavalink_ready():
        return

    data = event.data if isinstance(event.data, dict) else None
    if not data:
        logger.debug("Ignored VOICE_SERVER_UPDATE because payload was %r", event.data)
        return

    await _forward_voice_event("VOICE_SERVER_UPDATE", data)


logger.info("Environment: %s", ENVIRONMENT)
# Version
@slash_command(name="version", description="My first command :)")
async def my_command_function(ctx: SlashContext):
    await ctx.send(f"version: {ENVIRONMENT}")


async def _get_sendable_channel(client, channel_id: int):
    """Return a channel that exposes send(), or None if unavailable."""
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

# Logs voice  join
@listen(VoiceUserJoin)
async def on_voice_join(event: VoiceUserJoin):
    channel = await _get_sendable_channel(event.client, LOG_CHANNEL_ID)
    if not channel:
        print(f"⚠️ Cannot log join: channel {LOG_CHANNEL_ID} not found.")
        return
    await channel.send(
        f"🎙️ **{event.author.username}** joined **{event.channel.name}**"
    )
# Logs voice leave
@listen(VoiceUserLeave)
async def on_voice_leave(event: VoiceUserLeave):
    channel = await _get_sendable_channel(event.client, LOG_CHANNEL_ID)
    if not channel:
        print(f"⚠️ Cannot log leave: channel {LOG_CHANNEL_ID} not found.")
        return
    await channel.send(
        f"❌ **{event.author.username}** left **{event.channel.name}**"
    )

@listen(WebsocketReady)
async def on_gateway_ready(event: WebsocketReady):
    global lavalink_client
    if not MUSIC_AVAILABLE or lavalink_client:
        return
    try:
        lavalink_client = lavalink.Client(event.client.user.id)
        lavalink_client.add_node(
            host=LAVALINK_HOST,
            port=LAVALINK_PORT,
            password=LAVALINK_PASSWORD,
            region=LAVALINK_REGION,
            ssl=LAVALINK_SSL,
        )
        lavalink_client.add_event_hooks(LavalinkEvents(music_manager))
        logger.info("Connected to Lavalink node at %s:%s", LAVALINK_HOST, LAVALINK_PORT)
    except Exception as error:
        lavalink_client = None
        logger.error("Failed to connect to Lavalink: %s", error)


@slash_command(name="mcstatus", description="Check the status of agartha.mc.gg")
async def mcstatus_command(ctx: SlashContext):
    await ctx.defer()
    server_address = "agartha.my.pebble.host"
    api_url = f"https://api.mcsrvstat.us/3/{server_address}"
    timeout = aiohttp.ClientTimeout(total=10)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            headers = {"User-Agent": "DiscordBot/1.0 (contact:admin@superintendent.me .superintendent discord)"}
            async with session.get(api_url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()

                    if data.get('online', False):
                        motd = "\n".join(data['motd']['clean']) if 'motd' in data else "No MOTD available"
                        players = f"{data['players']['online']}/{data['players']['max']}" if 'players' in data else "Unknown"

                        # Build player list
                        player_list = ""
                        if 'players' in data and 'list' in data['players'] and data['players']['list']:
                            player_list = "\n**Players online:**\n" + "\n".join(
                                [f"- {player['name']}" for player in data['players']['list']]
                            )

                        await ctx.send(
                            f"?o. **{server_address} is ONLINE**\n"
                            f"**MOTD:** {motd}\n"
                            f"**Players:** {players}"
                            f"{player_list}\n"
                        )
                    else:
                        await ctx.send(f"??O **{server_address} is OFFLINE**")
                else:
                    await ctx.send(f"?s??,? Failed to check server status (HTTP {response.status})")
    except asyncio.TimeoutError:
        await ctx.send("?s??,? Timed out while reaching the status API.")
    except Exception as e:
        await ctx.send(f"?s??,? Error checking server status: {str(e)}")


@slash_command(name="lavalinkstats", description="Show Lavalink node statistics")
async def lavalink_stats_command(ctx: SlashContext):
    if not await _require_lavalink(ctx):
        return
    await ctx.defer(ephemeral=True)
    if not lavalink_client:
        await ctx.send("Music playback isn't configured.", ephemeral=True)
        return

    nodes = list(getattr(getattr(lavalink_client, "node_manager", None), "nodes", []))
    if not nodes:
        await ctx.send("No Lavalink nodes are registered with this bot.", ephemeral=True)
        return

    sections: List[str] = []
    for node in nodes:
        node_name = getattr(node, "name", "Lavalink Node")
        stats = node.stats
        if getattr(stats, "is_fake", True):
            try:
                raw_stats = await node.get_stats()
            except Exception as error:
                logger.warning("Unable to refresh Lavalink stats for %s: %s", node_name, error)
            else:
                stats = lavalink.Stats(node, raw_stats)
                node.stats = stats
        status_label = "Online" if getattr(node, "available", False) else "Offline"
        if getattr(stats, "is_fake", True):
            sections.append(f"**{node_name}** ({status_label})\nStatistics are not available yet. Try again shortly.")
            continue

        lines = [
            f"**{node_name}** ({status_label})",
            f"Players: {stats.playing_players}/{stats.players} playing",
            f"Uptime: {_format_uptime(stats.uptime)}",
            f"CPU: {stats.cpu_cores} cores | system {stats.system_load * 100:.1f}% | lavalink {stats.lavalink_load * 100:.1f}%",
            (
                "Memory: "
                f"{_format_bytes(stats.memory_used)} used / {_format_bytes(stats.memory_allocated)} allocated "
                f"(free {_format_bytes(stats.memory_free)})"
            ),
            (
                "Frames: "
                f"sent {stats.frames_sent:,} | nulled {stats.frames_nulled:,} | deficit {stats.frames_deficit:,}"
            ),
            f"Penalty: {stats.penalty.total:.2f}",
        ]
        sections.append("\n".join(lines))

    await ctx.send("\n\n".join(sections), ephemeral=True)


@slash_command(name="play", description="Queue music from YouTube or YouTube Music")
@slash_option(
    name="query",
    description="YouTube or YouTube Music link, or search terms",
    opt_type=OptionType.STRING,
    required=True,
)
async def play_command(ctx: SlashContext, query: str):
    if not await _require_music_permission(ctx):
        return
    if not await _require_lavalink(ctx):
        return
    if not ctx.guild_id:
        await ctx.send("This command can only be used inside a server.", ephemeral=True)
        return

    voice_channel = _get_voice_channel(ctx.author)
    if not voice_channel:
        await ctx.send("Join a voice channel first, then ask me to play music.", ephemeral=True)
        return

    await ctx.defer()
    guild_id = int(ctx.guild_id)
    session = music_manager.get_session(guild_id)
    session.cancel_idle_timer()
    player = music_manager.get_player(guild_id)
    if DEFAULT_PLAYER_VOLUME != player.volume:
        try:
            await player.set_volume(DEFAULT_PLAYER_VOLUME)
        except Exception as error:
            logger.warning("Unable to set player volume to %s: %s", DEFAULT_PLAYER_VOLUME, error)
    try:
        await session.ensure_connected(voice_channel)
    except asyncio.TimeoutError:
        if lavalink_client:
            lavalink_client.player_manager.remove(guild_id)
        await ctx.send("I couldn't join that voice chat in time. Please try again.", ephemeral=True)
        return
    except MusicError as error:
        logger.error("Unable to process voice connection for guild %s: %s", guild_id, error)
        if lavalink_client:
            lavalink_client.player_manager.remove(guild_id)
        await ctx.send(str(error), ephemeral=True)
        return
    except Exception as error:
        logger.error("Failed to connect to voice channel %s: %s", voice_channel.id, error)
        if lavalink_client:
            lavalink_client.player_manager.remove(guild_id)
        await ctx.send("I couldn't join that voice chat. Check my permissions and try again.", ephemeral=True)
        return

    try:
        result = await music_manager.load_tracks(query)
    except MusicError as error:
        await ctx.send(f"I couldn't load that track: {error}", ephemeral=True)
        return

    player.channel_id = voice_channel.id

    if result.load_type == lavalink.LoadType.PLAYLIST:
        for track in result.tracks:
            track.requester = ctx.author.id  # type: ignore[attr-defined]
            player.add(track)
        playlist_name = result.playlist_info.name if result.playlist_info else "Playlist"
        await ctx.send(
            f"Queued playlist **{playlist_name}** with {len(result.tracks)} tracks for {ctx.author.mention}"
        )
    else:
        track = result.tracks[0]
        track.requester = ctx.author.id  # type: ignore[attr-defined]
        player.add(track)
        await ctx.send(
            f"Queued **{track.title}** (`{_format_duration(track.duration)}`) for {ctx.author.mention}\n"
            f"<{track.uri}>"
        )

    session.cancel_idle_timer()
    if not player.is_playing:
        await player.play()


@slash_command(name="skip", description="Skip the currently playing track")
async def skip_command(ctx: SlashContext):
    if not await _require_music_permission(ctx):
        return
    if not await _require_lavalink(ctx):
        return
    if not ctx.guild_id:
        await ctx.send("This only works inside a server.", ephemeral=True)
        return
    player = lavalink_client.player_manager.get(int(ctx.guild_id)) if lavalink_client else None
    if not player or not player.current:
        await ctx.send("Nothing is playing to skip.", ephemeral=True)
        return
    await player.skip()
    await ctx.send("Skipped the current track.")


@slash_command(name="pause", description="Pause the current track")
async def pause_command(ctx: SlashContext):
    if not await _require_music_permission(ctx):
        return
    if not await _require_lavalink(ctx):
        return
    if not ctx.guild_id:
        await ctx.send("This only works inside a server.", ephemeral=True)
        return
    player = lavalink_client.player_manager.get(int(ctx.guild_id)) if lavalink_client else None
    if not player or not player.is_playing or player.paused:
        await ctx.send("There's nothing playing to pause.", ephemeral=True)
        return
    await player.set_pause(True)
    await ctx.send("Paused the music.")


@slash_command(name="resume", description="Resume playback if paused")
async def resume_command(ctx: SlashContext):
    if not await _require_music_permission(ctx):
        return
    if not await _require_lavalink(ctx):
        return
    if not ctx.guild_id:
        await ctx.send("This only works inside a server.", ephemeral=True)
        return
    player = lavalink_client.player_manager.get(int(ctx.guild_id)) if lavalink_client else None
    if not player or not player.paused:
        await ctx.send("I'm not paused right now.", ephemeral=True)
        return
    await player.set_pause(False)
    await ctx.send("Resumed playback.")


@slash_command(name="queue", description="Show the current music queue")
async def queue_command(ctx: SlashContext):
    if not await _require_lavalink(ctx):
        return
    if not ctx.guild_id:
        await ctx.send("This command must be used inside a server.", ephemeral=True)
        return
    player = lavalink_client.player_manager.get(int(ctx.guild_id)) if lavalink_client else None
    if not player or (not player.current and not player.queue):
        await ctx.send("Nothing is queued up right now.")
        return
    lines: List[str] = []
    if player.current:
        lines.append(
            f"**Now playing:** {player.current.title} (`{_format_duration(player.current.duration)}`) ? requested by <@{player.current.requester}>"
        )
    if player.queue:
        lines.append("")
        lines.append("**Up next:**")
        for index, track in enumerate(player.queue[:10], start=1):
            lines.append(
                f"{index}. {track.title} (`{_format_duration(track.duration)}`) ? requested by <@{track.requester}>"
            )
        remaining = len(player.queue) - 10
        if remaining > 0:
            lines.append(f"...and {remaining} more.")
    await ctx.send("\n".join(lines))



@slash_command(name="stop", description="Stop playback and clear the queue")
async def stop_command(ctx: SlashContext):
    if not await _require_music_permission(ctx):
        return
    if not await _require_lavalink(ctx):
        return
    if not ctx.guild_id:
        await ctx.send("Use this inside a server.", ephemeral=True)
        return
    guild_id = int(ctx.guild_id)
    player = lavalink_client.player_manager.get(guild_id) if lavalink_client else None
    session = music_manager.active_session(guild_id)
    if not player and not session:
        await ctx.send("There's no active music session to stop.", ephemeral=True)
        return
    if player:
        player.queue.clear()
        try:
            await player.stop()
        except Exception:
            pass
        lavalink_client.player_manager.remove(guild_id)
    if session:
        await session.disconnect()
    await ctx.send("Music stopped and the bot left the voice channel.")

def _bot_was_mentioned(content: str, bot_id: int) -> bool:
    mention_patterns = (f"<@{bot_id}>", f"<@!{bot_id}>")
    return any(pattern in content for pattern in mention_patterns)


def _strip_bot_mentions(content: str, bot_id: int) -> str:
    mention_patterns = (f"<@{bot_id}>", f"<@!{bot_id}>")
    for pattern in mention_patterns:
        content = content.replace(pattern, '')
    return content.strip()


def _append_memory(user_id: int, role: str, message: str) -> None:
    history = user_memories.setdefault(user_id, [])
    history.append({"role": role, "content": message})
    if len(history) > MAX_MEMORY_MESSAGES:
        history[:] = history[-MAX_MEMORY_MESSAGES:]


async def _query_openrouter(messages: List[dict]) -> str:
    if not OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY missing")

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    if OPENROUTER_SITE_URL:
        headers["HTTP-Referer"] = OPENROUTER_SITE_URL
    if OPENROUTER_APP_NAME:
        headers["X-Title"] = OPENROUTER_APP_NAME

    payload = {
        "model": AI_MODEL_ID,
        "messages": messages,
        "temperature": 0.7,
    }

    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            OPENROUTER_API_URL,
            json=payload,
            headers=headers,
        ) as response:
            if response.status != 200:
                error_text = await response.text()
                raise RuntimeError(
                    f"OpenRouter error {response.status}: {error_text}"
                )
            data = await response.json()

    choices = data.get("choices")
    if not choices:
        raise RuntimeError("OpenRouter returned no choices")

    message = choices[0].get("message", {})
    content = message.get("content", "").strip()
    if not content:
        raise RuntimeError("OpenRouter returned empty content")
    return content


def _chunk_response(content: str, limit: int = 1800) -> List[str]:
    if len(content) <= limit:
        return [content]

    chunks = []
    current = []
    current_len = 0
    for paragraph in content.split("\n"):
        piece = paragraph + "\n"
        if current_len + len(piece) > limit and current:
            chunks.append("".join(current).rstrip())
            current = [piece]
            current_len = len(piece)
        else:
            current.append(piece)
            current_len += len(piece)
    if current:
        chunks.append("".join(current).rstrip())
    return [chunk for chunk in chunks if chunk]


@listen(MessageCreate)
async def handle_ai_conversation(event: MessageCreate):
    message = event.message
    if not message or not message.content:
        return
    if not OPENROUTER_API_KEY:
        return
    if message.author.bot:
        return

    bot_user = event.client.user
    if not bot_user:
        return

    bot_id = bot_user.id
    if not _bot_was_mentioned(message.content, bot_id):
        return

    cleaned_content = _strip_bot_mentions(message.content, bot_id)
    if not cleaned_content:
        cleaned_content = "Hello!"

    logger.info(
        "Incoming mention from %s (%s): %s",
        getattr(message.author, "username", "unknown"),
        message.author.id,
        cleaned_content,
    )

    user_id = int(message.author.id)
    history = user_memories.get(user_id, [])
    trimmed_history = history[-MAX_MEMORY_MESSAGES:]
    logger.debug("History length for %s: %d", user_id, len(trimmed_history))

    request_messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    request_messages.extend(trimmed_history)
    request_messages.append({"role": "user", "content": cleaned_content})

    try:
        ai_response = await _query_openrouter(request_messages)
    except Exception as error:
        logger.error("Failed to fetch AI response for %s: %s", user_id, error)
        await message.reply(
            "I couldn't reach my AI brain right now. Please try again later."
        )
        return

    _append_memory(user_id, "user", cleaned_content)
    _append_memory(user_id, "assistant", ai_response)
    preview = ai_response if len(ai_response) <= 200 else f"{ai_response[:200]}..."
    logger.info(
        "AI response to %s (%s): %s",
        getattr(message.author, "username", "unknown"),
        message.author.id,
        preview,
    )

    chunks = _chunk_response(ai_response)
    first_message = True
    for chunk in chunks:
        if first_message:
            await message.reply(chunk)
            first_message = False
        else:
            await message.channel.send(chunk)


bot.start()
