"""JSON-backed FAQ entry storage keyed by guild and command key."""

from __future__ import annotations

import json
import logging
from typing import Dict, List, Optional


class FAQStore:
    def __init__(self, path: str, logger: logging.Logger) -> None:
        self.path = path
        self.logger = logger
        self.entries: Dict[int, Dict[str, Dict[str, str]]] = {}
        self.load()

    def load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as file:
                raw = json.load(file)
        except FileNotFoundError:
            self.entries = {}
            return
        except json.JSONDecodeError as error:
            self.logger.error("Failed to parse FAQ data file %s: %s", self.path, error)
            self.entries = {}
            return

        loaded: Dict[int, Dict[str, Dict[str, str]]] = {}
        if not isinstance(raw, dict):
            self.entries = {}
            return

        for raw_guild_id, raw_entries in raw.items():
            try:
                guild_id = int(raw_guild_id)
            except (TypeError, ValueError):
                self.logger.warning("Ignoring malformed FAQ guild key %s", raw_guild_id)
                continue

            if not isinstance(raw_entries, dict):
                self.logger.warning("Ignoring malformed FAQ entries for guild %s", raw_guild_id)
                continue

            guild_entries: Dict[str, Dict[str, str]] = {}
            for raw_key, raw_entry in raw_entries.items():
                if not isinstance(raw_key, str) or not isinstance(raw_entry, dict):
                    continue
                title = raw_entry.get("title")
                content = raw_entry.get("content")
                if not isinstance(title, str) or not isinstance(content, str):
                    continue
                normalized_key = raw_key.strip().lower()
                if not normalized_key:
                    continue
                guild_entries[normalized_key] = {"title": title, "content": content}

            if guild_entries:
                loaded[guild_id] = guild_entries

        self.entries = loaded

    def save(self) -> None:
        serializable = {
            str(guild_id): entries
            for guild_id, entries in sorted(self.entries.items(), key=lambda item: item[0])
        }
        try:
            with open(self.path, "w", encoding="utf-8") as file:
                json.dump(serializable, file, indent=2, ensure_ascii=True)
        except Exception as error:
            self.logger.error("Failed to persist FAQ data: %s", error)

    def set_entry(self, guild_id: int, key: str, title: str, content: str) -> None:
        normalized_key = key.strip().lower()
        if not normalized_key:
            raise ValueError("FAQ key cannot be empty")
        guild_entries = self.entries.setdefault(guild_id, {})
        guild_entries[normalized_key] = {"title": title.strip(), "content": content.strip()}
        self.save()

    def delete_entry(self, guild_id: int, key: str) -> bool:
        normalized_key = key.strip().lower()
        guild_entries = self.entries.get(guild_id)
        if not guild_entries or normalized_key not in guild_entries:
            return False
        guild_entries.pop(normalized_key)
        if not guild_entries:
            self.entries.pop(guild_id, None)
        self.save()
        return True

    def get_entry(self, guild_id: int, key: str) -> Optional[Dict[str, str]]:
        normalized_key = key.strip().lower()
        guild_entries = self.entries.get(guild_id, {})
        entry = guild_entries.get(normalized_key)
        if not entry:
            return None
        return {"title": entry["title"], "content": entry["content"]}

    def list_keys(self, guild_id: int) -> List[str]:
        guild_entries = self.entries.get(guild_id, {})
        return sorted(guild_entries.keys())
