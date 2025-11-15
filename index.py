import interactions
import os
import aiohttp
import asyncio
import functools
import logging
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, Dict, List, Optional
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
from interactions.api.events.discord import MessageCreate, VoiceUserJoin, VoiceUserLeave
from interactions.api.voice.audio import AudioVolume

try:
    import yt_dlp  # type: ignore
except ImportError:  # pragma: no cover - handled at runtime
    yt_dlp = None

# Load environment variables
load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("chuds.bot")

YTDL_OPTIONS = {
    "quiet": True,
    "format": "bestaudio/best",
    "outtmpl": "%(id)s",
    "restrictfilenames": True,
    "ignoreerrors": False,
    "no_warnings": True,
    "default_search": "auto",
    "source_address": "0.0.0.0",
    "nocheckcertificate": True,
}
FFMPEG_RECONNECT_ARGS = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
MUSIC_IDLE_TIMEOUT = 90
VOICE_CONNECT_TIMEOUT = 15

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

if not OPENROUTER_API_KEY:
    logger.warning("OPENROUTER_API_KEY not set. AI chat feature disabled.")
if yt_dlp is None:
    logger.warning("yt-dlp is not installed. Music commands are disabled until the dependency is available.")

user_memories: Dict[int, List[dict]] = {}


class MusicError(Exception):
    """Raised when the music subsystem encounters an issue."""


@dataclass
class MusicTrack:
    title: str
    stream_url: str
    webpage_url: str
    duration: Optional[int]
    uploader: Optional[str]
    requested_by: str

    @property
    def pretty_duration(self) -> str:
        if not self.duration:
            return "LIVE"
        minutes, seconds = divmod(self.duration, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes}:{seconds:02d}"


class GuildMusicSession:
    def __init__(self, guild_id: int, cleanup_callback: Callable[[int], None]) -> None:
        self.guild_id = guild_id
        self.queue: Deque[MusicTrack] = deque()
        self.current: Optional[MusicTrack] = None
        self.voice_state = None
        self._condition = asyncio.Condition()
        self.player_task: Optional[asyncio.Task] = None
        self.idle_task: Optional[asyncio.Task] = None
        self._closing = False
        self._cleanup_callback = cleanup_callback

    async def ensure_connected(self, channel) -> None:
        """Connect or move to the target voice channel."""
        if self.voice_state and getattr(self.voice_state, "channel", None):
            current_channel = self.voice_state.channel
            if current_channel and current_channel.id == channel.id:
                return
            await self.voice_state.move(channel.id)
            return
        self.voice_state = await channel.connect()

    async def enqueue(self, track: MusicTrack) -> None:
        """Add a track to the queue and spin up the playback loop."""
        self._closing = False
        self._cancel_idle_timer()
        async with self._condition:
            self.queue.append(track)
            self._condition.notify()
        if not self.player_task or self.player_task.done():
            self.player_task = asyncio.create_task(self._player_loop())

    async def skip(self) -> None:
        if not self.voice_state or not self.voice_state.playing:
            raise MusicError("Nothing is currently playing.")
        await self.voice_state.stop()

    def pause(self) -> None:
        if not self.voice_state or not self.voice_state.playing:
            raise MusicError("Nothing is currently playing.")
        if self.voice_state.paused:
            raise MusicError("Playback is already paused.")
        self.voice_state.pause()

    def resume(self) -> None:
        if not self.voice_state or not self.voice_state.paused:
            raise MusicError("Playback is not paused.")
        self.voice_state.resume()

    async def stop(self, *, disconnect: bool = False) -> None:
        """Clear queue and optionally disconnect from the channel."""
        self._closing = True
        async with self._condition:
            self.queue.clear()
            self._condition.notify_all()
        self._cancel_idle_timer()

        if self.voice_state and (self.voice_state.playing or self.voice_state.paused):
            await self.voice_state.stop()

        task = self.player_task
        if task:
            try:
                await task
            except Exception as error:  # pragma: no cover
                logger.warning("Music player task ended with error: %s", error)
        self.player_task = None
        self.current = None

        if disconnect:
            if self.voice_state:
                try:
                    await self.voice_state.disconnect()
                finally:
                    self.voice_state = None
            self._cleanup_callback(self.guild_id)

        self._closing = False

    async def disconnect(self) -> None:
        await self.stop(disconnect=True)

    async def _player_loop(self) -> None:
        """Continuously pull tracks from the queue and stream them."""
        while True:
            track = await self._next_track()
            if track is None:
                break
            if not self.voice_state:
                logger.warning("Voice state missing for guild %s, aborting playback.", self.guild_id)
                break

            self.current = track
            audio = AudioVolume(track.stream_url)
            audio.ffmpeg_before_args = FFMPEG_RECONNECT_ARGS
            try:
                await self.voice_state.play(audio)
            except Exception as error:
                logger.error("Failed to play %s: %s", track.title, error)
            finally:
                audio.cleanup()
                self.current = None

            if not self.queue:
                self._start_idle_timer()

        self.player_task = None

    async def _next_track(self) -> Optional[MusicTrack]:
        async with self._condition:
            while not self.queue and not self._closing:
                await self._condition.wait()
            if self._closing:
                return None
            return self.queue.popleft()

    def _start_idle_timer(self) -> None:
        if self.idle_task and not self.idle_task.done():
            return
        self.idle_task = asyncio.create_task(self._disconnect_when_idle())

    def _cancel_idle_timer(self) -> None:
        if self.idle_task and not self.idle_task.done():
            self.idle_task.cancel()
        self.idle_task = None

    async def _disconnect_when_idle(self) -> None:
        try:
            await asyncio.sleep(MUSIC_IDLE_TIMEOUT)
        except asyncio.CancelledError:  # pragma: no cover
            return
        if self.queue or self.current:
            return
        await self.disconnect()


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

    async def build_track(self, query: str, requested_by: str) -> MusicTrack:
        if yt_dlp is None:
            raise MusicError("yt-dlp is not installed on this system.")

        normalized = query.strip()
        search_target = normalized
        if not normalized.startswith(("http://", "https://")):
            search_target = f"ytsearch1:{normalized}"

        def _extract() -> dict:
            with yt_dlp.YoutubeDL(YTDL_OPTIONS) as downloader:
                return downloader.extract_info(search_target, download=False)

        try:
            info = await asyncio.to_thread(_extract)
        except Exception as error:
            raise MusicError(f"Failed to process query: {error}") from error

        if not info:
            raise MusicError("No results returned for that query.")
        if "entries" in info:
            entries = info.get("entries") or []
            if not entries:
                raise MusicError("No playable results found.")
            info = entries[0]

        stream_url = info.get("url")
        if not stream_url:
            raise MusicError("Unable to find a playable audio stream.")

        return MusicTrack(
            title=info.get("title", "Untitled"),
            stream_url=stream_url,
            webpage_url=info.get("webpage_url", search_target),
            duration=info.get("duration"),
            uploader=info.get("uploader"),
            requested_by=requested_by,
        )


