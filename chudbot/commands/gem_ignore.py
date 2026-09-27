"""Gem reaction ignore-channel commands."""

from interactions import OptionType, SlashContext, slash_command, slash_default_member_permission, slash_option
from interactions.models.discord.enums import Permissions

from chudbot.command_handler import CommandHandler


def _channel_id(channel) -> int | None:
    try:
        return int(getattr(channel, "id", channel))
    except (TypeError, ValueError):
        return None


def setup(handler: CommandHandler) -> None:
    store = handler.resources.gem_ignored_channel_store

    def valid_channel(ctx: SlashContext, channel) -> int | None:
        channel_guild_id = getattr(channel, "guild_id", None)
        if channel_guild_id is not None and int(channel_guild_id) != int(ctx.guild_id):
            return None
        return _channel_id(channel)

    @slash_command(name="gemignore", description="Ignore gem reactions in a channel and its threads.")
    @slash_option(name="channel", description="Channel to ignore.", required=True, opt_type=OptionType.CHANNEL)
    @slash_default_member_permission(Permissions.MANAGE_GUILD)
    async def gem_ignore(ctx: SlashContext, channel):
        if not ctx.guild_id:
            await ctx.send("Use this command inside a server.", ephemeral=True)
            return
        channel_id = valid_channel(ctx, channel)
        if channel_id is None:
            await ctx.send("That channel is not in this server.", ephemeral=True)
            return
        store.add(int(ctx.guild_id), channel_id)
        await ctx.send(f"Gem reactions will be ignored in {getattr(channel, 'mention', f'<#{channel_id}>')} and its threads.", ephemeral=True)

    @slash_command(name="gemunignore", description="Stop ignoring gem reactions in a channel.")
    @slash_option(name="channel", description="Channel to stop ignoring.", required=True, opt_type=OptionType.CHANNEL)
    @slash_default_member_permission(Permissions.MANAGE_GUILD)
    async def gem_unignore(ctx: SlashContext, channel):
        if not ctx.guild_id:
            await ctx.send("Use this command inside a server.", ephemeral=True)
            return
        channel_id = valid_channel(ctx, channel)
        if channel_id is None:
            await ctx.send("That channel is not in this server.", ephemeral=True)
            return
        store.remove(int(ctx.guild_id), channel_id)
        await ctx.send(f"Gem reactions are no longer ignored in {getattr(channel, 'mention', f'<#{channel_id}>')}.", ephemeral=True)

    handler.register_slash_command(gem_ignore)
    handler.register_slash_command(gem_unignore)
