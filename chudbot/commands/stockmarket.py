"""Discord commands for the player-controlled stock market."""

from __future__ import annotations

import asyncio
from typing import Optional

from interactions import OptionType, SlashContext, slash_command, slash_option

from chudbot.command_handler import CommandHandler
from chudbot.economy.responses import defer_ping, send_ping
from chudbot.economy.stockmarket import (
    execute_stock_trade,
    load_stock_market,
)


def _guild_id(ctx: SlashContext) -> Optional[int]:
    raw_id = getattr(ctx, "guild_id", None)
    if raw_id is None:
        raw_id = getattr(getattr(ctx, "guild", None), "id", None)
    return None if raw_id is None else int(raw_id)


_TRADE_LOCKS: dict[int, asyncio.Lock] = {}


def _trade_lock(guild_id: int) -> asyncio.Lock:
    return _TRADE_LOCKS.setdefault(guild_id, asyncio.Lock())


def setup(handler: CommandHandler) -> None:
    store = handler.resources.economy_store

    @slash_command(
        name="stock",
        description="View and trade the player-controlled stock market",
    )
    @slash_option(
        name="action",
        description="What to do",
        required=False,
        opt_type=OptionType.STRING,
        choices=[
            {"name": "Market overview", "value": "view"},
            {"name": "Ticker quote", "value": "quote"},
            {"name": "My stats", "value": "stats"},
            {"name": "Buy shares", "value": "buy"},
            {"name": "Sell shares", "value": "sell"},
            {"name": "Open short", "value": "short"},
            {"name": "Cover short", "value": "cover"},
        ],
    )
    @slash_option(
        name="symbol",
        description="Ticker symbol, such as RKLB or LMT",
        required=False,
        opt_type=OptionType.STRING,
    )
    @slash_option(
        name="quantity",
        description="Number of shares for a trade",
        required=False,
        opt_type=OptionType.INTEGER,
        min_value=1,
    )
    async def stock_command(
        ctx: SlashContext,
        action: Optional[str] = "view",
        symbol: Optional[str] = None,
        quantity: Optional[int] = None,
    ) -> None:
        await defer_ping(ctx)
        guild_id = _guild_id(ctx)
        if guild_id is None:
            await send_ping(ctx, "The stock market can only be used in a server.", ephemeral=True)
            return

        action = (action or "view").casefold()
        player_id = int(ctx.author.id)

        try:
            if action in {"buy", "sell", "short", "cover"}:
                if not symbol or quantity is None:
                    await send_ping(ctx, "Trades need both a ticker and a positive share quantity.", ephemeral=True)
                    return
                # Loading and saving a full market snapshot must be one
                # serialized operation. Otherwise two simultaneous sells can
                # both read the same holdings and both be accepted.
                async with _trade_lock(guild_id):
                    market = await load_stock_market(store, guild_id)
                    result, _ = await execute_stock_trade(
                        store, market, guild_id, player_id, action, symbol, quantity
                    )
                if not result.accepted:
                    await send_ping(ctx, f"❌ {result.reason}", ephemeral=True)
                    return
                message = (
                    f"✅ **{action.title()}** {result.quantity:,} {result.symbol} at "
                    f"${result.price:,.2f}. Cash: **{result.cash:,.0f} coins**."
                )
            else:
                market = await load_stock_market(store, guild_id)
                if action == "view":
                    message = market.view()
                elif action == "quote":
                    if not symbol:
                        await send_ping(ctx, "Choose a ticker for the quote, such as **RKLB**.", ephemeral=True)
                        return
                    message = market.stock(symbol, player_id)
                elif action == "stats":
                    message = market.statistics(player_id)
                else:
                    await send_ping(ctx, "Unknown stock action.", ephemeral=True)
                    return
        except KeyError:
            await send_ping(ctx, f"Unknown ticker **{symbol}**. Try RKLB, LMT, SPCX, or GD.", ephemeral=True)
            return

        await send_ping(ctx, message)

    handler.register_slash_command(stock_command)
