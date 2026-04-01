"""FAQ commands backed by a JSON store."""

from interactions import OptionType, SlashContext, slash_command, slash_default_member_permission, slash_option
from interactions.models import Embed
from interactions.models.discord.enums import Permissions

from command_handler import CommandHandler

FAQ_EMBED_COLOR = 0xF17828


def setup(handler: CommandHandler) -> None:
    store = handler.resources.faq_store

    @slash_command(name="faq", description="Show a saved FAQ entry or list the available entries.")
    @slash_option(
        name="key",
        description="FAQ key to show, like rules or serverip.",
        required=False,
        opt_type=OptionType.STRING,
    )
    async def faq_command(ctx: SlashContext, key: str = ""):
        if not ctx.guild_id:
            await ctx.send("Use this command inside a server.", ephemeral=True)
            return

        guild_id = int(ctx.guild_id)
        if key and key.strip():
            entry = store.get_entry(guild_id, key)
            if not entry:
                await ctx.send(f"No FAQ entry exists for `{key.strip().lower()}`.", ephemeral=True)
                return

            embed = Embed(
                title=entry["title"],
                description=entry["content"],
                color=FAQ_EMBED_COLOR,
            )
            embed.set_footer(text=f"FAQ key: {key.strip().lower()}")
            await ctx.send(embed=embed)
            return

        keys = store.list_keys(guild_id)
        if not keys:
            await ctx.send("No FAQ entries are configured yet.", ephemeral=True)
            return

        embed = Embed(
            title="FAQ Entries",
            description="\n".join(f"`{faq_key}`" for faq_key in keys),
            color=FAQ_EMBED_COLOR,
        )
        await ctx.send(embed=embed, ephemeral=True)

    @slash_command(name="faqset", description="Create or update a FAQ entry.")
    @slash_option(
        name="key",
        description="Short key used with /faq.",
        required=True,
        opt_type=OptionType.STRING,
    )
    @slash_option(
        name="title",
        description="Embed title to show for this FAQ entry.",
        required=True,
        opt_type=OptionType.STRING,
    )
    @slash_option(
        name="content",
        description="Main FAQ text.",
        required=True,
        opt_type=OptionType.STRING,
    )
    @slash_default_member_permission(Permissions.MANAGE_GUILD)
    async def faq_set_command(ctx: SlashContext, key: str, title: str, content: str):
        if not ctx.guild_id:
            await ctx.send("Use this command inside a server.", ephemeral=True)
            return

        normalized_key = key.strip().lower()
        if not normalized_key:
            await ctx.send("FAQ key cannot be empty.", ephemeral=True)
            return

        store.set_entry(int(ctx.guild_id), normalized_key, title, content)
        await ctx.send(f"Saved FAQ entry `{normalized_key}`.", ephemeral=True)

    @slash_command(name="faqremove", description="Delete a FAQ entry.")
    @slash_option(
        name="key",
        description="Key of the FAQ entry to remove.",
        required=True,
        opt_type=OptionType.STRING,
    )
    @slash_default_member_permission(Permissions.MANAGE_GUILD)
    async def faq_remove_command(ctx: SlashContext, key: str):
        if not ctx.guild_id:
            await ctx.send("Use this command inside a server.", ephemeral=True)
            return

        normalized_key = key.strip().lower()
        if not store.delete_entry(int(ctx.guild_id), normalized_key):
            await ctx.send(f"No FAQ entry exists for `{normalized_key}`.", ephemeral=True)
            return

        await ctx.send(f"Removed FAQ entry `{normalized_key}`.", ephemeral=True)

    handler.register_slash_command(faq_command)
    handler.register_slash_command(faq_set_command)
    handler.register_slash_command(faq_remove_command)
