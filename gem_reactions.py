"""Gem reaction feature that sends featured messages to a channel when they get enough gem reactions."""

from __future__ import annotations

import logging
from typing import Optional, Set, Tuple

from interactions import Member, listen
from interactions.api.events.discord import MessageReactionAdd
from interactions.models import Embed
from reaction_roles import member_has_role, snowflake_to_int


GEM_EMOJI = "💎"
GEM_THRESHOLD = 4
GEM_CURATOR_ROLE_ID = 1_434_633_532_436_648_126

# Track which messages we've already posted to avoid duplicates
posted_messages: Set[int] = set()


async def _get_member_from_reaction_event(
    event: MessageReactionAdd, logger: logging.Logger
) -> Optional[Member]:
    author = getattr(event, "author", None)
    if isinstance(author, Member):
        return author

    message = getattr(event, "message", None)
    guild_id = None
    if message is not None:
        guild = getattr(message, "guild", None)
        if guild is not None:
            guild_id = snowflake_to_int(getattr(guild, "id", guild))
        if guild_id is None:
            guild_id = snowflake_to_int(getattr(message, "_guild_id", None))
    if guild_id is None:
        guild_id = snowflake_to_int(getattr(event, "guild_id", None))

    user_id = snowflake_to_int(getattr(author, "id", None))

    if guild_id is None or user_id is None:
        return None

    client = getattr(event, "client", None)
    if not client:
        return None
    try:
        return await client.fetch_member(guild_id, user_id)
    except Exception as error:
        logger.warning(
            "Unable to fetch member %s in guild %s for gem reaction: %s",
            user_id,
            guild_id,
            error,
        )
        return None


