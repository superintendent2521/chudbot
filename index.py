import interactions
import os
import aiohttp
import logging
from typing import Dict, List
from dotenv import load_dotenv
from interactions import Client, Intents, listen, slash_command, SlashContext
from interactions.api.events.discord import MessageCreate, VoiceUserJoin, VoiceUserLeave

# Load environment variables
load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("chuds.bot")

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

if not OPENROUTER_API_KEY:
    logger.warning("OPENROUTER_API_KEY not set. AI chat feature disabled.")

user_memories: Dict[int, List[dict]] = {}

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
    server_address = "agartha.mc.gg"
    api_url = f"https://api.mcsrvstat.us/3/{server_address}"
    
    try:
        async with aiohttp.ClientSession() as session:
            headers = {"User-Agent": "DiscordBot/1.0 (contact:admin@superintendent.me .superintendent discord)"}
            async with session.get(api_url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    
                    if data.get('online', False):
                        motd = "\n".join(data['motd']['clean']) if 'motd' in data else "No MOTD available"
                        players = f"{data['players']['online']}/{data['players']['max']}" if 'players' in data else "Unknown"
                        version = data.get('version', 'Unknown')
                        
                        # Build player list
                        player_list = ""
                        if 'players' in data and 'list' in data['players'] and data['players']['list']:
                            player_list = "\n**Players online:**\n" + "\n".join(
                                [f"- {player['name']}" for player in data['players']['list']]
                            )
                        
                        await ctx.send(
                            f"✅ **{server_address} is ONLINE**\n"
                            f"**MOTD:** {motd}\n"
                            f"**Players:** {players}"
                            f"{player_list}\n"
                        )
                    else:
                        await ctx.send(f"❌ **{server_address} is OFFLINE**")
                else:
                    await ctx.send(f"⚠️ Failed to check server status (HTTP {response.status})")
    except Exception as e:
        await ctx.send(f"⚠️ Error checking server status: {str(e)}")





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
