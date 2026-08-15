"""Guild economy commands: balances, work, gambling, and robbery."""

from __future__ import annotations

import asyncio
import os
import random
from typing import Optional

from interactions import Member, OptionType, SlashContext, slash_command, slash_option

from command_handler import CommandHandler
from economy_store import DEFAULT_POSTGRES_URL, PostgresEconomyStore


MINIMUM_WAGER = 10
WORK_REWARD_MIN = 75
WORK_REWARD_MAX = 200
SLOT_SYMBOLS = ("🍒", "🍋", "🍇", "🔔", "💎")
ROULETTE_RED_NUMBERS = frozenset(
    {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}
)
_random = random.SystemRandom()


def _format_coins(amount: int) -> str:
    return f"{amount:,} coin{'s' if amount != 1 else ''}"


def _format_wait(seconds: int) -> str:
    minutes, remaining = divmod(max(1, seconds), 60)
    if minutes:
        return f"{minutes}m {remaining}s" if remaining else f"{minutes}m"
    return f"{remaining}s"


def _guild_id(ctx: SlashContext) -> Optional[int]:
    raw_id = getattr(ctx, "guild_id", None)
    if raw_id is None:
        guild = getattr(ctx, "guild", None)
        raw_id = getattr(guild, "id", None)
    return None if raw_id is None else int(raw_id)


async def _require_guild(ctx: SlashContext) -> Optional[int]:
    guild_id = _guild_id(ctx)
    if guild_id is None:
        await ctx.send("Economy commands can only be used in a server.", ephemeral=True)
    return guild_id


