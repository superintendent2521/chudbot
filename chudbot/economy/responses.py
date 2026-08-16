"""Shared response helpers for economy-facing commands."""

from __future__ import annotations

from typing import Any, Optional


async def send_ping(ctx: Any, content: Optional[str] = None, **kwargs: Any) -> Any:
    """Send a response that mentions the interaction author.

    Autocomplete contexts send choices without message content, so those payloads
    pass through unchanged.
    """
    if content is None:
        return await ctx.send(**kwargs)

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
    return await ctx.send(content, **kwargs)
