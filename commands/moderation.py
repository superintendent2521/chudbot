"""Discord moderation commands."""

from datetime import datetime, timedelta

from interactions import OptionType, slash_option, slash_default_member_permission, Member, SlashContext, slash_command
from interactions.models.discord.enums import Permissions

from command_handler import CommandHandler
from warn import add_warn, get_warns

MAX_TIMEOUT_MINUTES = 28 * 24 * 60  # Discord hard limit for timeouts (28 days)


def _clamp_timeout(minutes: int) -> int:
    """Clamp a timeout length into Discord's allowed range (1 minute–28 days)."""
    return max(1, min(minutes, MAX_TIMEOUT_MINUTES))


def _format_warns(warns: list[str]) -> str:
    if not warns:
        return "No warnings recorded."
    return "\n".join(f"{idx + 1}. {text}" for idx, text in enumerate(warns))


def setup(handler: CommandHandler) -> None:
    @slash_command(name="ban", description="Ban a user from the server.")
    @slash_option(
        name="user",
        description="User to ban.",
        required=True,
        opt_type=OptionType.USER,
    )
    @slash_default_member_permission(Permissions.BAN_MEMBERS)
    async def ban_user(ctx: SlashContext, user: Member):
        if user.id == ctx.guild.me.id:
            await ctx.send(":x: You cannot ban me!")
            return

        try:
            reason = f"Banned by {ctx.author.display_name}"
            await user.ban(reason=reason)
            await ctx.send(f":white_check_mark: Banned {user.mention}.")
        except Exception as exc:
            await ctx.send(f":x: Failed to ban {user.mention}: {exc}")

    handler.register_slash_command(ban_user)

    @slash_command(name="unban", description="Unban a user from the server.")
    @slash_option(
        name="user",
        description="User to unban.",
        required=True,
        opt_type=OptionType.USER,
    )
    @slash_default_member_permission(Permissions.BAN_MEMBERS)
    async def unban_user(ctx: SlashContext, user: Member):
        try:
            reason = f"Unbanned by {ctx.author.display_name}"
            await ctx.guild.unban(user, reason=reason)
            await ctx.send(f":white_check_mark: Unbanned {user.mention}.")
        except Exception as exc:
            await ctx.send(f":x: Failed to unban {user.mention}: {exc}")

    handler.register_slash_command(unban_user)

    @slash_command(name="warn", description="Warn a user.")
    @slash_option(
        name="user",
        description="User to warn.",
        required=True,
        opt_type=OptionType.USER,
    )
    @slash_option(
        name="warning",
        description="Warning reason.",
        required=True,
        opt_type=OptionType.STRING,
    )
    @slash_default_member_permission(Permissions.BAN_MEMBERS)
    async def warn_user(ctx: SlashContext, user: Member, warning: str):
        try:
            add_warn(user.id, warning)
            await ctx.send(f":white_check_mark: Warned {user.mention}.")
        except Exception as exc:
            await ctx.send(f":x: Failed to warn {user.mention}: {exc}")

    handler.register_slash_command(warn_user)

    @slash_command(name="warns", description="Show a user's warnings.")
    @slash_option(
        name="user",
        description="User to inspect.",
        required=True,
        opt_type=OptionType.USER,
    )
    @slash_default_member_permission(Permissions.BAN_MEMBERS)
    async def show_warns(ctx: SlashContext, user: Member):
        warns = get_warns(user.id)
        formatted = _format_warns(warns)
        await ctx.send(f"Warnings for {user.mention}:\n{formatted}")

    handler.register_slash_command(show_warns)

    @slash_command(name="mute", description="Timeout a user for a set number of minutes.")
    @slash_option(
        name="user",
        description="User to mute.",
        required=True,
        opt_type=OptionType.USER,
    )
    @slash_option(
        name="duration",
        description="Duration in minutes (1–40320).",
        required=True,
        opt_type=OptionType.INTEGER,
    )
    @slash_option(
        name="reason",
        description="Reason for the mute (optional).",
        required=False,
        opt_type=OptionType.STRING,
    )
    @slash_default_member_permission(Permissions.MODERATE_MEMBERS)
    async def mute_user(ctx: SlashContext, user: Member, duration: int, reason: str = ""):
        if user.id == ctx.guild.me.id:
            await ctx.send(":x: I cannot mute myself.")
            return

        minutes = _clamp_timeout(duration)
        until = datetime.utcnow() + timedelta(minutes=minutes)
        audit_reason = reason or f"Muted by {ctx.author.display_name}"

        try:
            await user.timeout(until, reason=audit_reason)
            unix_ts = int(until.timestamp())
            await ctx.send(
                f":white_check_mark: Muted {user.mention} for {minutes} minutes (until <t:{unix_ts}:R>)."
            )
        except Exception as exc:
            await ctx.send(f":x: Failed to mute {user.mention}: {exc}")

    handler.register_slash_command(mute_user)

    @slash_command(name="unmute", description="Remove a user's timeout.")
    @slash_option(
        name="user",
        description="User to unmute.",
        required=True,
        opt_type=OptionType.USER,
    )
    @slash_option(
        name="reason",
        description="Reason for the unmute (optional).",
        required=False,
        opt_type=OptionType.STRING,
    )
    @slash_default_member_permission(Permissions.MODERATE_MEMBERS)
    async def unmute_user(ctx: SlashContext, user: Member, reason: str = ""):
        if user.id == ctx.guild.me.id:
            await ctx.send(":x: I cannot unmute myself.")
            return

        audit_reason = reason or f"Unmuted by {ctx.author.display_name}"

        try:
            await user.timeout(None, reason=audit_reason)
            await ctx.send(f":white_check_mark: Unmuted {user.mention}.")
        except Exception as exc:
            await ctx.send(f":x: Failed to unmute {user.mention}: {exc}")

    handler.register_slash_command(unmute_user)