def setup(handler: CommandHandler) -> None:
    database_url = (
        os.getenv("ECONOMY_DATABASE_URL")
        or os.getenv("DATABASE_URL")
        or DEFAULT_POSTGRES_URL
    )
    store = PostgresEconomyStore(
        database_url,
        min_pool_size=int(os.getenv("ECONOMY_DB_POOL_MIN", "2")),
        max_pool_size=int(os.getenv("ECONOMY_DB_POOL_MAX", "10")),
    )

    @slash_command(name="balance", description="Check an economy balance")
    @slash_option(
        name="user",
        description="User whose balance you want to view",
        required=False,
        opt_type=OptionType.USER,
    )
    async def balance_command(ctx: SlashContext, user: Optional[Member] = None):
        guild_id = await _require_guild(ctx)
        if guild_id is None:
            return
        viewer_id = int(ctx.author.id)
        viewer_balance = await asyncio.to_thread(store.balance, guild_id, viewer_id)
        subject = user or ctx.author
        if int(subject.id) == viewer_id:
            amount = viewer_balance
        else:
            amount = await asyncio.to_thread(store.peek_balance, guild_id, int(subject.id))
            if amount is None:
                await ctx.send(f"{subject.mention} hasn't joined the economy yet.")
                return
        await ctx.send(f"💰 {subject.mention} has **{_format_coins(amount)}**.")

    @slash_command(name="leaderboard", description="See the server's 10 richest economy users")
    async def leaderboard_command(ctx: SlashContext):
        guild_id = await _require_guild(ctx)
        if guild_id is None:
            return
        result = await asyncio.to_thread(store.leaderboard, guild_id, int(ctx.author.id), limit=10)
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        lines = [
            f"{medals.get(entry.rank, f'**#{entry.rank}**')} <@{entry.user_id}> — "
            f"**{_format_coins(entry.balance)}**"
            for entry in result.entries
        ]
        lines.extend(
            (
                "",
                f"Your rank: **#{result.user_rank}** — **{_format_coins(result.user_balance)}**",
            )
        )
        await ctx.send(
            "🏆 **Economy Leaderboard**\n" + "\n".join(lines),
            allowed_mentions={"parse": []},
        )

    @slash_command(name="work", description="Work for coins (3-minute cooldown)")
    async def work_command(ctx: SlashContext):
        guild_id = await _require_guild(ctx)
        if guild_id is None:
            return
        reward = _random.randint(WORK_REWARD_MIN, WORK_REWARD_MAX)
        result = await asyncio.to_thread(store.work, guild_id, int(ctx.author.id), reward)
        if result.retry_after:
            await ctx.send(
                f"⏳ You're tired. You can work again in **{_format_wait(result.retry_after)}**.",
                ephemeral=True,
            )
            return
        await ctx.send(
            f"🔨 You worked a shift and earned **{_format_coins(result.earned)}**. "
            f"Balance: **{_format_coins(result.balance)}**."
        )

    @slash_command(name="gamble", description="Bet coins on a 50/50 game")
    @slash_option(
        name="amount",
        description="Number of coins to bet (minimum 10)",
        required=True,
        opt_type=OptionType.INTEGER,
    )
    async def gamble_command(ctx: SlashContext, amount: int):
        guild_id = await _require_guild(ctx)
        if guild_id is None:
            return
        mention = ctx.author.mention
        if amount < MINIMUM_WAGER:
            await asyncio.to_thread(store.balance, guild_id, int(ctx.author.id))
            await ctx.send(
                f"{mention} The minimum wager is **{_format_coins(MINIMUM_WAGER)}**.",
                ephemeral=True,
            )
            return
        result = await asyncio.to_thread(
            store.gamble,
            guild_id,
            int(ctx.author.id),
            amount,
            _random.random() < 0.5,
        )
        if not result.accepted:
            await ctx.send(
                f"{mention} You can't bet {_format_coins(amount)}. Your balance is "
                f"**{_format_coins(result.balance)}**.",
                ephemeral=True,
            )
            return
        if result.won:
            await ctx.send(
                f"{mention} 🎰 **You won!** You gained {_format_coins(amount)}. "
                f"Balance: **{_format_coins(result.balance)}**."
            )
        else:
            await ctx.send(
                f"{mention} 🎰 You lost **{_format_coins(amount)}**. "
                f"Balance: **{_format_coins(result.balance)}**."
            )

    @slash_command(name="slots", description="Play a three-symbol slot machine")
    @slash_option(
        name="amount",
        description="Number of coins to bet (minimum 10)",
        required=True,
        opt_type=OptionType.INTEGER,
    )
    async def slots_command(ctx: SlashContext, amount: int):
        guild_id = await _require_guild(ctx)
        if guild_id is None:
            return
        mention = ctx.author.mention
        if amount < MINIMUM_WAGER:
            await asyncio.to_thread(store.balance, guild_id, int(ctx.author.id))
            await ctx.send(
                f"{mention} The minimum wager is **{_format_coins(MINIMUM_WAGER)}**.",
                ephemeral=True,
            )
            return

        symbols = tuple(_random.choice(SLOT_SYMBOLS) for _ in range(3))
        if symbols == ("💎", "💎", "💎"):
            profit = amount * 10
            outcome = "**JACKPOT!**"
        elif len(set(symbols)) == 1:
            profit = amount * 5
            outcome = "**Triple!**"
        elif len(set(symbols)) == 2:
            profit = amount // 2
            outcome = "**Pair!**"
        else:
            profit = -amount
            outcome = "No match."

        result = await asyncio.to_thread(
            store.settle_wager,
            guild_id,
            int(ctx.author.id),
            amount,
            profit=profit,
        )
        if not result.accepted:
            await ctx.send(
                f"{mention} You can't bet {_format_coins(amount)}. Your balance is "
                f"**{_format_coins(result.balance)}**.",
                ephemeral=True,
            )
            return
        change = (
            f"You gained **{_format_coins(result.profit)}**"
            if result.profit >= 0
            else f"You lost **{_format_coins(-result.profit)}**"
        )
        await ctx.send(
            f"{mention} 🎰 {' | '.join(symbols)}\n{outcome} {change}. "
            f"Balance: **{_format_coins(result.balance)}**."
        )

    @slash_command(name="roulette", description="Bet on red, black, or green roulette")
    @slash_option(
        name="color",
        description="Color to bet on: red, black, or green",
        required=True,
        opt_type=OptionType.STRING,
    )
    @slash_option(
        name="amount",
        description="Number of coins to bet (minimum 10)",
        required=True,
        opt_type=OptionType.INTEGER,
    )
    async def roulette_command(ctx: SlashContext, amount: int, color: str):
        guild_id = await _require_guild(ctx)
        if guild_id is None:
            return
        mention = ctx.author.mention
        selected_color = color.strip().lower()
        if selected_color not in {"red", "black", "green"}:
            await asyncio.to_thread(store.balance, guild_id, int(ctx.author.id))
            await ctx.send(
                f"{mention} Choose **red**, **black**, or **green**.",
                ephemeral=True,
            )
            return
        if amount < MINIMUM_WAGER:
            await asyncio.to_thread(store.balance, guild_id, int(ctx.author.id))
            await ctx.send(
                f"{mention} The minimum wager is **{_format_coins(MINIMUM_WAGER)}**.",
                ephemeral=True,
            )
            return

        number = _random.randint(0, 36)
        landed_color = "green" if number == 0 else "red" if number in ROULETTE_RED_NUMBERS else "black"
        won = selected_color == landed_color
        profit = amount * 35 if won and selected_color == "green" else amount if won else -amount
        result = await asyncio.to_thread(
            store.settle_wager,
            guild_id,
            int(ctx.author.id),
            amount,
            profit=profit,
        )
        if not result.accepted:
            await ctx.send(
                f"{mention} You can't bet {_format_coins(amount)}. Your balance is "
                f"**{_format_coins(result.balance)}**.",
                ephemeral=True,
            )
            return
        if won:
            outcome = f"You gained **{_format_coins(result.profit)}**!"
        else:
            outcome = f"You lost **{_format_coins(amount)}**."
        await ctx.send(
            f"{mention} 🎡 The wheel landed on **{number} {landed_color}**. {outcome} "
            f"Balance: **{_format_coins(result.balance)}**."
        )

    @slash_command(name="rob", description="Try to rob a recently active economy user")
    @slash_option(
        name="user",
        description="User to rob (must have used economy in the last 15 minutes)",
        required=True,
        opt_type=OptionType.USER,
    )
    async def rob_command(ctx: SlashContext, user: Member):
        guild_id = await _require_guild(ctx)
        if guild_id is None:
            return
        robber_id = int(ctx.author.id)
        target_id = int(user.id)
        if target_id == robber_id:
            await asyncio.to_thread(store.balance, guild_id, robber_id)
            await ctx.send("You can't rob yourself.", ephemeral=True)
            return
        if bool(getattr(user, "bot", False)):
            await asyncio.to_thread(store.balance, guild_id, robber_id)
            await ctx.send("You can't rob a bot.", ephemeral=True)
            return

        result = await asyncio.to_thread(
            store.rob,
            guild_id,
            robber_id,
            target_id,
            succeeded=_random.random() < 0.45,
            steal_percent=_random.randint(10, 30),
            fine_percent=_random.randint(5, 15),
        )
        if result.status == "cooldown":
            await ctx.send(
                f"⏳ Lie low for **{_format_wait(result.retry_after)}** before robbing again.",
                ephemeral=True,
            )
        elif result.status == "inactive":
            await ctx.send(
                f"{user.mention} hasn't used an economy command in the last **15 minutes**.",
                ephemeral=True,
            )
        elif result.status == "broke":
            await ctx.send(f"{user.mention} has nothing to steal.", ephemeral=True)
        elif result.status == "success":
            await ctx.send(
                f"🦹 You stole **{_format_coins(result.amount)}** from {user.mention}! "
                f"Balance: **{_format_coins(result.robber_balance)}**."
            )
        else:
            await ctx.send(
                f"🚓 You were caught and paid {user.mention} **{_format_coins(result.amount)}**. "
                f"Balance: **{_format_coins(result.robber_balance)}**."
            )

    handler.register_slash_command(balance_command)
    handler.register_slash_command(leaderboard_command)
    handler.register_slash_command(work_command)
    handler.register_slash_command(gamble_command)
    handler.register_slash_command(slots_command)
    handler.register_slash_command(roulette_command)
    handler.register_slash_command(rob_command)
