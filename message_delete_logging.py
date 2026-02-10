"""Log deleted messages to a dedicated audit channel."""

from __future__ import annotations

import logging
from typing import Tuple

from interactions import listen
from interactions.api.events.discord import MessageDelete


async def _get_sendable_channel(client, channel_id: int, logger: logging.Logger):
    """Fetch a channel and ensure it supports send()."""
    channel = client.cache.get_channel(channel_id)
    if not channel:
        try:
            channel = await client.fetch_channel(channel_id)
        except Exception as error:  # pragma: no cover - network/discord failures
            logger.warning("Unable to fetch channel %s: %s", channel_id, error)
            return None
    if not getattr(channel, "send", None):
        logger.warning(
            "Channel %s (%s) does not allow sending messages",
            channel_id,
            channel.__class__.__name__,
        )
        return None
    return channel


def _truncate(text: str, limit: int = 1500) -> str:
    """Keep Discord message under the limit with a short ellipsis."""
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def create_message_delete_logging_listeners(
    log_channel_id: int, logger: logging.Logger
) -> Tuple:
    """Create listeners that log deleted messages to the audit channel."""

    @listen(MessageDelete)
    async def on_message_delete(event: MessageDelete):
        channel = await _get_sendable_channel(event.client, log_channel_id, logger)
        if not channel:
            logger.warning(
                "Cannot log message delete: channel %s not found or sendable",
                log_channel_id,
            )
            return

        deleted_message = getattr(event, "message", None)

        # Avoid logging deletions from the audit channel itself to prevent loops.
        event_channel_id = getattr(event, "channel_id", None) or getattr(
            deleted_message, "channel_id", None
        )
        if event_channel_id == log_channel_id:
            return

        author = None
        author_id = None
        if deleted_message and getattr(deleted_message, "author", None):
            author = (
                getattr(deleted_message.author, "mention", None)
                or getattr(deleted_message.author, "username", None)
                or None
            )
            author_id = getattr(deleted_message.author, "id", None)

        channel_mention = (
            f"<#{event_channel_id}>"
            if event_channel_id is not None
            else "unknown channel"
        )

        message_id = getattr(deleted_message, "id", None) or getattr(
            event, "message_id", None
        )
        content = None
        if deleted_message:
            content = deleted_message.content or None
            if content:
                content = _truncate(content.strip())

        attachments = getattr(deleted_message, "attachments", None) or []
        attachment_lines = []
        for attachment in attachments:
            url = getattr(attachment, "url", None)
            filename = getattr(attachment, "filename", None)
            attachment_lines.append(url or filename)

        parts = [f"Message deleted in {channel_mention}"]
        if author or author_id:
            author_bits = []
            if author:
                author_bits.append(author)
            if author_id:
                author_bits.append(f"ID: {author_id}")
            parts.append("Author: " + " ".join(author_bits))
        if message_id:
            parts.append(f"Message ID: {message_id}")
        if content:
            parts.append("Content:")
            parts.append(content)
        else:
            parts.append("Content: <not available>")
        if attachment_lines:
            parts.append("Attachments:")
            parts.extend(f"- {item}" for item in attachment_lines if item)

        try:
            await channel.send("\n".join(parts))
        except Exception as error:  # pragma: no cover - discord send failures
            logger.error("Failed to log deleted message: %s", error, exc_info=True)

    return (on_message_delete,)
