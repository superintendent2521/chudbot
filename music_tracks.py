"""Track-query parsing and Lavalink loading."""

from __future__ import annotations

from typing import Any, List, Optional, Tuple
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import lavalink

from music_errors import MusicError


YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
}


class TrackLoader:
    """Resolve user queries and load tracks without owning player state."""

    def __init__(self, runtime: Any, sessions: Any) -> None:
        self.runtime = runtime
        self.sessions = sessions

    @staticmethod
    def _is_youtube_video_id(value: str) -> bool:
        return len(value) == 11 and all(char.isalnum() or char in {"_", "-"} for char in value)

    @staticmethod
    def _append_unique(items: List[str], value: Optional[str]) -> None:
        if value and value not in items:
            items.append(value)

    @staticmethod
    def _is_youtube_url(query: str) -> bool:
        parsed = urlparse(query)
        host = parsed.netloc.lower().split("@")[-1].split(":")[0]
        return host in YOUTUBE_HOSTS

    def _youtube_identifiers(self, query: str) -> List[str]:
        parsed = urlparse(query)
        host = parsed.netloc.lower().split("@")[-1].split(":")[0]
        if host not in YOUTUBE_HOSTS:
            return [query]

        path = parsed.path.rstrip("/")
        path_parts = [part for part in parsed.path.split("/") if part]
        params = parse_qs(parsed.query)
        video_id: Optional[str] = None
        playlist_id: Optional[str] = None

        if host in {"youtu.be", "www.youtu.be"} and path_parts:
            video_id = path_parts[0]
        elif path == "/watch":
            video_id = params.get("v", [None])[0]
            playlist_id = params.get("list", [None])[0]
        elif path_parts and path_parts[0] in {"shorts", "live", "embed"} and len(path_parts) > 1:
            video_id = path_parts[1]
        elif path == "/playlist":
            playlist_id = params.get("list", [None])[0]

        identifiers: List[str] = []
        if video_id and self._is_youtube_video_id(video_id):
            canonical_video = f"https://www.youtube.com/watch?v={video_id}"
            self._append_unique(identifiers, canonical_video)
            self._append_unique(identifiers, query)
            self._append_unique(identifiers, f"ytsearch:{video_id}")
            return identifiers

        if playlist_id:
            playlist_query = urlencode({"list": playlist_id})
            playlist_url = urlunparse(("https", "www.youtube.com", "/playlist", "", playlist_query, ""))
            self._append_unique(identifiers, playlist_url)
            self._append_unique(identifiers, query)
            return identifiers

        return [query]

    def _load_identifiers(self, query: str) -> Tuple[List[str], bool]:
        normalized = query.strip()
        if not normalized:
            raise MusicError("Please provide a search term or link.")
        if not normalized.startswith(("http://", "https://")):
            return [f"ytsearch:{normalized}"], False
        identifiers = self._youtube_identifiers(normalized)
        return identifiers, self._is_youtube_url(normalized)

    async def _get_tracks(self, identifier: str, guild_id: Optional[int]) -> lavalink.LoadResult:
        client = self.runtime.get_lavalink_client()
        if not client:
            raise MusicError("Music playback isn't configured.")
        try:
            result = await client.get_tracks(identifier)
        except lavalink.errors.ClientError as error:
            if not self.runtime._is_no_available_nodes_error(error):
                self.runtime.logger.warning(
                    "Lavalink rejected track identifier %r: %s",
                    identifier,
                    error or type(error).__name__,
                    exc_info=True,
                )
                raise MusicError(
                    "The Lavalink node rejected the track request. Check that the bot and "
                    "Lavalink versions, password, and REST endpoint configuration match."
                ) from error

            self.runtime.logger.warning("Lavalink reported no available nodes while loading tracks: %s", error)
            if not self.runtime._can_retry_no_nodes_reconnect():
                raise MusicError(
                    "Lavalink is unavailable right now. Backing off retries for another "
                    f"{self.runtime._remaining_no_nodes_backoff():.0f}s."
                )
            reconnected = await self.runtime.reconnect_lavalink(reason="no available nodes during track load")
            if not reconnected:
                raise MusicError("Lavalink is unavailable right now. Try again in a moment.")

            if guild_id is not None:
                session = self.sessions.get(guild_id)
                if session:
                    try:
                        await session.reconnect_voice_state()
                    except Exception as reconnect_error:
                        self.runtime.logger.warning(
                            "Failed to refresh voice state for guild %s after Lavalink reconnect: %s",
                            guild_id,
                            reconnect_error,
                        )

            client = self.runtime.get_lavalink_client()
            if not client:
                raise MusicError("Lavalink is unavailable right now. Try again in a moment.")
            try:
                result = await client.get_tracks(identifier)
            except lavalink.errors.ClientError as retry_error:
                if self.runtime._is_no_available_nodes_error(retry_error):
                    raise MusicError("Lavalink is unavailable right now. Try again in a moment.")
                self.runtime.logger.warning(
                    "Lavalink rejected retried track identifier %r: %s",
                    identifier,
                    retry_error or type(retry_error).__name__,
                    exc_info=True,
                )
                raise MusicError(
                    "The Lavalink node rejected the track request. Check that the bot and "
                    "Lavalink versions, password, and REST endpoint configuration match."
                ) from retry_error
        return result

    async def load_tracks(self, query: str, guild_id: Optional[int] = None) -> lavalink.LoadResult:
        identifiers, is_youtube_link = self._load_identifiers(query)
        last_error: Optional[str] = None
        saw_lavalink_error = False

        for identifier in identifiers:
            result = await self._get_tracks(identifier, guild_id)
            if result.load_type == lavalink.LoadType.ERROR:
                saw_lavalink_error = True
                last_error = str(result.error)
                self.runtime.logger.warning(
                    "Lavalink failed to load identifier %r: %s",
                    identifier,
                    result.error,
                )
                continue
            if result.load_type == lavalink.LoadType.EMPTY or not result.tracks:
                last_error = "No matches found for that query."
                continue
            return result

        if is_youtube_link and saw_lavalink_error:
            raise MusicError(
                "Lavalink couldn't resolve that YouTube link. I tried the cleaned link and available fallback; "
                "if this keeps happening, update the Lavalink node's YouTube source plugin/config. "
                f"Last error: {last_error}"
            )
        if last_error:
            if saw_lavalink_error:
                raise MusicError(f"Lavalink error: {last_error}")
            raise MusicError(last_error)
        raise MusicError("No matches found for that query.")
