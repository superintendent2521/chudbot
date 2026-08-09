"""Listener that rewrites x.com links to fixupx.com alternatives."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, List, Mapping, Optional
from urllib.parse import urlsplit, urlunsplit

import aiohttp
from interactions import listen
from interactions.api.events.discord import MessageCreate

# Matches standard X (formerly Twitter) URLs. Allows optional scheme and stops at whitespace/angle brackets.
X_LINK_PATTERN = re.compile(
    r"(?:https?://)?(?:www\.)?x\.com/[\w\-./?%&#=:+,;~]+",
    re.IGNORECASE,
)
TRAILING_PUNCTUATION = ".,;:!?)]}\"'"
STATUS_PATH_PATTERN = re.compile(r"/(?:status|statuses)/(\d{2,20})(?:/|$)", re.IGNORECASE)
FXTWITTER_STATUS_API = "https://api.fxtwitter.com/2/status/{tweet_id}"
API_TIMEOUT_SECONDS = 6


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


def _extract_status_id(url: str) -> Optional[str]:
    """Return the post snowflake from an X status URL, if it has one."""
    trimmed = url.rstrip(TRAILING_PUNCTUATION)
    normalized = trimmed if trimmed.startswith(("http://", "https://")) else f"https://{trimmed}"
    match = STATUS_PATH_PATTERN.search(urlsplit(normalized).path)
    return match.group(1) if match else None


def _status_has_video(status: Any) -> bool:
    """Detect native videos/GIFs, external videos, and videos in quoted posts."""
    if not isinstance(status, Mapping):
        return False

    media = status.get("media")
    if isinstance(media, Mapping):
        videos = media.get("videos")
        if isinstance(videos, list) and videos:
            return True

        external = media.get("external")
        if isinstance(external, Mapping) and external.get("type") == "video":
            return True

        # The combined list also identifies animated GIFs, which FixupX serves
        # as playable video and should therefore still receive a fixed link.
        all_media = media.get("all")
        if isinstance(all_media, list) and any(
            isinstance(item, Mapping) and item.get("type") in {"video", "gif"}
            for item in all_media
        ):
            return True

        photos = media.get("photos")
        if isinstance(photos, list) and any(
            isinstance(item, Mapping) and item.get("type") == "gif" for item in photos
        ):
            return True

    return _status_has_video(status.get("quote"))


async def _tweet_has_video(
    tweet_id: str,
    session: aiohttp.ClientSession,
    logger: logging.Logger,
) -> bool:
    """Ask FxTwitter for media metadata; API failures intentionally stay silent."""
    try:
        async with session.get(FXTWITTER_STATUS_API.format(tweet_id=tweet_id)) as response:
            if response.status != 200:
                logger.debug(
                    "FxTwitter lookup for status %s returned HTTP %s",
                    tweet_id,
                    response.status,
                )
                return False
            payload = await response.json(content_type=None)
    except (asyncio.TimeoutError, aiohttp.ClientError, ValueError):
        logger.debug("FxTwitter lookup failed for status %s", tweet_id, exc_info=True)
        return False

    if not isinstance(payload, Mapping) or payload.get("code") != 200:
        return False
    return _status_has_video(payload.get("status"))


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

            candidates: List[tuple[str, str]] = []
            for match in matches:
                original_url = match.group(0)
                tweet_id = _extract_status_id(original_url)
                if not tweet_id:
                    continue
                try:
                    candidates.append((tweet_id, _to_fixupx(original_url)))
                except Exception:
                    logger.debug("Skipping malformed X URL: %s", original_url, exc_info=True)
                    continue

            if not candidates:
                return

            unique_ids = list(dict.fromkeys(tweet_id for tweet_id, _ in candidates))
            timeout = aiohttp.ClientTimeout(total=API_TIMEOUT_SECONDS)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                video_results = await asyncio.gather(
                    *(_tweet_has_video(tweet_id, session, logger) for tweet_id in unique_ids)
                )
            video_ids = {
                tweet_id for tweet_id, has_video in zip(unique_ids, video_results) if has_video
            }
            replacements: List[str] = [
                replacement for tweet_id, replacement in candidates if tweet_id in video_ids
            ]
            if not replacements:
                return

            reply_text = "\n".join(dict.fromkeys(replacements))  # remove duplicates, keep order
            await message.reply(reply_text)
            logger.info("Replied with fixupx links for message %s", message.id)
        except Exception as error:
            logger.error("Failed to handle fixupx link reply: %s", error, exc_info=True)

    return (on_x_link,)
