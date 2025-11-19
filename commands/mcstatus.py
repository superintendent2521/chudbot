"""Minecraft status command."""

import asyncio

import aiohttp
from interactions import SlashContext, slash_command

from command_handler import CommandHandler


def setup(handler: CommandHandler) -> None:
    @slash_command(name="mcstatus", description="Check the status of agartha.mc.gg")
    async def mcstatus_command(ctx: SlashContext):
        await ctx.defer()
        server_address = "agartha.my.pebble.host"
        api_url = f"https://api.mcsrvstat.us/3/{server_address}"
        timeout = aiohttp.ClientTimeout(total=10)

        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                headers = {
                    "User-Agent": "DiscordBot/1.0 (contact:admin@superintendent.me .superintendent discord)"
                }
                async with session.get(api_url, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()

                        if data.get("online", False):
                            motd = "\n".join(data["motd"]["clean"]) if "motd" in data else "No MOTD available"
                            players = (
                                f"{data['players']['online']}/{data['players']['max']}"
                                if "players" in data
                                else "Unknown"
                            )

                            player_list = ""
                            if "players" in data and "list" in data["players"] and data["players"]["list"]:
                                player_list = "\n**Players online:**\n" + "\n".join(
                                    [f"- {player['name']}" for player in data["players"]["list"]]
                                )

                            await ctx.send(
                                f"?o. **{server_address} is ONLINE**\n"
                                f"**MOTD:** {motd}\n"
                                f"**Players:** {players}"
                                f"{player_list}\n"
                            )
                        else:
                            await ctx.send(f"??O **{server_address} is OFFLINE**")
                    else:
                        await ctx.send(f"?s??,? Failed to check server status (HTTP {response.status})")
        except asyncio.TimeoutError:
            await ctx.send("?s??,? Timed out while reaching the status API.")
        except Exception as error:
            await ctx.send(f"?s??,? Error checking server status: {error}")

    handler.register_slash_command(mcstatus_command)

