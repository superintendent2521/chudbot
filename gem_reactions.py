"""Gem reaction feature that sends featured messages to a channel when they get enough gem reactions."""

from __future__ import annotations

import logging
from typing import Set, Tuple

from interactions import listen
from interactions.api.events.discord import MessageReactionAdd
from interactions.models import Embed


GEM_EMOJI = "💎"
GEM_THRESHOLD = 4

# Track which messages we've already posted to avoid duplicates
posted_messages: Set[int] = set()


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
            if gem_count < GEM_THRESHOLD:
                logger.debug(f"Gem count {gem_count} is below threshold {GEM_THRESHOLD}")
                return

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
            embed = Embed(
                title=f"💎 {username} posted this",
                description=f"Got {gem_count} :gem: reactions\n\n**Original Message:**\n{message_content[:1000]}",  # Include message content
                color=0xFFD700,  # Gold color for gems
            )

            # FIXED: Create proper Discord jump link with proper IDs
            if guild_id and channel_id and message.id:
                try:
                    jump_url = f"https://discord.com/channels/{int(guild_id)}/{int(channel_id)}/{int(message.id)}"
                    embed.description += f"\n\n[Jump to message]({jump_url})"
                    logger.info(f"Created jump URL: {jump_url}")
                except Exception as e:
                    logger.error(f"Failed to create jump URL: {e}")
                    embed.description += f"\n\n*(Jump link creation failed)*"
            else:
                logger.warning(f"Missing IDs for jump link - Guild: {guild_id}, Channel: {channel_id}, Message: {message.id}")
                embed.description += f"\n\n*(Jump link unavailable - Missing IDs)*"

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
                if gem_channel:
                    await gem_channel.send(embed=embed)
                    posted_messages.add(message.id)
                    logger.info(
                        "Posted message %s to gem channel. Author: %s, Gem count: %d",
                        message.id,
                        username,
                        gem_count,
                    )
                else:
                    logger.error("Could not fetch gem channel %s", gem_channel_id)
            except Exception as e:
                logger.error("Failed to send to gem channel: %s", e, exc_info=True)

        except Exception as error:
            logger.error(
                "Failed to handle gem reaction: %s",
                error,
                exc_info=True,
            )

    return (on_gem_reaction,)
