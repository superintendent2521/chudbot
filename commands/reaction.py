"""Reaction role command."""

from interactions import OptionType, Role, SlashContext, slash_command, slash_option

from command_handler import CommandHandler


def setup(handler: CommandHandler) -> None:
    resources = handler.resources
    store = resources.reaction_role_store
    admin_role_id = resources.reaction_role_admin_role_id
    default_emoji = resources.default_reaction_role_emoji
    has_required_role = resources.member_has_role
    snowflake_to_int = resources.snowflake_to_int
    logger = resources.logger

    @slash_command(name="reaction", description="Create a reaction role message in this channel.")
    @slash_option(
        name="role",
        description="Role to grant when members react.",
        opt_type=OptionType.ROLE,
        required=True,
    )
    async def create_reaction_role(ctx: SlashContext, role: Role):
        if not ctx.guild_id:
            await ctx.send("Use this command inside a server.", ephemeral=True)
            return
        if not has_required_role(ctx.author, admin_role_id):
            await ctx.send(
                f"You need the <@&{admin_role_id}> role to create reaction role messages.",
                ephemeral=True,
            )
            return

        channel = ctx.channel
        if not channel or not getattr(channel, "send", None):
            channel_id = getattr(ctx, "channel_id", None)
            if channel_id:
                try:
                    channel = await ctx.client.fetch_channel(int(channel_id))
                except Exception as error:
                    logger.error("Unable to fetch channel %s for reaction role: %s", channel_id, error)
                    channel = None
        if not channel or not getattr(channel, "send", None):
            await ctx.send("I can't post messages in this location. Please try again elsewhere.", ephemeral=True)
            return

        message_content = (
            f"React with {default_emoji} to receive {role.mention}.\n"
            "Remove your reaction to have the role removed."
        )
        try:
            reaction_message = await channel.send(message_content)
            await reaction_message.add_reaction(default_emoji)
        except Exception as error:
            logger.error(
                "Failed to send reaction role message in channel %s: %s",
                getattr(channel, "id", "unknown"),
                error,
            )
            await ctx.send(
                "I couldn't post the reaction role message. Double-check my permissions and try again.",
                ephemeral=True,
            )
            return

        channel_obj = getattr(reaction_message, "channel", None)
        stored_channel_id = snowflake_to_int(getattr(channel_obj, "id", None)) or snowflake_to_int(
            getattr(ctx, "channel_id", None)
        )
        if stored_channel_id is None:
            stored_channel_id = 0

        store.set_entry(
            int(getattr(reaction_message, "id", reaction_message)),
            guild_id=int(ctx.guild_id),
            channel_id=stored_channel_id,
            role_id=int(role.id),
            emoji=default_emoji,
        )
        message_url = f"https://discord.com/channels/{ctx.guild_id}/{stored_channel_id}/{reaction_message.id}"
        channel_mention = getattr(channel, "mention", f"<#{stored_channel_id}>")
        await ctx.send(
            f"Reaction role message created in {channel_mention} for {role.mention}.\n<{message_url}>",
            ephemeral=True,
        )

    handler.register_slash_command(create_reaction_role)

