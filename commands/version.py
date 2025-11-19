"""Version command."""

from interactions import SlashContext, slash_command

from command_handler import CommandHandler


def setup(handler: CommandHandler) -> None:
    environment = handler.resources.environment

    @slash_command(name="version", description="My first command :)")
    async def version_command(ctx: SlashContext):
        await ctx.send(f"version: {environment}")

    handler.register_slash_command(version_command)

