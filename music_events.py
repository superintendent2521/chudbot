"""Lavalink event listeners for music playback."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, cast

import lavalink


class LavalinkEvents:
    def __init__(self, manager: Any) -> None:
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
        player = event.player
        if getattr(player, "queue", None) or getattr(player, "is_playing", False):
            return
        await self.manager.schedule_idle(player.guild_id)

    @lavalink.listener(lavalink.QueueEndEvent)
    async def queue_end(self, event: lavalink.QueueEndEvent) -> None:
        await self.manager.schedule_idle(event.player.guild_id)

    @lavalink.listener(lavalink.NodeDisconnectedEvent)
    async def node_disconnected(self, event: lavalink.NodeDisconnectedEvent) -> None:
        await self.manager.runtime.handle_node_disconnect(
            code=getattr(event, "code", None),
            reason=getattr(event, "reason", None),
        )

    @lavalink.listener(lavalink.NodeReadyEvent)
    async def node_ready(self, event: lavalink.NodeReadyEvent) -> None:
        self.manager.runtime.handle_node_ready()

    @lavalink.listener(lavalink.TrackExceptionEvent)
    async def track_exception(self, event: lavalink.TrackExceptionEvent) -> None:
        logger = self.manager.runtime.logger
        player = event.player
        logger.warning(
            "Track exception in guild %s: %s",
            player.guild_id,
            getattr(event, "exception", "Unknown error"),
        )
        if getattr(player, "queue", None):
            if getattr(player, "is_playing", False):
                logger.warning(
                    "Not calling player.play() after track exception in guild %s because playback is still active: "
                    "current=%s queue_size=%s",
                    player.guild_id,
                    getattr(getattr(player, "current", None), "title", None),
                    len(getattr(player, "queue", []) or []),
                )
                return
            logger.info(
                "Advancing queue after track exception in guild %s: current=%s queue_size=%s",
                player.guild_id,
                getattr(getattr(player, "current", None), "title", None),
                len(getattr(player, "queue", []) or []),
            )
            play = getattr(player, "play", None)
            if callable(play):
                await cast(Callable[[], Awaitable[Any]], play)()
        else:
            await self.manager.schedule_idle(player.guild_id)
