"""Guild economy commands: balances, work, gambling, and robbery."""

from __future__ import annotations

import asyncio
import random
import secrets
from typing import Any, Optional

from interactions import (
    Button,
    ButtonStyle,
    Member,
    Modal,
    OptionType,
    ShortText,
    SlashContext,
    listen,
    slash_command,
    slash_option,
)
from interactions.api.events import WebsocketReady

from blackjack_game import hand_value, new_game, play_dealer, profit as blackjack_profit
from command_handler import CommandHandler
from economy_store import (
    BASE_ROB_SUCCESS_PERCENT,
    MAX_SECURITY_LEVEL,
    rob_success_chance,
)


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


def _blackjack_table(
    mention: str,
    player_hand: list[str],
    dealer_hand: list[str],
    *,
    reveal_dealer: bool,
    footer: str,
) -> str:
    dealer_cards = " ".join(dealer_hand) if reveal_dealer else f"{dealer_hand[0]} ??"
    dealer_score = f" (**{hand_value(dealer_hand)}**)" if reveal_dealer else ""
    return (
        f"{mention} 🃏 **Blackjack**\n"
        f"Dealer: `{dealer_cards}`{dealer_score}\n"
        f"Your hand: `{' '.join(player_hand)}` (**{hand_value(player_hand)}**)\n"
        f"{footer}"
    )


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
    store = handler.resources.economy_store

    @listen(WebsocketReady)
    async def open_economy_pool(_: WebsocketReady) -> None:
        await store.open()

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
        viewer_balance = await store.balance(guild_id, viewer_id)
        subject = user or ctx.author
        if int(subject.id) == viewer_id:
            amount = viewer_balance
        else:
            amount = await store.peek_balance(guild_id, int(subject.id))
            if amount is None:
                await ctx.send(f"{subject.mention} hasn't joined the economy yet.")
                return
        await ctx.send(f"💰 {subject.mention} has **{_format_coins(amount)}**.")

    @slash_command(name="leaderboard", description="See the server's 10 richest economy users")
    async def leaderboard_command(ctx: SlashContext):
        guild_id = await _require_guild(ctx)
        if guild_id is None:
            return
        result = await store.leaderboard(guild_id, int(ctx.author.id), limit=10)
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
        result = await store.work(guild_id, int(ctx.author.id), reward)
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
            await store.balance(guild_id, int(ctx.author.id))
            await ctx.send(
                f"{mention} The minimum wager is **{_format_coins(MINIMUM_WAGER)}**.",
                ephemeral=True,
            )
            return
        result = await store.gamble(
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
            await store.balance(guild_id, int(ctx.author.id))
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

        result = await store.settle_wager(
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
            await store.balance(guild_id, int(ctx.author.id))
            await ctx.send(
                f"{mention} Choose **red**, **black**, or **green**.",
                ephemeral=True,
            )
            return
        if amount < MINIMUM_WAGER:
            await store.balance(guild_id, int(ctx.author.id))
            await ctx.send(
                f"{mention} The minimum wager is **{_format_coins(MINIMUM_WAGER)}**.",
                ephemeral=True,
            )
            return

        number = _random.randint(0, 36)
        landed_color = "green" if number == 0 else "red" if number in ROULETTE_RED_NUMBERS else "black"
        won = selected_color == landed_color
        profit = amount * 35 if won and selected_color == "green" else amount if won else -amount
        result = await store.settle_wager(
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

    @slash_command(name="blackjack", description="Play interactive blackjack")
    async def blackjack_command(ctx: SlashContext):
        guild_id = await _require_guild(ctx)
        if guild_id is None:
            return
        game_id = secrets.token_hex(8)
        wager_modal = Modal(
            ShortText(
                label="Wager",
                custom_id="wager",
                placeholder="Minimum 10 coins",
                min_length=1,
                max_length=18,
            ),
            title="Blackjack",
            custom_id=f"blackjack_wager_{game_id}",
        )
        await ctx.send_modal(wager_modal)
        try:
            game_ctx = await handler.bot.wait_for_modal(
                wager_modal,
                author=ctx.author.id,
                timeout=60,
            )
        except asyncio.TimeoutError:
            return

        raw_amount = game_ctx.responses["wager"].replace(",", "").strip()
        try:
            amount = int(raw_amount)
        except ValueError:
            await game_ctx.send("Enter a whole number of coins.", ephemeral=True)
            return

        mention = game_ctx.author.mention
        if amount < MINIMUM_WAGER:
            await store.balance(guild_id, int(ctx.author.id))
            await game_ctx.send(
                f"{mention} The minimum wager is **{_format_coins(MINIMUM_WAGER)}**.",
                ephemeral=True,
            )
            return

        player_id = int(ctx.author.id)
        reserved = await store.settle_wager(
            guild_id,
            player_id,
            amount,
            profit=-amount,
        )
        if not reserved.accepted:
            await game_ctx.send(
                f"{mention} You can't bet {_format_coins(amount)}. Your balance is "
                f"**{_format_coins(reserved.balance)}**.",
                ephemeral=True,
            )
            return

        deck, player_hand, dealer_hand = new_game(_random)

        async def finish_game(*, timed_out: bool = False) -> str:
            player_value = hand_value(player_hand)
            has_natural = len(player_hand) == 2 and player_value == 21
            dealer_has_natural = len(dealer_hand) == 2 and hand_value(dealer_hand) == 21
            if player_value <= 21 and not has_natural and not dealer_has_natural:
                play_dealer(deck, dealer_hand)
            net_profit, outcome = blackjack_profit(player_hand, dealer_hand, amount)
            payout = amount + net_profit
            balance = await store.pay_reserved_wager(guild_id, player_id, payout)
            if net_profit > 0:
                change = f"You gained **{_format_coins(net_profit)}**."
            elif net_profit < 0:
                change = f"You lost **{_format_coins(-net_profit)}**."
            else:
                change = "Your wager was returned."
            timeout_text = "Time expired, so you stood. " if timed_out else ""
            return _blackjack_table(
                mention,
                player_hand,
                dealer_hand,
                reveal_dealer=True,
                footer=(
                    f"{timeout_text}{outcome} {change} "
                    f"Balance: **{_format_coins(balance)}**."
                ),
            )

        player_natural = len(player_hand) == 2 and hand_value(player_hand) == 21
        dealer_natural = len(dealer_hand) == 2 and hand_value(dealer_hand) == 21
        if player_natural or dealer_natural:
            await game_ctx.send(await finish_game())
            return

        hit_button = Button(
            custom_id=f"blackjack_hit_{game_id}",
            style=ButtonStyle.GREEN,
            label="Hit",
            emoji="🃏",
        )
        stand_button = Button(
            custom_id=f"blackjack_stand_{game_id}",
            style=ButtonStyle.RED,
            label="Stand",
            emoji="✋",
        )
        buttons = [hit_button, stand_button]
        message = await game_ctx.send(
            _blackjack_table(
                mention,
                player_hand,
                dealer_hand,
                reveal_dealer=False,
                footer=f"Wager: **{_format_coins(amount)}**. Choose **Hit** or **Stand**.",
            ),
            components=buttons,
        )

        async def is_player(component: Any) -> bool:
            component_author = getattr(component.ctx, "author", None)
            component_author_id = getattr(component_author, "id", None)
            if component_author_id is not None and int(component_author_id) == player_id:
                return True
            await component.ctx.send("This isn't your blackjack hand.", ephemeral=True)
            return False

        while True:
            try:
                component = await handler.bot.wait_for_component(
                    components=buttons,
                    check=is_player,
                    timeout=60,
                )
            except asyncio.TimeoutError:
                hit_button.disabled = True
                stand_button.disabled = True
                await message.edit(content=await finish_game(timed_out=True), components=buttons)
                return

            if component.ctx.custom_id == hit_button.custom_id:
                player_hand.append(deck.pop())
                if hand_value(player_hand) < 21:
                    await component.ctx.edit_origin(
                        content=_blackjack_table(
                            mention,
                            player_hand,
                            dealer_hand,
                            reveal_dealer=False,
                            footer=(
                                f"Wager: **{_format_coins(amount)}**. "
                                "Choose **Hit** or **Stand**."
                            ),
                        ),
                        components=buttons,
                    )
                    continue

            hit_button.disabled = True
            stand_button.disabled = True
            await component.ctx.edit_origin(content=await finish_game(), components=buttons)
            return

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
            await store.balance(guild_id, robber_id)
            await ctx.send("You can't rob yourself.", ephemeral=True)
            return
        if bool(getattr(user, "bot", False)):
            await store.balance(guild_id, robber_id)
            await ctx.send("You can't rob a bot.", ephemeral=True)
            return

        result = await store.rob(
            guild_id,
            robber_id,
            target_id,
            success_roll=_random.random(),
            steal_percent=_random.randint(3, 8),
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

    @slash_command(name="security", description="Buy the next anti-rob security tier")
    async def security_command(ctx: SlashContext):
        guild_id = await _require_guild(ctx)
        if guild_id is None:
            return
        result = await store.upgrade_security(guild_id, int(ctx.author.id))
        if result.status == "maxed":
            await ctx.send(
                f"🛡️ Your security is already maxed at **tier {MAX_SECURITY_LEVEL}** "
                f"(**{result.protection_percent:.2f}%** protection). Balance: "
                f"**{_format_coins(result.balance)}**.",
                ephemeral=True,
            )
            return
        if result.status == "insufficient":
            await ctx.send(
                f"🛡️ Security tier **{result.level + 1}** costs "
                f"**{_format_coins(result.cost)}**. You are tier **{result.level}/{MAX_SECURITY_LEVEL}** "
                f"with **{result.protection_percent:.2f}%** protection. Balance: "
                f"**{_format_coins(result.balance)}**.",
                ephemeral=True,
            )
            return

        success_percent = rob_success_chance(result.level) * 100
        await ctx.send(
            f"🛡️ Security upgraded to **tier {result.level}/{MAX_SECURITY_LEVEL}**. "
            f"Protection: **{result.protection_percent:.2f}%**; robbers now have a "
            f"**{success_percent:.2f}%** chance to succeed (base "
            f"{BASE_ROB_SUCCESS_PERCENT:.0f}%). Cost: **{_format_coins(result.cost)}**. "
            f"Balance: **{_format_coins(result.balance)}**."
        )

    @slash_command(name="gift", description="Give coins to another user")
    @slash_option(
        name="user",
        description="User to give coins to",
        required=True,
        opt_type=OptionType.USER,
    )
    @slash_option(
        name="amount",
        description="Number of coins to give (minimum 1)",
        required=True,
        opt_type=OptionType.INTEGER,
    )
    async def gift_command(ctx: SlashContext, user: Member, amount: int):
        guild_id = await _require_guild(ctx)
        if guild_id is None:
            return
        giver_id = int(ctx.author.id)
        recipient_id = int(user.id)
        if recipient_id == giver_id:
            await store.balance(guild_id, giver_id)
            await ctx.send("You can't gift coins to yourself.", ephemeral=True)
            return
        if amount < 1:
            await store.balance(guild_id, giver_id)
            await ctx.send("You must gift at least 1 coin.", ephemeral=True)
            return

        result = await store.gift(
            guild_id,
            giver_id,
            recipient_id,
            amount,
        )
        if not result.accepted:
            await ctx.send(
                f"You can't gift {_format_coins(amount)}. Your balance is "
                f"**{_format_coins(result.giver_balance)}**.",
                ephemeral=True,
            )
            return
        await ctx.send(
            f"🎁 You gifted **{_format_coins(result.amount)}** to {user.mention}. "
            f"Your balance: **{_format_coins(result.giver_balance)}**."
        )
    

    handler.register_slash_command(balance_command)
    handler.register_slash_command(leaderboard_command)
    handler.register_slash_command(work_command)
    handler.register_slash_command(gamble_command)
    handler.register_slash_command(slots_command)
    handler.register_slash_command(roulette_command)
    handler.register_slash_command(blackjack_command)
    handler.register_slash_command(rob_command)
    handler.register_slash_command(security_command)
    handler.register_slash_command(gift_command)
    handler.register_listener(open_economy_pool)
