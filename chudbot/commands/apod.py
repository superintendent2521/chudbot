"""NASA Astronomy Picture of the Day command."""

from __future__ import annotations

import asyncio
import os
from datetime import date
from typing import Any, Optional

import aiohttp
from interactions import OptionType, SlashContext, slash_command, slash_option
from interactions.models import Embed

from chudbot.command_handler import CommandHandler


NASA_APOD_URL = "https://api.nasa.gov/planetary/apod"
FIRST_APOD_DATE = date(1995, 6, 16)
REQUEST_TIMEOUT_SECONDS = 15


def _parse_date(value: Optional[str]) -> Optional[str]:
    if value is None or not value.strip():
        return None
    try:
        requested_date = date.fromisoformat(value.strip())
    except ValueError as error:
        raise ValueError("Use a date in `YYYY-MM-DD` format.") from error
    if requested_date < FIRST_APOD_DATE:
        raise ValueError("NASA's APOD archive starts on `1995-06-16`.")
    if requested_date > date.today():
        raise ValueError("The APOD date cannot be in the future.")
    return requested_date.isoformat()


def _build_embed(data: dict[str, Any]) -> Embed:
    title = str(data.get("title") or "Astronomy Picture of the Day")
    explanation = str(data.get("explanation") or "NASA did not provide an explanation.")
    if len(explanation) > 3900:
        explanation = f"{explanation[:3897]}..."

    media_url = str(data.get("hdurl") or data.get("url") or "")
    page_date = str(data.get("date") or "")
    copyright_name = str(data.get("copyright") or "").strip()
    details = [f"**Date:** `{page_date}`"] if page_date else []
    if copyright_name:
        details.append(f"**Credit:** {copyright_name}")
    description = "\n".join(details + ["", explanation])

    embed = Embed(
        title=title,
        description=description,
        color=0x0B3D91,
    )
    if media_url:
        embed.url = media_url

    media_type = str(data.get("media_type") or "").lower()
    if media_type == "image" and media_url:
        embed.set_image(url=media_url)
    elif media_type == "video":
        thumbnail_url = str(data.get("thumbnail_url") or "")
        if thumbnail_url:
            embed.set_image(url=thumbnail_url)
        video_url = str(data.get("url") or "")
        if video_url:
            embed.description = f"{description}\n\n[Watch the video]({video_url})"
    return embed


def setup(handler: CommandHandler) -> None:
    logger = handler.resources.logger
    api_key = os.getenv("NASA_API_KEY", "").strip() or "DEMO_KEY"

    @slash_command(name="apod", description="Show NASA's Astronomy Picture of the Day")
    @slash_option(
        name="date",
        description="Optional archive date in YYYY-MM-DD format",
        opt_type=OptionType.STRING,
        required=False,
    )
    async def apod_command(ctx: SlashContext, date: Optional[str] = None):
        try:
            requested_date = _parse_date(date)
        except ValueError as error:
            await ctx.send(str(error), ephemeral=True)
            return

        await ctx.defer()
        params = {"api_key": api_key, "thumbs": "true"}
        if requested_date:
            params["date"] = requested_date

        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(NASA_APOD_URL, params=params) as response:
                    if response.status != 200:
                        logger.warning("NASA APOD returned HTTP %s", response.status)
                        await ctx.send("NASA couldn't provide that APOD right now. Please try again later.")
                        return
                    data = await response.json()
        except (asyncio.TimeoutError, aiohttp.ClientError) as error:
            logger.warning("NASA APOD request failed: %s", error)
            await ctx.send("NASA took too long to respond. Please try again later.")
            return
        except Exception as error:
            logger.error("Unexpected APOD error: %s", error, exc_info=True)
            await ctx.send("Something went wrong while loading NASA's APOD.")
            return

        if not isinstance(data, dict):
            logger.warning("NASA APOD returned an unexpected response: %r", data)
            await ctx.send("NASA returned an unexpected APOD response. Please try again later.")
            return
        await ctx.send(embed=_build_embed(data))

    handler.register_slash_command(apod_command)
