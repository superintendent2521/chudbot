"""Owner-only maintenance announcement command."""

from __future__ import annotations

from collections.abc import Iterable as IterableABC
from typing import Any, Callable, Iterable, cast

from interactions import SlashContext, slash_command

from chudbot.command_handler import CommandHandler


AUTHORIZED_USER_ID = 936_029_184_407_519_262
ANNOUNCEMENT = "brunk is going to temporarily be offline"


def _iter_channels(channels: Any) -> list[Any]:
    if channels is None:
        return []
    if isinstance(channels, dict):
        return list(channels.values())
    values = getattr(channels, "values", None)
    if callable(values):
        try:
            values_fn = cast(Callable[[], Iterable[Any]], values)
            return list(values_fn())
        except TypeError:
            pass
    if isinstance(channels, IterableABC):
        return list(channels)
    return []


def _find_general_channels(ctx: SlashContext) -> list[Any]:
    guilds = _iter_channels(getattr(ctx.client, "guilds", None))
    current_guild = getattr(ctx, "guild", None)
    if current_guild is not None:
        guilds.append(current_guild)

    cache = getattr(ctx.client, "cache", None)
    guilds.extend(_iter_channels(getattr(cache, "guild_cache", None)))

    candidates: list[Any] = []
    for guild in guilds:
        candidates.extend(_iter_channels(getattr(guild, "channels", None)))
    candidates.extend(_iter_channels(getattr(cache, "channel_cache", None)))
    candidates.extend(_iter_channels(getattr(cache, "channels", None)))

    general_channels: list[Any] = []
    seen_channel_ids: set[int] = set()
    for channel in candidates:
        if str(getattr(channel, "name", "")).casefold() != "general":
            continue
        if not callable(getattr(channel, "send", None)):
            continue
        try:
            channel_id = int(channel.id)
        except (AttributeError, TypeError, ValueError):
            continue
        if channel_id in seen_channel_ids:
            continue
        seen_channel_ids.add(channel_id)
        general_channels.append(channel)
    return general_channels


def setup(handler: CommandHandler) -> None:
    logger = handler.resources.logger

    @slash_command(
        name="maintenance",
        description="Announce that Brunk will temporarily be offline.",
    )
    async def maintenance_command(ctx: SlashContext):
        if int(ctx.author.id) != AUTHORIZED_USER_ID:
            await ctx.send("You are not authorized to use this command.", ephemeral=True)
            return

        await ctx.defer(ephemeral=True)

        general_channels = _find_general_channels(ctx)
        if not general_channels:
            await ctx.send("I couldn't find any channels named #general.", ephemeral=True)
            return

        sent_count = 0
        for general_channel in general_channels:
            try:
                await general_channel.send(ANNOUNCEMENT)
                sent_count += 1
            except Exception as error:
                logger.error(
                    "Failed to send maintenance announcement in channel %s: %s",
                    getattr(general_channel, "id", "unknown"),
                    error,
                )

        failed_count = len(general_channels) - sent_count
        await ctx.send(
            f"Maintenance announcement sent in {sent_count} #general channel(s); "
            f"{failed_count} failed.",
            ephemeral=True,
        )

    handler.register_slash_command(maintenance_command)
