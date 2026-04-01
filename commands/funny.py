"""Small joke commands and text triggers."""

from __future__ import annotations

from interactions import SlashContext, listen, slash_command
from interactions.api.events.discord import MessageCreate

from command_handler import CommandHandler

TEXT_TRIGGERS = {
    "whencassini": "never",
    "when cassini": "never",
}


def setup(handler: CommandHandler) -> None:
    @slash_command(name="whencassini", description="Get the official Cassini ETA.")
    async def when_cassini_command(ctx: SlashContext):
        await ctx.send("never")

    @slash_command(name="cassinifact", description="Receive an important Cassini fact.")
    async def cassini_fact_command(ctx: SlashContext):
        await ctx.send("Cassini remains in the planning phase, spiritually.")

    @slash_command(name="areweback", description="Check whether things are back.")
    async def are_we_back_command(ctx: SlashContext):
        await ctx.send("no")

    @listen(MessageCreate)
    async def on_funny_trigger(event: MessageCreate):
        message = getattr(event, "message", None)
        if not message or getattr(getattr(message, "author", None), "bot", False):
            return
        content = (getattr(message, "content", None) or "").strip().lower()
        if content not in TEXT_TRIGGERS:
            return
        await message.reply(TEXT_TRIGGERS[content])

    handler.register_slash_command(when_cassini_command)
    handler.register_slash_command(cassini_fact_command)
    handler.register_slash_command(are_we_back_command)
    handler.register_listener(on_funny_trigger)
