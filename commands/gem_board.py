"""Gem board configuration command."""

from interactions import OptionType, SlashContext, slash_command, slash_default_member_permission, slash_option
from interactions.models.discord.enums import Permissions

from command_handler import CommandHandler


def _snowflake_to_int(value):
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


def setup(handler: CommandHandler) -> None:
    resources = handler.resources
    store = resources.gem_board_store
    logger = resources.logger

    @slash_command(name="gemboard", description="Set the channel for gem board posts.")
    @slash_option(
        name="channel",
        description="Channel to post gem board messages in (defaults to the current channel).",
        required=False,
        opt_type=OptionType.CHANNEL,
    )
    @slash_default_member_permission(Permissions.MANAGE_GUILD)
    async def set_gem_board_channel(ctx: SlashContext, channel=None):
        if not ctx.guild_id:
            await ctx.send("Use this command inside a server.", ephemeral=True)
            return

        target_channel = channel or ctx.channel
        if not target_channel or not getattr(target_channel, "send", None):
            channel_id = getattr(channel, "id", None) or getattr(ctx, "channel_id", None)
            if channel_id:
                try:
                    target_channel = await ctx.client.fetch_channel(int(channel_id))
                except Exception as error:
                    logger.error("Unable to fetch channel %s for gem board: %s", channel_id, error)
                    target_channel = None

        if not target_channel or not getattr(target_channel, "send", None):
            await ctx.send("I can't post messages in that channel. Pick another channel.", ephemeral=True)
            return

        target_channel_id = int(getattr(target_channel, "id", target_channel))
        channel_guild_id = _snowflake_to_int(getattr(target_channel, "guild_id", None))
        if channel_guild_id is None:
            channel_guild = getattr(target_channel, "guild", None)
            channel_guild_id = _snowflake_to_int(getattr(channel_guild, "id", None))
        if channel_guild_id is not None and int(channel_guild_id) != int(ctx.guild_id):
            await ctx.send("That channel is not in this server.", ephemeral=True)
            return

        store.set_channel(int(ctx.guild_id), target_channel_id)
        channel_mention = getattr(target_channel, "mention", f"<#{target_channel_id}>")
        await ctx.send(f"Gem board posts will be sent to {channel_mention}.", ephemeral=True)

    handler.register_slash_command(set_gem_board_channel)
