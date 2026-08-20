"""Commands for linking a Discord account to the browser dashboard."""

from __future__ import annotations

from interactions import OptionType, SlashContext, slash_command, slash_option

from chudbot.command_handler import CommandHandler
from chudbot.economy.responses import defer_ping, send_ping


def setup(handler: CommandHandler) -> None:
    store = handler.resources.economy_store

    @slash_command(name="register", description="Link your Discord account to the Chudbot web dashboard")
    @slash_option(
        name="code",
        description="The six-character code shown by the web dashboard",
        required=True,
        opt_type=OptionType.STRING,
        min_length=6,
        max_length=6,
    )
    async def register_command(ctx: SlashContext, code: str):
        guild = getattr(ctx, "guild_id", None)
        if guild is None:
            await send_ping(ctx, "Run this command inside a server so I know which server to link.", ephemeral=True)
            return
        await defer_ping(ctx)
        linked = await store.complete_web_registration(code, int(guild), int(ctx.author.id))
        if linked:
            await send_ping(ctx, "Your account is linked. You can return to the web dashboard now.", ephemeral=True)
        else:
            await send_ping(ctx, "I couldn't find that pending code. Generate a fresh code on the dashboard and try again.", ephemeral=True)

    handler.register_slash_command(register_command)
