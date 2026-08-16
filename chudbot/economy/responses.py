"""Shared response helpers for economy-facing commands."""

from __future__ import annotations

import logging
import time
from contextvars import ContextVar
from typing import Any, Optional


_interaction_started_at: ContextVar[Optional[float]] = ContextVar(
    "economy_interaction_started_at", default=None
)
_logger = logging.getLogger("chuds.bot.interactions")


async def defer_ping(
    ctx: Any, *, ephemeral: bool = False, edit_origin: bool = False
) -> None:
    """Acknowledge an interaction immediately and record acknowledgement latency."""
    started_at = time.perf_counter()
    _interaction_started_at.set(started_at)
    defer_started_at = time.perf_counter()
    defer_kwargs = {"ephemeral": ephemeral}
    if edit_origin:
        defer_kwargs["edit_origin"] = True
    await ctx.defer(**defer_kwargs)
    defer_ms = (time.perf_counter() - defer_started_at) * 1_000
    _logger.info(
        "interaction_acknowledged command=%s defer_ms=%.1f total_ms=%.1f",
        getattr(ctx, "invoke_target", None) or getattr(ctx, "command_name", "unknown"),
        defer_ms,
        (time.perf_counter() - started_at) * 1_000,
    )


async def send_ping(ctx: Any, content: Optional[str] = None, **kwargs: Any) -> Any:
    """Send a response that mentions the interaction author.

    Autocomplete contexts send choices without message content, so those payloads
    pass through unchanged.
    """
    send_started_at = time.perf_counter()
    # Discord fixes response visibility when an interaction is deferred. Avoid
    # sending an incompatible EPHEMERAL flag while editing a public defer.
    if (
        kwargs.get("ephemeral")
        and getattr(ctx, "deferred", False)
        and not getattr(ctx, "ephemeral", False)
    ):
        kwargs.pop("ephemeral")
    if content is None:
        result = await ctx.send(**kwargs)
        _log_response_timing(ctx, send_started_at)
        return result

    mention = getattr(getattr(ctx, "author", None), "mention", None)
    if mention and mention not in content:
        content = f"{mention} {content}"

    # Preserve intentionally restricted mentions (such as leaderboard entries)
    # while allowing the command author mention added above to notify them.
    allowed_mentions = kwargs.get("allowed_mentions")
    author_id = getattr(getattr(ctx, "author", None), "id", None)
    if mention and author_id is not None and isinstance(allowed_mentions, dict):
        allowed_mentions = dict(allowed_mentions)
        users = list(allowed_mentions.get("users", ()))
        if author_id not in users and str(author_id) not in users:
            users.append(str(author_id))
        allowed_mentions["users"] = users
        kwargs["allowed_mentions"] = allowed_mentions
    result = await ctx.send(content, **kwargs)
    _log_response_timing(ctx, send_started_at)
    return result


def _log_response_timing(ctx: Any, send_started_at: float) -> None:
    now = time.perf_counter()
    interaction_started_at = _interaction_started_at.get()
    _logger.info(
        "interaction_responded command=%s discord_send_ms=%.1f total_ms=%s",
        getattr(ctx, "invoke_target", None) or getattr(ctx, "command_name", "unknown"),
        (now - send_started_at) * 1_000,
        "unknown"
        if interaction_started_at is None
        else f"{(now - interaction_started_at) * 1_000:.1f}",
    )
