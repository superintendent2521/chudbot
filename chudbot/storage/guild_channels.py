"""JSON-backed guild -> channel configuration storage."""

from __future__ import annotations

import json
import logging
from typing import Dict, Optional


class GuildChannelStore:
    def __init__(self, path: str, logger: logging.Logger) -> None:
        self.path = path
        self.logger = logger
        self.entries: Dict[int, int] = {}
        self.load()

    def load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as file:
                raw = json.load(file)
        except FileNotFoundError:
            self.entries = {}
            return
        except json.JSONDecodeError as error:
            self.logger.error("Failed to parse guild channel file %s: %s", self.path, error)
            self.entries = {}
            return

        loaded: Dict[int, int] = {}
        if isinstance(raw, dict):
            for raw_guild_id, raw_channel_id in raw.items():
                try:
                    guild_id = int(raw_guild_id)
                    channel_id = int(raw_channel_id)
                except (TypeError, ValueError):
                    self.logger.warning("Ignoring malformed guild channel entry %s", raw_guild_id)
                    continue
                loaded[guild_id] = channel_id
        self.entries = loaded

    def save(self) -> None:
        serializable = {str(guild_id): channel_id for guild_id, channel_id in self.entries.items()}
        try:
            with open(self.path, "w", encoding="utf-8") as file:
                json.dump(serializable, file, indent=2)
        except Exception as error:
            self.logger.error("Failed to persist guild channel data: %s", error)

    def set_channel(self, guild_id: int, channel_id: int) -> None:
        self.entries[guild_id] = channel_id
        self.save()

    def clear_channel(self, guild_id: int) -> None:
        if guild_id in self.entries:
            self.entries.pop(guild_id)
            self.save()

    def get_channel_id(self, guild_id: int) -> Optional[int]:
        return self.entries.get(guild_id)
