"""Lavalink statistics command."""

from interactions import SlashContext, slash_command
import lavalink

from command_handler import CommandHandler


def setup(handler: CommandHandler) -> None:
    resources = handler.resources
    require_lavalink = resources.require_lavalink
    get_lavalink_client = resources.get_lavalink_client
    format_bytes = resources.format_bytes
    format_uptime = resources.format_uptime
    logger = resources.logger

    @slash_command(name="lavalinkstats", description="Show Lavalink node statistics")
    async def lavalink_stats_command(ctx: SlashContext):
        if not await require_lavalink(ctx):
            return
        await ctx.defer(ephemeral=True)
        lavalink_client = get_lavalink_client()
        if not lavalink_client:
            await ctx.send("Music playback isn't configured.", ephemeral=True)
            return

        nodes = list(getattr(getattr(lavalink_client, "node_manager", None), "nodes", []))
        if not nodes:
            await ctx.send("No Lavalink nodes are registered with this bot.", ephemeral=True)
            return

        sections = []
        for node in nodes:
            node_name = getattr(node, "name", "Lavalink Node")
            stats = node.stats
            if getattr(stats, "is_fake", True):
                try:
                    raw_stats = await node.get_stats()
                except Exception as error:
                    logger.warning("Unable to refresh Lavalink stats for %s: %s", node_name, error)
                else:
                    stats = lavalink.Stats(node, raw_stats)
                    node.stats = stats
            status_label = "Online" if getattr(node, "available", False) else "Offline"
            if getattr(stats, "is_fake", True):
                sections.append(
                    f"**{node_name}** ({status_label})\nStatistics are not available yet. Try again shortly."
                )
                continue

            lines = [
                f"**{node_name}** ({status_label})",
                f"Players: {stats.playing_players}/{stats.players} playing",
                f"Uptime: {format_uptime(stats.uptime)}",
                f"CPU: {stats.cpu_cores} cores | system {stats.system_load * 100:.1f}% | "
                f"lavalink {stats.lavalink_load * 100:.1f}%",
                (
                    "Memory: "
                    f"{format_bytes(stats.memory_used)} used / {format_bytes(stats.memory_allocated)} allocated "
                    f"(free {format_bytes(stats.memory_free)})"
                ),
                (
                    "Frames: "
                    f"sent {stats.frames_sent:,} | nulled {stats.frames_nulled:,} | deficit {stats.frames_deficit:,}"
                ),
                f"Penalty: {stats.penalty.total:.2f}",
            ]
            sections.append("\n".join(lines))

        await ctx.send("\n\n".join(sections), ephemeral=True)

    handler.register_slash_command(lavalink_stats_command)

