"""Playback operations executed by a dedicated music bot."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Optional

import lavalink


class MusicWorkerService:
    def __init__(self, runtime: Any, client: Any) -> None:
        self.runtime = runtime
        self.client = client
        self.active_channels: dict[int, int] = {}

    def _player(self, guild_id: int) -> Any:
        client = self.runtime.get_lavalink_client()
        return client.player_manager.get(guild_id) if client else None

    async def handle(self, request: dict) -> dict:
        action = request.get("action")
        guild_id = int(request["guild_id"])
        channel_id = request.get("channel_id")
        channel_id = int(channel_id) if channel_id is not None else self.active_channels.get(guild_id)
        if action == "play" and channel_id is None:
            return {"handled": True, "ok": False, "message": "Join a voice channel first."}

        if action == "play":
            active_channel = self.active_channels.get(guild_id)
            if active_channel is not None and not self.runtime.manager.active_session(guild_id):
                self.active_channels.pop(guild_id, None)
                active_channel = None
            if active_channel is not None and active_channel != channel_id:
                return {"handled": True, "busy": True, "ok": False, "message": "This worker is already playing in another channel."}
        elif channel_id is not None:
            active_channel = self.active_channels.get(guild_id)
            if active_channel is not None and active_channel != channel_id:
                return {"handled": True, "busy": True, "ok": False, "message": "This worker is playing in another channel."}

        if action == "play":
            session = self.runtime.manager.get_session(guild_id)
            session.cancel_idle_timer()
            player = self.runtime.manager.get_player(guild_id)
            await self.runtime.manager.ensure_audio_normalization(player)
            if player.volume != self.runtime.default_player_volume:
                await player.set_volume(self.runtime.default_player_volume)
            channel = SimpleNamespace(id=channel_id, _client=self.client)
            await session.ensure_connected(channel)
            await self.runtime.manager.wait_for_player_connection(guild_id)
            result = await self.runtime.manager.load_tracks(request.get("query", ""), guild_id=guild_id)
            tracks = result.tracks if result.load_type == lavalink.LoadType.PLAYLIST else result.tracks[:1]
            for track in tracks:
                extra = getattr(track, "extra", None)
                if not isinstance(extra, dict):
                    extra = {}
                    track.extra = extra
                extra["requester"] = int(request["user_id"])
                player.add(track)
            self.active_channels[guild_id] = channel_id
            if not player.is_playing:
                await player.play()
            return {"handled": True, "ok": True, "message": f"Queued {len(tracks)} track(s)."}

        player = self._player(guild_id)
        if action == "skip":
            if not player or not player.current:
                return {"handled": True, "ok": False, "message": "There's nothing playing to skip."}
            await player.skip()
            return {"handled": True, "ok": True, "message": "Skipped."}
        if action in {"pause", "resume"}:
            should_pause = action == "pause"
            if not player or not player.is_playing or player.paused == should_pause:
                return {"handled": True, "ok": False, "message": "Nothing is playing right now."}
            await player.set_pause(should_pause)
            return {"handled": True, "ok": True, "message": "Paused." if should_pause else "Resumed."}
        if action == "stop":
            session = self.runtime.manager.active_session(guild_id)
            if session:
                await session.disconnect()
            self.active_channels.pop(guild_id, None)
            return {"handled": True, "ok": True, "message": "Music stopped and the bot left the voice channel."}
        if action == "nowplaying":
            if not player or not player.current:
                return {"handled": True, "ok": False, "message": "Nothing is playing right now."}
            return {"handled": True, "ok": True, "message": f"Now playing: **{player.current.title}**"}
        if action == "queue":
            if not player or (not player.current and not player.queue):
                return {"handled": True, "ok": False, "message": "Nothing is queued up right now."}
            lines = []
            if player.current:
                lines.append(f"**Now playing:** {player.current.title}")
            lines.extend(f"{index}. {track.title}" for index, track in enumerate(player.queue[:10], 1))
            return {"handled": True, "ok": True, "message": "\n".join(lines)}
        return {"handled": True, "ok": False, "message": "Unknown music action."}
