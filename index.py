import logging
import os

from dotenv import load_dotenv
from interactions import Client, Intents

from ai_chat import AiChatService
from command_handler import CommandHandler, CommandResources
from music_runtime import MusicError, MusicRuntime
from reaction_roles import (
    ReactionRoleStore,
    create_reaction_role_listeners,
    member_has_role,
    snowflake_to_int,
)
from voice_logging import create_voice_logging_listeners
from member_join_handler import create_member_join_listeners
from gem_reactions import create_gem_reaction_listeners
from fixupx_link_listener import create_fixupx_listener
from message_delete_logging import create_message_delete_logging_listeners

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("chuds.bot")

MUSIC_IDLE_TIMEOUT = 90
VOICE_CONNECT_TIMEOUT = 15
DEFAULT_PLAYER_VOLUME = 50
REACTION_ROLE_ADMIN_ROLE_ID = 1_434_633_532_436_648_126
DEFAULT_REACTION_ROLE_EMOJI = "🥀"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REACTION_ROLE_DATA_FILE = os.path.join(BASE_DIR, "reaction_roles.json")

ENVIRONMENT = "main"  # or 'dev'
BOT_TOKEN = os.getenv(f"BOT_TOKEN_{ENVIRONMENT.upper()}")

LOG_CHANNEL_ID_RAW = os.getenv("LOG_CHANNEL_ID")
if LOG_CHANNEL_ID_RAW is None:
    raise RuntimeError("LOG_CHANNEL_ID is missing from environment.")
LOG_CHANNEL_ID_SANITIZED = "".join(ch for ch in LOG_CHANNEL_ID_RAW if ch.isdigit())
if not LOG_CHANNEL_ID_SANITIZED:
    raise ValueError(f"LOG_CHANNEL_ID must contain digits, got {LOG_CHANNEL_ID_RAW!r}")
try:
    LOG_CHANNEL_ID = int(LOG_CHANNEL_ID_SANITIZED)
except ValueError as exc:
    raise ValueError(f"LOG_CHANNEL_ID must be numeric, got {LOG_CHANNEL_ID_RAW!r}") from exc

GEM_CHANNEL_ID = 1447398899588530196
MESSAGE_DELETE_LOG_CHANNEL_ID = 1_470_612_259_721_052_300

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_SITE_URL = os.getenv("OPENROUTER_SITE_URL", "")
OPENROUTER_APP_NAME = os.getenv("OPENROUTER_APP_NAME", "Chuds Discord Bot")
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
AI_MODEL_ID = "z-ai/glm-4.5-air:free"
SYSTEM_PROMPT = (
    "You are a bot that is replicating jessie pinkman from the show breaking bad, talk like him, use his slang and mannerisms. if you dont know an answer, say a joke as a response, you must use yo in every sentence, yo"
    "Keep answers concise when possible and follow Discord formatting rules."
)
MAX_MEMORY_MESSAGES = 20

MUSIC_DJ_ROLE_ID_RAW = os.getenv("MUSIC_DJ_ROLE_ID")
MUSIC_DJ_ROLE_ID = None
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

reaction_role_store = ReactionRoleStore(
    REACTION_ROLE_DATA_FILE,
    DEFAULT_REACTION_ROLE_EMOJI,
    logger,
)
music_runtime = MusicRuntime(
    logger=logger,
    lavalink_host=LAVALINK_HOST,
    lavalink_port=LAVALINK_PORT,
    lavalink_password=LAVALINK_PASSWORD,
    lavalink_region=LAVALINK_REGION,
    lavalink_ssl=LAVALINK_SSL,
    music_available=MUSIC_AVAILABLE,
    music_dj_role_id=MUSIC_DJ_ROLE_ID,
    idle_timeout=MUSIC_IDLE_TIMEOUT,
    voice_connect_timeout=VOICE_CONNECT_TIMEOUT,
    default_player_volume=DEFAULT_PLAYER_VOLUME,
)
ai_service = AiChatService(
    logger=logger,
    openrouter_api_url=OPENROUTER_API_URL,
    openrouter_api_key=OPENROUTER_API_KEY,
    openrouter_site_url=OPENROUTER_SITE_URL,
    openrouter_app_name=OPENROUTER_APP_NAME,
    system_prompt=SYSTEM_PROMPT,
    model_id=AI_MODEL_ID,
    max_memory_messages=MAX_MEMORY_MESSAGES,
)

bot = Client(
    token=BOT_TOKEN,
    intents=Intents.DEFAULT
    | Intents.GUILD_VOICE_STATES
    | Intents.GUILD_MESSAGE_REACTIONS
    | Intents.GUILD_MEMBERS
    | Intents.MESSAGE_CONTENT
    | Intents.GUILD_MESSAGES
    )

command_resources = CommandResources(
    environment=ENVIRONMENT,
    reaction_role_admin_role_id=REACTION_ROLE_ADMIN_ROLE_ID,
    default_reaction_role_emoji=DEFAULT_REACTION_ROLE_EMOJI,
    reaction_role_store=reaction_role_store,
    member_has_role=member_has_role,
    snowflake_to_int=snowflake_to_int,
    require_lavalink=music_runtime.require_lavalink,
    require_music_permission=music_runtime.require_music_permission,
    format_bytes=music_runtime.format_bytes,
    format_duration=music_runtime.format_duration,
    format_uptime=music_runtime.format_uptime,
    get_lavalink_client=music_runtime.get_lavalink_client,
    music_manager=music_runtime.manager,
    default_player_volume=music_runtime.default_player_volume,
    get_voice_channel=music_runtime.get_voice_channel,
    logger=logger,
    music_error_cls=MusicError,
)
command_handler = CommandHandler(bot, command_resources)
command_handler.load_from_package("commands")

for listener in create_reaction_role_listeners(reaction_role_store, logger):
    bot.add_listener(listener)
for listener in music_runtime.create_gateway_listeners():
    bot.add_listener(listener)
for listener in create_voice_logging_listeners(LOG_CHANNEL_ID, logger):
    bot.add_listener(listener)
for listener in create_member_join_listeners(logger):
    bot.add_listener(listener)
for listener in create_gem_reaction_listeners(GEM_CHANNEL_ID, logger):
    bot.add_listener(listener)
for listener in create_fixupx_listener(logger):
    bot.add_listener(listener)
for listener in create_message_delete_logging_listeners(MESSAGE_DELETE_LOG_CHANNEL_ID, logger):
    bot.add_listener(listener)
bot.add_listener(ai_service.create_listener())

logger.info("Environment: %s", ENVIRONMENT)
bot.start()