def create_gem_reaction_listeners(
    gem_channel_id: int, logger: logging.Logger
) -> Tuple:
    """Create listeners for gem reactions.
    
    Args:
        gem_channel_id: ID of the channel to send gem messages to
        logger: Logger instance for logging events
        
    Returns:
        Tuple of listener functions
    """

    @listen(MessageReactionAdd)
    async def on_gem_reaction(event: MessageReactionAdd):
        """Handle gem reactions and send to gem channel when threshold is reached."""
        try:
            message = event.message
            if not message:
                logger.debug("No message in event")
                return

            logger.debug(f"Message object: {message}, type: {type(message)}")
            logger.debug(f"Message attributes: {dir(message)}")

            # Check if the emoji is the gem emoji
            emoji = event.emoji
            emoji_name = getattr(emoji, "name", str(emoji))
            logger.info(f"Reaction added: emoji_name={emoji_name}, emoji={emoji}, emoji type={type(emoji)}")
            
            if emoji_name != GEM_EMOJI and str(emoji) != GEM_EMOJI:
                logger.debug(f"Emoji {emoji_name} does not match gem emoji {GEM_EMOJI}")
                return

            logger.info(f"Gem emoji detected on message {message.id}")

            member = await _get_member_from_reaction_event(event, logger)
            force_post = bool(member and member_has_role(member, GEM_CURATOR_ROLE_ID))
            if force_post:
                logger.info(
                    "Bypassing gem threshold: member %s (%s) has curator role %s",
                    getattr(member, "display_name", None),
                    getattr(member, "id", None),
                    GEM_CURATOR_ROLE_ID,
                )

            # Skip if we already posted this message
            if message.id in posted_messages:
                logger.debug(f"Message {message.id} already posted")
                return

            # Count gem reactions on the message
            gem_count = 0
            logger.debug(f"Message reactions: {message.reactions}")
            if hasattr(message, "reactions") and message.reactions:
                logger.info(f"Checking {len(message.reactions)} reactions on message")
                for reaction in message.reactions:
                    try:
                        emoji_name_check = getattr(reaction.emoji, "name", str(reaction.emoji))
                        count = reaction.count if hasattr(reaction, "count") else 1
                        logger.debug(f"  Reaction: {emoji_name_check} (count: {count})")
                        if emoji_name_check == GEM_EMOJI or str(reaction.emoji) == GEM_EMOJI:
                            gem_count = count
                            logger.info(f"Found gem reaction with count {gem_count}")
                            break
                    except Exception as e:
                        logger.error(f"Error checking reaction: {e}")
                        continue

            logger.info(f"Total gem count: {gem_count}, threshold: {GEM_THRESHOLD}")
            # Check if threshold is reached
            if gem_count < GEM_THRESHOLD and not force_post:
                logger.debug(f"Gem count {gem_count} is below threshold {GEM_THRESHOLD}")
                return
            if force_post and gem_count < GEM_THRESHOLD:
                gem_count = max(gem_count, 1)

            # Get author info
            author = getattr(message, "author", None)
            username = getattr(author, "username", "Unknown") if author else "Unknown"
            
            # FIXED: Get message content - Extract and include original message content
            message_content = getattr(message, "content", "") or ""
            if not message_content and hasattr(message, "embeds") and message.embeds:
                # Try to get content from embeds if no regular content
                for embed in message.embeds:
                    if hasattr(embed, "description") and embed.description:
                        message_content = embed.description[:1000]  # Limit length
                        break
            
            # FIXED: Get guild ID and channel ID using the same pattern as reaction_roles.py
            guild_id = None
            if message is not None:
                # Try to get guild from message.guild first
                guild = getattr(message, "guild", None)
                if guild is not None:
                    guild_id = int(getattr(guild, "id", guild))
                # Fallback: try message._guild_id
                if guild_id is None:
                    guild_id = getattr(message, "_guild_id", None)
            # Final fallback: try event.guild_id
            if guild_id is None:
                guild_id = getattr(event, "guild_id", None)
            
            # Get channel ID - similar approach
            channel_id = None
            if message is not None:
                channel = getattr(message, "channel", None)
                if channel is not None:
                    channel_id = int(getattr(channel, "id", channel))
            
            # Log the results for debugging
            logger.info(f"Extracted IDs - Guild: {guild_id}, Channel: {channel_id}, Message: {message.id}")

            # Create embed with message content - FIXED: Include original message content
            base_description = f"Got {gem_count} :gem: reactions"
            if force_post:
                base_description += "\nFeatured early by curator reaction."
            base_description += f"\n\n**Original Message:**\n{message_content[:1000]}"
            embed = Embed(
                title=f"💎 {username} posted this",
                description=base_description,
                color=0xFFD700,  # Gold color for gems
            )

            description_text = embed.description or ""

            # FIXED: Create proper Discord jump link with proper IDs
            if guild_id and channel_id and message.id:
                try:
                    jump_url = f"https://discord.com/channels/{int(guild_id)}/{int(channel_id)}/{int(message.id)}"
                    description_text += f"\n\n[Jump to message]({jump_url})"
                    logger.info(f"Created jump URL: {jump_url}")
                except Exception as e:
                    logger.error(f"Failed to create jump URL: {e}")
                    description_text += "\n\n*(Jump link creation failed)*"
            else:
                logger.warning(f"Missing IDs for jump link - Guild: {guild_id}, Channel: {channel_id}, Message: {message.id}")
                description_text += "\n\n*(Jump link unavailable - Missing IDs)*"

            embed.description = description_text

            # Add image/gif if present
            if hasattr(message, "attachments") and message.attachments:
                for attachment in message.attachments:
                    try:
                        content_type = getattr(attachment, "content_type", "")
                        if content_type.startswith("image/") or content_type.startswith("video/"):
                            embed.set_image(url=getattr(attachment, "url", ""))
                            break
                    except Exception:
                        continue

            # Add embed image from message embeds if no attachments
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

            # Send to gem channel
            try:
                gem_channel = await event.client.fetch_channel(gem_channel_id)
                send = getattr(gem_channel, "send", None) if gem_channel else None
                if send:
                    await send(embed=embed)
                    posted_messages.add(message.id)
                    logger.info(
                        "Posted message %s to gem channel. Author: %s, Gem count: %d",
                        message.id,
                        username,
                        gem_count,
                    )
                else:
                    logger.error("Could not fetch gem channel %s or channel is not sendable", gem_channel_id)
            except Exception as e:
                logger.error("Failed to send to gem channel: %s", e, exc_info=True)

        except Exception as error:
            logger.error(
                "Failed to handle gem reaction: %s",
                error,
                exc_info=True,
            )

    return (on_gem_reaction,)
