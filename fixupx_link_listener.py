"""Listener that rewrites x.com links to fixupx.com alternatives."""

from __future__ import annotations

import logging
import re
from typing import List
from urllib.parse import urlsplit, urlunsplit

from interactions import listen
from interactions.api.events.discord import MessageCreate

# Matches standard X (formerly Twitter) URLs. Allows optional scheme and stops at whitespace/angle brackets.
X_LINK_PATTERN = re.compile(
    r"(?:https?://)?(?:www\.)?x\.com/[\w\-./?%&#=:+,;~]+",
    re.IGNORECASE,
)
TRAILING_PUNCTUATION = ".,;:!?)]}\"'"


def _to_fixupx(url: str) -> str:
    """Replace the domain with fixupx.com while preserving path/query/fragment."""
    trimmed = url.rstrip(TRAILING_PUNCTUATION)
    normalized = trimmed if trimmed.startswith(("http://", "https://")) else f"https://{trimmed}"
    parsed = urlsplit(normalized)
    new_url = urlunsplit(
        (
            parsed.scheme or "https",
            "fixupx.com",
            parsed.path,
            parsed.query,
            parsed.fragment,
        )
    )
    return new_url


def create_fixupx_listener(logger: logging.Logger):
    """Create a listener that replies with fixupx.com versions of x.com links."""

    @listen(MessageCreate)
    async def on_x_link(event: MessageCreate):
        try:
            message = event.message
            if not message:
                return
            if getattr(message.author, "bot", False):
                return

            content = message.content or ""
            if not content:
                return

            lower_content = content.lower()
            if "fixupx.com" in lower_content:
                return

            matches = list(X_LINK_PATTERN.finditer(content))
            if not matches:
                return

            replacements: List[str] = []
            for match in matches:
                original_url = match.group(0)
                try:
                    replacements.append(_to_fixupx(original_url))
                except Exception:
                    logger.debug("Skipping malformed X URL: %s", original_url, exc_info=True)
                    continue

            if not replacements:
                return

            reply_text = "\n".join(dict.fromkeys(replacements))  # remove duplicates, keep order
            await message.reply(reply_text)
            logger.info("Replied with fixupx links for message %s", message.id)
        except Exception as error:
            logger.error("Failed to handle fixupx link reply: %s", error, exc_info=True)

    return (on_x_link,)
