"""Member join handler that appends 'john' to new member nicknames."""

from __future__ import annotations

import logging
from typing import Tuple

from interactions import listen
from interactions.api.events.discord import MemberAdd


def create_member_join_listeners(logger: logging.Logger) -> Tuple:
    """Create listeners for member join events.
    
    Args:
        logger: Logger instance for logging events
        
    Returns:
        Tuple of listener functions
    """
    @listen(MemberAdd)
    async def on_member_join(event: MemberAdd):
        """Handle member join event by prepending 'john' to their nickname."""
        try:
            # Get the member that just joined
            member = event.member
            original_name = member.user.username
            new_nickname = f"john {original_name}"
            
            # Modify the member's nickname in the guild
            await event.client.http.modify_current_user_nick(
                guild_id=event.guild_id,
                nickname=new_nickname
            )
            
            logger.info(
                "Modified nickname for %s (%s) in guild %s to '%s'",
                original_name,
                member.user.id,
                event.guild_id,
                new_nickname,
            )
        except Exception as error:
            logger.error(
                "Failed to modify nickname for member in guild %s: %s",
                event.guild_id,
                error,
            )

    return (on_member_join,)
