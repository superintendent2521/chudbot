"""Bot statistics command."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterable as IterableABC
from typing import Any, Callable, Iterable, Optional, cast

from interactions import SlashContext, slash_command

from command_handler import CommandHandler


def _iter_values(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, dict):
        return list(value.values())

    values = getattr(value, "values", None)
    if callable(values):
        try:
            values_fn = cast(Callable[[], Iterable[Any]], values)
            return list(values_fn())
        except TypeError:
            pass

    if isinstance(value, IterableABC):
        return list(value)
    return []


def _object_id(value: Any) -> Optional[int]:
    raw_id = getattr(value, "id", None)
    if raw_id is None:
        return None
    try:
        return int(raw_id)
    except (TypeError, ValueError):
        return None


def _cache_collection(bot: Any, names: Iterable[str]) -> list[Any]:
    cache = getattr(bot, "cache", None)
    if cache is None:
        return []

    for name in names:
        collection = getattr(cache, name, None)
        values = _iter_values(collection)
        if values:
            return values
    return []


def _get_guilds(bot: Any) -> list[Any]:
    direct_guilds = _iter_values(getattr(bot, "guilds", None))
    if direct_guilds:
        return direct_guilds
    return _cache_collection(bot, ("guild_cache", "guilds"))


def _get_member_count(guild: Any) -> int:
    member_count = getattr(guild, "member_count", None)
    if member_count is not None:
        try:
            return int(member_count)
        except (TypeError, ValueError):
            pass

    members = _iter_values(getattr(guild, "members", None))
    return len(members)


def _get_channel_guild_id(channel: Any) -> Optional[int]:
    guild_id = getattr(channel, "guild_id", None)
    if guild_id is None:
        guild = getattr(channel, "guild", None)
        guild_id = getattr(guild, "id", None)
    try:
        return int(guild_id) if guild_id is not None else None
    except (TypeError, ValueError):
        return None


def _get_total_channels(bot: Any, guilds: list[Any]) -> int:
    channel_ids: set[int] = set()
    fallback_count = 0

    for guild in guilds:
        for channel in _iter_values(getattr(guild, "channels", None)):
            channel_id = _object_id(channel)
            if channel_id is None:
                fallback_count += 1
            else:
                channel_ids.add(channel_id)

    guild_ids = {_object_id(guild) for guild in guilds}
    guild_ids.discard(None)
    for channel in _cache_collection(bot, ("channel_cache", "channels")):
        channel_guild_id = _get_channel_guild_id(channel)
        if guild_ids and channel_guild_id not in guild_ids:
            continue

        channel_id = _object_id(channel)
        if channel_id is None:
            fallback_count += 1
        else:
            channel_ids.add(channel_id)

    return len(channel_ids) + fallback_count


def _current_rss_bytes() -> Optional[int]:
    status_path = "/proc/self/status"
    if os.path.exists(status_path):
        try:
            with open(status_path, "r", encoding="utf-8") as status_file:
                for line in status_file:
                    if line.startswith("VmRSS:"):
                        parts = line.split()
                        if len(parts) >= 2:
                            return int(parts[1]) * 1024
        except OSError:
            pass

    try:
        psutil_module = __import__("psutil")
        process_cls = getattr(psutil_module, "Process")
        process = process_cls(os.getpid())
        return int(process.memory_info().rss)
    except (ImportError, AttributeError, OSError):
        pass

    try:
        resource_module = __import__("resource")
    except ImportError:
        return None

    getrusage = getattr(resource_module, "getrusage", None)
    rusage_self = getattr(resource_module, "RUSAGE_SELF", None)
    if not callable(getrusage) or rusage_self is None:
        return None

    try:
        usage = getrusage(rusage_self).ru_maxrss
    except OSError:
        return None
    if sys.platform == "darwin":
        return int(usage)
    return int(usage) * 1024


def _format_bytes(num_bytes: Optional[int]) -> str:
    if num_bytes is None:
        return "Unknown"

    value = float(max(num_bytes, 0))
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return "0 B"


def _format_ping(bot: Any) -> str:
    for attr_name in ("latency", "average_latency"):
        latency = getattr(bot, attr_name, None)
        if callable(latency):
            latency = latency()
        if latency is None:
            continue
        if not isinstance(latency, (int, float, str)):
            continue
        try:
            latency_float = float(latency)
        except (TypeError, ValueError):
            continue

        latency_ms = latency_float if latency_float > 10 else latency_float * 1000
        return f"{latency_ms:.0f} ms"

    return "Unknown"


def _player_is_playing(player: Any) -> bool:
    if getattr(player, "paused", False):
        return False
    if getattr(player, "current", None) is None:
        return False

    is_playing = getattr(player, "is_playing", None)
    if is_playing is None:
        return True
    return bool(is_playing)


def _get_playing_music_channel_count(lavalink_client: Any) -> int:
    if lavalink_client is None:
        return 0

    player_manager = getattr(lavalink_client, "player_manager", None)
    players = []
    for attr_name in ("players", "_players"):
        players = _iter_values(getattr(player_manager, attr_name, None))
        if players:
            break
    if not players:
        players = _iter_values(player_manager)

    channel_ids: set[int] = set()
    fallback_count = 0
    for player in players:
        if not _player_is_playing(player):
            continue
        channel_id = getattr(player, "channel_id", None)
        if channel_id is None:
            fallback_count += 1
            continue
        try:
            channel_ids.add(int(channel_id))
        except (TypeError, ValueError):
            fallback_count += 1

    return len(channel_ids) + fallback_count


def setup(handler: CommandHandler) -> None:
    bot = handler.bot
    get_lavalink_client = handler.resources.get_lavalink_client
    economy_store = handler.resources.economy_store
    logger = handler.resources.logger

    @slash_command(name="statistics", description="Show bot statistics")
    async def statistics_command(ctx: SlashContext):
        guilds = _get_guilds(bot)
        total_users = sum(_get_member_count(guild) for guild in guilds)
        total_channels = _get_total_channels(bot, guilds)
        playing_music_channels = _get_playing_music_channel_count(get_lavalink_client())

        try:
            economy = await economy_store.statistics()
            economy_lines = [
                "",
                "**Economy Statistics**",
                f"Participating servers: {economy.guilds:,}",
                f"Accounts: {economy.accounts:,}",
                f"Coins in circulation: {economy.total_balance:,}",
                f"Average balance: {economy.average_balance:,}",
                f"Highest balance: {economy.highest_balance:,}",
                f"Queued economy logs: {economy.queued_logs:,}",
                f"Dropped economy logs: {economy.dropped_logs:,}",
            ]
        except Exception as error:
            logger.warning("Unable to load economy statistics: %s", error)
            economy_lines = ["", "**Economy Statistics**", "Currently unavailable"]

        lines = [
            "**Bot Statistics**",
            f"Servers: {len(guilds):,}",
            f"Users across servers: {total_users:,}",
            f"Channels: {total_channels:,}",
            f"RAM usage: {_format_bytes(_current_rss_bytes())}",
            f"Playing music in {playing_music_channels:,} channels",
            f"Ping: {_format_ping(bot)}",
        ]
        lines.extend(economy_lines)
        await ctx.send("\n".join(lines))

    handler.register_slash_command(statistics_command)
