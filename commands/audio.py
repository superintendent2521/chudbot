"""Music-related slash commands."""

import asyncio
from typing import List

from interactions import OptionType, SlashContext, slash_command, slash_option
import lavalink

from command_handler import CommandHandler


def setup(handler: CommandHandler) -> None:
    resources = handler.resources
    require_music_permission = resources.require_music_permission
    require_lavalink = resources.require_lavalink
    music_manager = resources.music_manager
    default_volume = resources.default_player_volume
    get_voice_channel = resources.get_voice_channel
    format_duration = resources.format_duration
    get_lavalink_client = resources.get_lavalink_client
    logger = resources.logger
    MusicError = resources.music_error_cls

    @slash_command(name="play", description="Queue music from YouTube or YouTube Music")
    @slash_option(
        name="query",
        description="YouTube or YouTube Music link, or search terms",
        opt_type=OptionType.STRING,
        required=True,
    )
    async def play_command(ctx: SlashContext, query: str):
        if not await require_music_permission(ctx):
            return
        if not await require_lavalink(ctx):
            return
        if not ctx.guild_id:
            await ctx.send("This command can only be used inside a server.", ephemeral=True)
            return

        voice_channel = get_voice_channel(ctx.author)
        if not voice_channel:
            await ctx.send("Join a voice channel first, then ask me to play music.", ephemeral=True)
            return

        await ctx.defer()
        guild_id = int(ctx.guild_id)
        session = music_manager.get_session(guild_id)
        session.cancel_idle_timer()
        player = music_manager.get_player(guild_id)
        if default_volume != player.volume:
            try:
                await player.set_volume(default_volume)
            except Exception as error:
                logger.warning("Unable to set player volume to %s: %s", default_volume, error)
        try:
            await session.ensure_connected(voice_channel)
        except asyncio.TimeoutError:
            lavalink_client = get_lavalink_client()
            if lavalink_client:
                lavalink_client.player_manager.remove(guild_id)
            await ctx.send("I couldn't join that voice chat in time. Please try again.", ephemeral=True)
            return
        except MusicError as error:
            lavalink_client = get_lavalink_client()
            logger.error("Unable to process voice connection for guild %s: %s", guild_id, error)
            if lavalink_client:
                lavalink_client.player_manager.remove(guild_id)
            await ctx.send(str(error), ephemeral=True)
            return
        except Exception as error:
            lavalink_client = get_lavalink_client()
            logger.error("Failed to connect to voice channel %s: %s", voice_channel.id, error)
            if lavalink_client:
                lavalink_client.player_manager.remove(guild_id)
            await ctx.send("I couldn't join that voice chat. Check my permissions and try again.", ephemeral=True)
            return

        try:
            result = await music_manager.load_tracks(query)
        except MusicError as error:
            await ctx.send(f"I couldn't load that track: {error}", ephemeral=True)
            return

        player.channel_id = voice_channel.id

        if result.load_type == lavalink.LoadType.PLAYLIST:
            for track in result.tracks:
                track.extra["requester"] = ctx.author.id
                player.add(track)
            playlist_name = result.playlist_info.name if result.playlist_info else "Playlist"
            await ctx.send(
                f"Queued playlist **{playlist_name}** with {len(result.tracks)} tracks for {ctx.author.mention}"
            )
        else:
            track = result.tracks[0]
            track.requester = ctx.author.id  # type: ignore[attr-defined]
            player.add(track)
            await ctx.send(
                f"Queued **{track.title}** (`{format_duration(track.duration)}`) for {ctx.author.mention}\n"
                f"<{track.uri}>"
            )

        session.cancel_idle_timer()
        if not player.is_playing:
            await player.play()

    @slash_command(name="skip", description="Skip the currently playing track")
    async def skip_command(ctx: SlashContext):
        if not await require_music_permission(ctx):
            return
        if not await require_lavalink(ctx):
            return
        if not ctx.guild_id:
            await ctx.send("This only works inside a server.", ephemeral=True)
            return
        lavalink_client = get_lavalink_client()
        player = lavalink_client.player_manager.get(int(ctx.guild_id)) if lavalink_client else None
        if not player or not player.current:
            await ctx.send("Nothing is playing to skip.", ephemeral=True)
            return
        await player.skip()
        await ctx.send("Skipped the current track.")

    @slash_command(name="pause", description="Pause the current track")
    async def pause_command(ctx: SlashContext):
        if not await require_music_permission(ctx):
            return
        if not await require_lavalink(ctx):
            return
        if not ctx.guild_id:
            await ctx.send("This only works inside a server.", ephemeral=True)
            return
        lavalink_client = get_lavalink_client()
        player = lavalink_client.player_manager.get(int(ctx.guild_id)) if lavalink_client else None
        if not player or not player.is_playing or player.paused:
            await ctx.send("There's nothing playing to pause.", ephemeral=True)
            return
        await player.set_pause(True)
        await ctx.send("Paused the music.")

    @slash_command(name="resume", description="Resume playback if paused")
    async def resume_command(ctx: SlashContext):
        if not await require_music_permission(ctx):
            return
        if not await require_lavalink(ctx):
            return
        if not ctx.guild_id:
            await ctx.send("This only works inside a server.", ephemeral=True)
            return
        lavalink_client = get_lavalink_client()
        player = lavalink_client.player_manager.get(int(ctx.guild_id)) if lavalink_client else None
        if not player or not player.paused:
            await ctx.send("I'm not paused right now.", ephemeral=True)
            return
        await player.set_pause(False)
        await ctx.send("Resumed playback.")

    @slash_command(name="queue", description="Show the current music queue")
    async def queue_command(ctx: SlashContext):
        if not await require_lavalink(ctx):
            return
        if not ctx.guild_id:
            await ctx.send("This command must be used inside a server.", ephemeral=True)
            return
        lavalink_client = get_lavalink_client()
        player = lavalink_client.player_manager.get(int(ctx.guild_id)) if lavalink_client else None
        if not player or (not player.current and not player.queue):
            await ctx.send("Nothing is queued up right now.")
            return
        lines: List[str] = []
        if player.current:
            lines.append(
                f"**Now playing:** {player.current.title} (`{format_duration(player.current.duration)}`) "
                f"? requested by <@{player.current.requester}>"
            )
        if player.queue:
            lines.append("")
            lines.append("**Up next:**")
            for index, track in enumerate(player.queue[:10], start=1):
                lines.append(
                    f"{index}. {track.title} (`{format_duration(track.duration)}`) "
                    f"? requested by <@{track.requester}>"
                )
            remaining = len(player.queue) - 10
            if remaining > 0:
                lines.append(f"...and {remaining} more.")
        await ctx.send("\n".join(lines))

    @slash_command(name="stop", description="Stop playback and clear the queue")
    async def stop_command(ctx: SlashContext):
        if not await require_music_permission(ctx):
            return
        if not await require_lavalink(ctx):
            return
        if not ctx.guild_id:
            await ctx.send("Use this inside a server.", ephemeral=True)
            return
        guild_id = int(ctx.guild_id)
        lavalink_client = get_lavalink_client()
        player = lavalink_client.player_manager.get(guild_id) if lavalink_client else None
        session = music_manager.active_session(guild_id)
        if not player and not session:
            await ctx.send("There's no active music session to stop.", ephemeral=True)
            return
        if player:
            player.queue.clear()
            try:
                await player.stop()
            except Exception:
                pass
            if lavalink_client:
                lavalink_client.player_manager.remove(guild_id)
        if session:
            await session.disconnect()
        await ctx.send("Music stopped and the bot left the voice channel.")

    handler.register_slash_command(play_command)
    handler.register_slash_command(skip_command)
    handler.register_slash_command(pause_command)
    handler.register_slash_command(resume_command)
    handler.register_slash_command(queue_command)
    handler.register_slash_command(stop_command)

