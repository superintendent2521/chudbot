"""Help command listing the bot's registered slash commands."""

from interactions import SlashContext, slash_command

from chudbot.command_handler import CommandHandler


def _command_details(command: object) -> tuple[str, str]:
    """Return display-safe name and description values from an interaction command."""
    name = str(getattr(command, "name", "unknown"))
    description = str(getattr(command, "description", "No description available."))
    return name, description


def _help_text(commands: list[object]) -> str:
    return "\n".join(_help_lines(commands))


def _help_lines(commands: list[object]) -> list[str]:
    lines = ["**Available commands**"]
    for command in sorted(commands, key=lambda item: _command_details(item)[0]):
        name, description = _command_details(command)
        lines.append(f"`/{name}` — {description}")
    return lines


def _help_pages(commands: list[object], limit: int = 1900) -> list[str]:
    """Split help into Discord-safe messages without splitting command entries."""
    pages: list[str] = []
    current = ""
    for line in _help_lines(commands):
        candidate = f"{current}\n{line}" if current else line
        if current and len(candidate) > limit:
            pages.append(current)
            current = line
        else:
            current = candidate
    if current:
        pages.append(current)
    return pages


def setup(handler: CommandHandler) -> None:
    @slash_command(name="help", description="Show the bot's available commands")
    async def help_command(ctx: SlashContext):
        for page in _help_pages(handler.registered_commands):
            await ctx.send(page)

    handler.register_slash_command(help_command)
