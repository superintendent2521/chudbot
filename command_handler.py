"""Command loader for the interactions-based bot."""

from __future__ import annotations

import importlib
import logging
import pkgutil
from dataclasses import dataclass
from typing import Any, Callable, Optional, Sequence

from interactions import Client, InteractionCommand
from interactions.models.internal.listener import Listener


@dataclass(frozen=True)
class CommandResources:
    """Shared objects and helpers that command modules depend on."""

    environment: str
    reaction_role_admin_role_id: int
    default_reaction_role_emoji: str
    reaction_role_store: "ReactionRoleStore" # pyright: ignore[reportUndefinedVariable]
    member_has_role: Callable[[Optional[Member], int], bool] # type: ignore
    snowflake_to_int: Callable[[Any], Optional[int]] # type: ignore
    require_lavalink: Callable[[SlashContext], Awaitable[bool]] # type: ignore
    require_music_permission: Callable[[SlashContext], Awaitable[bool]] # type: ignore
    format_bytes: Callable[[Optional[int]], str] # type: ignore
    format_duration: Callable[[Optional[int]], str] # type: ignore
    format_uptime: Callable[[Optional[int]], str] # type: ignore
    get_lavalink_client: Callable[[], Optional["lavalink.Client"]] # type: ignore
    music_manager: "MusicManager" # type: ignore
    default_player_volume: int # type: ignore
    get_voice_channel: Callable[[Member], Optional[VoiceChannel]] # type: ignore
    logger: logging.Logger # type: ignore
    music_error_cls: type # type: ignore


class CommandHandler:
    """Loads command modules and registers them with the bot."""

    def __init__(self, bot: Client, resources: CommandResources) -> None:
        self.bot = bot
        self.resources = resources

    def register_slash_command(self, command: InteractionCommand) -> None:
        """Register a slash command with the client."""
        self.bot.add_interaction(command)

    def register_listener(self, listener: Listener) -> None:
        """Register an event listener with the client."""
        self.bot.add_listener(listener)

    def load_modules(self, module_names: Sequence[str]) -> None:
        """Load an explicit sequence of command modules."""
        for module_name in module_names:
            self._load_module(module_name)

    def load_from_package(self, package_name: str) -> None:
        """Load every public module from the provided package."""
        package = importlib.import_module(package_name)
        package_path = getattr(package, "__path__", None)
        if package_path is None:
            raise ValueError(f"{package_name} is not a package")
        prefix = f"{package.__name__}."
        for module_info in pkgutil.iter_modules(package_path, prefix):
            module_basename = module_info.name.split(".")[-1]
            if module_basename.startswith("_"):
                continue
            self._load_module(module_info.name)

    def _load_module(self, module_name: str) -> None:
        module = importlib.import_module(module_name)
        setup = getattr(module, "setup", None)
        if not callable(setup):
            raise RuntimeError(f"Command module {module_name} is missing a setup(handler) function.")
        setup(self)

