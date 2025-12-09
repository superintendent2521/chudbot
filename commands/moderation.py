"""Discord moderation commands."""

from interactions import OptionType, slash_option, slash_default_member_permission, Member, SlashContext, slash_command
from command_handler import CommandHandler
from interactions.models.discord.enums import Permissions

from ..warn import *
# wildcard but fuck it we ball

def setup(handler: CommandHandler) -> None:
    @slash_command(name="ban", description="Ban a user from the server.")
    @slash_option(
        name="user",
        description="User to ban.", 
        required=True,
        opt_type=OptionType.USER
    )
    @slash_default_member_permission(Permissions.BAN_MEMBERS)
    async def ban_user(ctx: SlashContext, user: Member):
        # Prevent banning the bot
        if user.id == ctx.guild.me.id:
            await ctx.send("❌ You cannot ban me!")
            return

        # Try to ban the user
        try:
            # You can add a reason for the ban
            reason = f"Banned by {ctx.author.display_name}"
            await user.ban(reason=reason)
            await ctx.send(f"✅ Successfully banned {user.mention} from the server!")
        except Exception as e:
            await ctx.send(f"❌ Failed to ban {user.mention}: {str(e)}")

    handler.register_slash_command(ban_user)

    @slash_command(name="warn", description="Warn a user.")
    @slash_option(
        name="user",
        description="User to warn.",
        required=True,
        opt_type=OptionType.USER
    )
    @slash_option(
        name="warning",
        description="Warning reason.",
        required=True,
        opt_type=OptionType.STRING
    )
    @slash_default_member_permission(Permissions.BAN_MEMBERS)
    async def warn_user(ctx: SlashContext, user: Member, warning: str):
        # member permission decorator is what does our checks for if you can do it or not.
        try:
            # Fixed: pass user.id instead of Member class
            add_warn(user.id, warning)
            await ctx.send(f"✅ Successfully warned {user.mention}")
        except Exception as e:
            await ctx.send(f"❌ Failed to warn {user.mention}: {str(e)}")
    handler.register_slash_command(warn_user)