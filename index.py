import interactions
import os
import aiohttp
from dotenv import load_dotenv
from interactions import Client, Intents, listen, slash_command, SlashContext
from interactions.api.events.discord import VoiceUserJoin, VoiceUserLeave

# Load environment variables
load_dotenv()

# Environment switch (change to 'dev' for development)
ENVIRONMENT = 'main'  # or 'dev'

# Get appropriate token based on environment
BOT_TOKEN = os.getenv(f'BOT_TOKEN_{ENVIRONMENT.upper()}')
LOG_CHANNEL_ID = int(os.getenv('LOG_CHANNEL_ID'))

bot = Client(
    token=BOT_TOKEN,
    intents=Intents.DEFAULT | Intents.GUILD_VOICE_STATES,
)
print(ENVIRONMENT)
# Version
@slash_command(name="version", description="My first command :)")
async def my_command_function(ctx: SlashContext):
    await ctx.send(f"version: {ENVIRONMENT}")

# Logs voice  join
@listen(VoiceUserJoin)
async def on_voice_join(event: VoiceUserJoin):
    channel = event.client.cache.get_channel(LOG_CHANNEL_ID) \
              or await event.client.fetch_channel(LOG_CHANNEL_ID)
    if not channel:
        print(f"⚠️ Cannot log join: channel {LOG_CHANNEL_ID} not found.")
        return
    await channel.send(
        f"🎙️ **{event.author.username}** joined **{event.channel.name}**"
    )
# Logs voice leave
@listen(VoiceUserLeave)
async def on_voice_leave(event: VoiceUserLeave):
    channel = event.client.cache.get_channel(LOG_CHANNEL_ID) \
              or await event.client.fetch_channel(LOG_CHANNEL_ID)
    if not channel:
        print(f"⚠️ Cannot log leave: channel {LOG_CHANNEL_ID} not found.")
        return
    await channel.send(
        f"❌ **{event.author.username}** left **{event.channel.name}**"
    )

@slash_command(name="mcstatus", description="Check the status of hyperborea.mcserver.us")
async def mcstatus_command(ctx: SlashContext):
    await ctx.defer()
    server_address = "hyperborea.mcserver.us"
    api_url = f"https://api.mcsrvstat.us/3/{server_address}"
    
    try:
        async with aiohttp.ClientSession() as session:
            headers = {"User-Agent": "DiscordBot/1.0 (contact:admin@superintendent.me .superintendent discord)"}
            async with session.get(api_url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    
                    if data.get('online', False):
                        motd = "\n".join(data['motd']['clean']) if 'motd' in data else "No MOTD available"
                        players = f"{data['players']['online']}/{data['players']['max']}" if 'players' in data else "Unknown"
                        version = data.get('version', 'Unknown')
                        
                        # Build player list
                        player_list = ""
                        if 'players' in data and 'list' in data['players'] and data['players']['list']:
                            player_list = "\n**Players online:**\n" + "\n".join(
                                [f"- {player['name']}" for player in data['players']['list']]
                            )
                        
                        await ctx.send(
                            f"✅ **{server_address} is ONLINE**\n"
                            f"**Players:** {players}"
                            f"{player_list}\n"
                        )
                    else:
                        await ctx.send(f"❌ **{server_address} is OFFLINE**")
                else:
                    await ctx.send(f"⚠️ Failed to check server status (HTTP {response.status})")
    except Exception as e:
        await ctx.send(f"⚠️ Error checking server status: {str(e)}")





bot.start()