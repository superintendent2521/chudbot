"""Reaction role storage and listeners."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional, Tuple, Union

from interactions import Member, listen
from interactions.api.events.discord import MessageReactionAdd, MessageReactionRemove

ReactionRoleEntry = Dict[str, Union[int, str]]


def snowflake_to_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    candidate = getattr(value, "id", None)
    if candidate is not None:
        try:
            return int(candidate)
        except (TypeError, ValueError):
            return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def member_has_role(member: Optional[Member], target_role_id: int) -> bool:
    if not member or not target_role_id:
        return False
    try:
        for role in getattr(member, "roles", []) or []:
            role_id = snowflake_to_int(getattr(role, "id", role))
            if role_id == target_role_id:
                return True
    except Exception:
        return False
    return False


class ReactionRoleStore:
    def __init__(self, path: str, default_emoji: str, logger: logging.Logger) -> None:
        self.path = path
        self.default_emoji = default_emoji
        self.logger = logger
        self.entries: Dict[int, ReactionRoleEntry] = {}
        self.load()

    def load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as file:
                raw = json.load(file)
        except FileNotFoundError:
            self.entries = {}
            return
        except json.JSONDecodeError as error:
            self.logger.error("Failed to parse reaction role file %s: %s", self.path, error)
            self.entries = {}
            return

        loaded: Dict[int, ReactionRoleEntry] = {}
        if isinstance(raw, dict):
            for raw_message_id, payload in raw.items():
                if not isinstance(payload, dict):
                    continue
                try:
                    message_id = int(raw_message_id)
                    guild_id = int(payload.get("guild_id"))
                    channel_id = int(payload.get("channel_id"))
                    role_id = int(payload.get("role_id"))
                except (TypeError, ValueError):
                    self.logger.warning("Ignoring malformed reaction role entry %s", raw_message_id)
                    continue
                emoji = str(payload.get("emoji") or self.default_emoji)
                loaded[message_id] = {
                    "guild_id": guild_id,
                    "channel_id": channel_id,
                    "role_id": role_id,
                    "emoji": emoji,
                }
        self.entries = loaded

    def save(self) -> None:
        serializable = {str(message_id): entry for message_id, entry in self.entries.items()}
        try:
            with open(self.path, "w", encoding="utf-8") as file:
                json.dump(serializable, file, indent=2)
        except Exception as error:
            self.logger.error("Failed to persist reaction role data: %s", error)

    def set_entry(self, message_id: int, *, guild_id: int, channel_id: int, role_id: int, emoji: str) -> None:
        self.entries[message_id] = {
            "guild_id": guild_id,
            "channel_id": channel_id,
            "role_id": role_id,
            "emoji": emoji,
        }
        self.save()

    def remove_entry(self, message_id: int) -> None:
        if message_id in self.entries:
            self.entries.pop(message_id)
            self.save()

    def get_entry(self, message_id: int) -> Optional[ReactionRoleEntry]:
        return self.entries.get(message_id)

    def all_entries(self) -> Dict[int, ReactionRoleEntry]:
        return dict(self.entries)


async def _get_member_from_reaction_event(event, logger: logging.Logger) -> Optional[Member]:
    author = getattr(event, "author", None)
    if isinstance(author, Member):
        return author

    message = getattr(event, "message", None)
    guild_id = None
    if message is not None:
        guild = getattr(message, "guild", None)
        if guild is not None:
            guild_id = snowflake_to_int(getattr(guild, "id", guild))
        if guild_id is None:
            guild_id = snowflake_to_int(getattr(message, "_guild_id", None))
    if guild_id is None:
        guild_id = snowflake_to_int(getattr(event, "guild_id", None))

    user_id = snowflake_to_int(getattr(author, "id", None))

    if guild_id is None or user_id is None:
        return None

    client = getattr(event, "client", None)
    if not client:
        return None
    try:
        return await client.fetch_member(guild_id, user_id)
    except Exception as error:
        logger.warning(
            "Unable to fetch member %s in guild %s for reaction role: %s",
            user_id,
            guild_id,
            error,
        )
        return None


def _emoji_matches(emoji_obj, target: str) -> bool:
    if not emoji_obj or not target:
        return False
    name = getattr(emoji_obj, "name", None)
    if name and name == target:
        return True
    emoji_id = getattr(emoji_obj, "id", None)
    if emoji_id and str(emoji_id) == target:
        return True
    try:
        return str(emoji_obj) == target
    except Exception:
        return False


async def _handle_reaction_role_event(
    event,
    store: ReactionRoleStore,
    logger: logging.Logger,
    *,
    grant: bool,
) -> None:
    message_obj = getattr(event, "message", None)
    reaction_obj = getattr(event, "reaction", None)
    raw_message_id = None
    for candidate in (
        getattr(message_obj, "id", None),
        getattr(reaction_obj, "message_id", None),
        getattr(event, "message_id", None),
    ):
        if candidate:
            raw_message_id = candidate
            break

    message_id = raw_message_id
    emoji = getattr(event, "emoji", None)
    if not message_id or not emoji:
        logger.debug("Reaction event missing message/emoji: %s", event)
        return
    try:
        message_id_int = int(getattr(message_id, "id", message_id))
    except (TypeError, ValueError):
        logger.debug("Unable to parse message id %r for reaction event", message_id)
        return
    entry = store.get_entry(message_id_int)
    if not entry:
        logger.debug("No reaction role entry for message %s", message_id_int)
        return
    if not _emoji_matches(emoji, str(entry.get("emoji", ""))):
        logger.debug("Emoji %s did not match configured %s for message %s", emoji, entry.get("emoji"), message_id_int)
        return
    member = await _get_member_from_reaction_event(event, logger)
    if not member:
        logger.debug("Failed to resolve member for reaction event on message %s", message_id_int)
        return
    user = getattr(member, "user", None)
    if user and getattr(user, "bot", False):
        return
    role_id = int(entry["role_id"])
    action = "opt-in" if grant else "opt-out"
    try:
        if grant:
            if member.has_role(role_id):
                return
            await member.add_role(role_id, reason=f"Reaction role {action}")
            logger.info(
                "Granted role %s to %s (%s) via reaction message %s",
                role_id,
                member.display_name,
                member.id,
                message_id_int,
            )
        else:
            if not member.has_role(role_id):
                return
            await member.remove_role(role_id, reason=f"Reaction role {action}")
            logger.info(
                "Removed role %s from %s via reaction message %s",
                role_id,
                getattr(member, "display_name", member.id),
                message_id_int,
            )
    except Exception as error:
        logger.error(
            "Failed to update reaction role %s for member %s (%s): %s",
            role_id,
            getattr(member, "display_name", member.id),
            action,
            error,
        )


def create_reaction_role_listeners(
    store: ReactionRoleStore, logger: logging.Logger
) -> Tuple:  # Tuple of Listener, but keep generic for typing simplicity
    async def handler(event, grant: bool) -> None:
        await _handle_reaction_role_event(event, store, logger, grant=grant)

    @listen(MessageReactionAdd)
    async def on_reaction_role_add(event: MessageReactionAdd):
        await handler(event, True)

    @listen(MessageReactionRemove)
    async def on_reaction_role_remove(event: MessageReactionRemove):
        await handler(event, False)

    return on_reaction_role_add, on_reaction_role_remove

