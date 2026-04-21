"""Coal reaction feature that sends featured messages to a channel."""

from __future__ import annotations

import logging
from typing import Set, Tuple

from interactions import listen
from interactions.api.events.discord import MessageReactionAdd
from interactions.models import Embed

from guild_channel_store import GuildChannelStore
from reaction_roles import member_has_role, snowflake_to_int


COAL_EMOJI = "Coal"
COAL_EMOJI_ID = 1_457_140_176_072_741_040
COAL_THRESHOLD = 2

# Track which messages we've already posted to avoid duplicates
posted_messages: Set[int] = set()


async def _get_sendable_channel(client, channel_id: int, logger: logging.Logger):
    channel = client.cache.get_channel(channel_id)
    if not channel:
        try:
            channel = await client.fetch_channel(channel_id)
        except Exception as error:
            logger.warning("Unable to fetch coal board channel %s: %s", channel_id, error)
            return None
    if not getattr(channel, "send", None):
        logger.warning(
            "Coal board channel %s (%s) does not allow sending messages",
            channel_id,
            channel.__class__.__name__,
        )
        return None
    return channel


def _is_coal_emoji(emoji) -> bool:
    emoji_name = getattr(emoji, "name", str(emoji))
    emoji_id = snowflake_to_int(getattr(emoji, "id", None))
    return (
        emoji_name == COAL_EMOJI
        or str(emoji) == COAL_EMOJI
        or emoji_id == COAL_EMOJI_ID
    )


def create_coal_reaction_listeners(
    store: GuildChannelStore,
    admin_role_id: int,
    logger: logging.Logger,
) -> Tuple:
    """Create listeners for coal reactions using per-guild channel config."""

    @listen(MessageReactionAdd)
    async def on_coal_reaction(event: MessageReactionAdd):
        try:
            message = event.message
            if not message:
                return

            emoji = event.emoji
            if not _is_coal_emoji(emoji):
                return

            if message.id in posted_messages:
                return

            author = getattr(event, "author", None)
            force_post = bool(author and member_has_role(author, admin_role_id))

            coal_count = 0
            if hasattr(message, "reactions") and message.reactions:
                for reaction in message.reactions:
                    try:
                        if _is_coal_emoji(reaction.emoji):
                            coal_count = reaction.count if hasattr(reaction, "count") else 1
                            break
                    except Exception:
                        continue

            if coal_count < COAL_THRESHOLD and not force_post:
                return
            if force_post and coal_count < COAL_THRESHOLD:
                coal_count = max(coal_count, 1)

            guild_id = None
            guild = getattr(message, "guild", None)
            if guild is not None:
                guild_id = snowflake_to_int(getattr(guild, "id", guild))
            if guild_id is None:
                guild_id = snowflake_to_int(getattr(message, "_guild_id", None))
            if guild_id is None:
                guild_id = snowflake_to_int(getattr(event, "guild_id", None))
            if guild_id is None:
                logger.warning("Skipping coal board post for message %s with no guild id", message.id)
                return

            coal_channel_id = store.get_channel_id(guild_id)
            if not coal_channel_id:
                return

            author = getattr(message, "author", None)
            username = getattr(author, "username", "Unknown") if author else "Unknown"
            message_content = getattr(message, "content", "") or ""
            if not message_content and hasattr(message, "embeds") and message.embeds:
                for embed in message.embeds:
                    if hasattr(embed, "description") and embed.description:
                        message_content = embed.description[:1000]
                        break

            channel_id = None
            channel = getattr(message, "channel", None)
            if channel is not None:
                channel_id = snowflake_to_int(getattr(channel, "id", channel))

            base_description = f"Got {coal_count} :rock: reactions"
            if force_post:
                base_description += "\nFeatured early by admin reaction."
            base_description += f"\n\n**Original Message:**\n{message_content[:1000]}"

            embed = Embed(
                title=f"🪨 {username} posted this",
                description=base_description,
                color=0x2F3136,
            )

            if guild_id and channel_id and message.id:
                jump_url = f"https://discord.com/channels/{int(guild_id)}/{int(channel_id)}/{int(message.id)}"
                embed.description = f"{embed.description}\n\n[Jump to message]({jump_url})"

            if hasattr(message, "attachments") and message.attachments:
                for attachment in message.attachments:
                    try:
                        content_type = getattr(attachment, "content_type", "")
                        if content_type.startswith("image/") or content_type.startswith("video/"):
                            embed.set_image(url=getattr(attachment, "url", ""))
                            break
                    except Exception:
                        continue

            if (
                (not hasattr(message, "attachments") or not message.attachments)
                and hasattr(message, "embeds")
                and message.embeds
            ):
                for msg_embed in message.embeds:
                    try:
                        if hasattr(msg_embed, "image") and msg_embed.image:
                            embed.set_image(url=getattr(msg_embed.image, "url", ""))
                            break
                    except Exception:
                        continue

            coal_channel = await _get_sendable_channel(event.client, coal_channel_id, logger)
            if not coal_channel:
                return

            await coal_channel.send(embed=embed)
            posted_messages.add(message.id)
            logger.info(
                "Posted message %s to coal board. Author: %s, Coal count: %d, Forced: %s",
                message.id,
                username,
                coal_count,
                force_post,
            )
        except Exception as error:
            logger.error("Failed to handle coal reaction: %s", error, exc_info=True)

    return (on_coal_reaction,)