music_manager = MusicManager()


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
bot = Client(
    token=BOT_TOKEN,
    intents=Intents.DEFAULT | Intents.GUILD_VOICE_STATES,
)
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
    if yt_dlp is None:
        await ctx.send(
            "Music playback isn't available because yt-dlp is not installed on the host.",
            ephemeral=True,
        )
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
    try:
        await asyncio.wait_for(session.ensure_connected(voice_channel), timeout=VOICE_CONNECT_TIMEOUT)
    except asyncio.TimeoutError:
        await ctx.send("I couldn't join that voice chat in time. Please try again.", ephemeral=True)
        return
    except Exception as error:
        logger.error("Failed to connect to voice channel %s: %s", voice_channel.id, error)
        await ctx.send("I couldn't join that voice chat. Check my permissions and try again.", ephemeral=True)
        return

    display_name = getattr(ctx.author, "display_name", ctx.author.username)
    try:
        track = await music_manager.build_track(query, display_name)
    except MusicError as error:
        await ctx.send(f"I couldn't load that track: {error}", ephemeral=True)
        return

    await session.enqueue(track)
    await ctx.send(
        f"Queued **{track.title}** (`{track.pretty_duration}`) for {ctx.author.mention}\n<{track.webpage_url}>"
    )


@slash_command(name="skip", description="Skip the currently playing track")
async def skip_command(ctx: SlashContext):
    if not await _require_music_permission(ctx):
        return
    if not ctx.guild_id:
        await ctx.send("This only works inside a server.", ephemeral=True)
        return
    session = music_manager.active_session(int(ctx.guild_id))
    if not session or not session.current:
        await ctx.send("Nothing is playing to skip.", ephemeral=True)
        return
    try:
        await session.skip()
    except MusicError as error:
        await ctx.send(str(error), ephemeral=True)
        return
    await ctx.send("Skipped the current track.")


@slash_command(name="pause", description="Pause the current track")
async def pause_command(ctx: SlashContext):
    if not await _require_music_permission(ctx):
        return
    if not ctx.guild_id:
        await ctx.send("This only works inside a server.", ephemeral=True)
        return
    session = music_manager.active_session(int(ctx.guild_id))
    if not session:
        await ctx.send("I'm not in a voice channel right now.", ephemeral=True)
        return
    try:
        session.pause()
    except MusicError as error:
        await ctx.send(str(error), ephemeral=True)
        return
    await ctx.send("Paused the music.")


@slash_command(name="resume", description="Resume playback if paused")
async def resume_command(ctx: SlashContext):
    if not await _require_music_permission(ctx):
        return
    if not ctx.guild_id:
        await ctx.send("This only works inside a server.", ephemeral=True)
        return
    session = music_manager.active_session(int(ctx.guild_id))
    if not session:
        await ctx.send("I'm not playing anything right now.", ephemeral=True)
        return
    try:
        session.resume()
    except MusicError as error:
        await ctx.send(str(error), ephemeral=True)
        return
    await ctx.send("Resumed playback.")


@slash_command(name="queue", description="Show the current music queue")
async def queue_command(ctx: SlashContext):
    if not ctx.guild_id:
        await ctx.send("This command must be used inside a server.", ephemeral=True)
        return
    session = music_manager.active_session(int(ctx.guild_id))
    if not session or (not session.current and not session.queue):
        await ctx.send("Nothing is queued up right now.")
        return

    lines: List[str] = []
    if session.current:
        lines.append(
            f"**Now playing:** {session.current.title} (`{session.current.pretty_duration}`) — requested by {session.current.requested_by}"
        )
    queued_tracks = list(session.queue)
    if queued_tracks:
        lines.append("")
        lines.append("**Up next:**")
        for index, track in enumerate(queued_tracks[:10], start=1):
            lines.append(f"{index}. {track.title} (`{track.pretty_duration}`) — requested by {track.requested_by}")
        remaining = len(queued_tracks) - 10
        if remaining > 0:
            lines.append(f"...and {remaining} more.")

    await ctx.send("\n".join(lines))


@slash_command(name="stop", description="Stop playback and clear the queue")
async def stop_command(ctx: SlashContext):
    if not await _require_music_permission(ctx):
        return
    if not ctx.guild_id:
        await ctx.send("Use this inside a server.", ephemeral=True)
        return
    guild_id = int(ctx.guild_id)
    session = music_manager.active_session(guild_id)
    if not session:
        await ctx.send("There's no active music session to stop.", ephemeral=True)
        return
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
