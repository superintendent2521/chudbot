"""Controller-side routing for dedicated music bot workers."""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

import aiohttp


class MusicWorkerPool:
    """Route one voice channel to one worker bot for the life of its session."""

    def __init__(self, urls: str, secret: str, logger: Any) -> None:
        self.urls = [url.strip().rstrip("/") for url in urls.split(",") if url.strip()]
        self.secret = secret
        self.logger = logger
        self.assignments: Dict[tuple[int, int], str] = {}
        self._next_worker = 0

    @classmethod
    def from_environment(cls, logger: Any) -> "MusicWorkerPool":
        return cls(
            os.getenv("MUSIC_WORKER_URLS", ""),
            os.getenv("MUSIC_WORKER_SECRET", ""),
            logger,
        )

    @property
    def enabled(self) -> bool:
        return bool(self.urls and self.secret)

    async def execute(
        self,
        *,
        action: str,
        guild_id: int,
        channel_id: Optional[int],
        user_id: int,
        query: Optional[str] = None,
    ) -> dict:
        if not self.enabled:
            return {"handled": False}

        key = (guild_id, channel_id or 0)
        candidates = []
        assigned = self.assignments.get(key)
        if assigned:
            candidates.append(assigned)
        candidates.extend(url for url in self.urls if url not in candidates)

        payload = {
            "action": action,
            "guild_id": guild_id,
            "channel_id": channel_id,
            "user_id": user_id,
            "query": query,
        }
        headers = {"Authorization": f"Bearer {self.secret}"}

        for url in candidates:
            try:
                timeout = aiohttp.ClientTimeout(total=30)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(f"{url}/command", json=payload, headers=headers) as response:
                        result = await response.json()
                        if response.status == 200 and result.get("handled", True):
                            self.assignments[key] = url
                            return result
                        if response.status == 409:
                            continue
                        return result
            except (aiohttp.ClientError, TimeoutError) as error:
                self.logger.warning("Music worker %s failed for %s: %s", url, action, error)

        return {"handled": True, "ok": False, "message": "All music workers are busy or unavailable."}

    def release(self, guild_id: int, channel_id: Optional[int]) -> None:
        self.assignments.pop((guild_id, channel_id or 0), None)

    def channel_for_guild(self, guild_id: int) -> Optional[int]:
        for assigned_guild, channel_id in self.assignments:
            if assigned_guild == guild_id and channel_id:
                return channel_id
        return None
