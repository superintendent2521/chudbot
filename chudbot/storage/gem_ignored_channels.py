"""Persistent per-guild gem reaction ignore lists."""

from __future__ import annotations

import json
import logging


class GemIgnoredChannelStore:
    def __init__(self, path: str, logger: logging.Logger) -> None:
        self.path = path
        self.logger = logger
        self.entries: dict[int, set[int]] = {}
        self.load()

    def load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as file:
                raw = json.load(file)
        except FileNotFoundError:
            return
        except json.JSONDecodeError as error:
            self.logger.error("Failed to parse gem ignore file %s: %s", self.path, error)
            return

        for raw_guild_id, raw_channel_ids in raw.items() if isinstance(raw, dict) else ():
            try:
                guild_id = int(raw_guild_id)
                channel_ids = {int(channel_id) for channel_id in raw_channel_ids}
            except (TypeError, ValueError):
                self.logger.warning("Ignoring malformed gem ignore entry %s", raw_guild_id)
                continue
            self.entries[guild_id] = channel_ids

    def save(self) -> None:
        try:
            with open(self.path, "w", encoding="utf-8") as file:
                json.dump({str(guild_id): sorted(channel_ids) for guild_id, channel_ids in self.entries.items()}, file, indent=2)
        except Exception as error:
            self.logger.error("Failed to persist gem ignore data: %s", error)

    def add(self, guild_id: int, channel_id: int) -> None:
        self.entries.setdefault(guild_id, set()).add(channel_id)
        self.save()

    def remove(self, guild_id: int, channel_id: int) -> None:
        channel_ids = self.entries.get(guild_id, set())
        channel_ids.discard(channel_id)
        if not channel_ids:
            self.entries.pop(guild_id, None)
        self.save()

    def get(self, guild_id: int) -> set[int]:
        return self.entries.get(guild_id, set())
