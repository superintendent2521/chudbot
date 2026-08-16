"""Guild economy commands: balances, work, gambling, and robbery."""

from __future__ import annotations

import asyncio
import random
import secrets
from typing import Any, Optional, cast

from interactions import (
    AutocompleteContext,
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

from chudbot.games.blackjack import hand_value, new_game, play_dealer, profit as blackjack_profit
from chudbot.command_handler import CommandHandler
from chudbot.economy.responses import defer_ping, send_ping
from chudbot.economy.store import (
    BASE_ROB_SUCCESS_PERCENT,
    MAX_DUMPSTER_SPEED_TIER,
    MAX_LOAN_AMOUNT,
    MAX_SECURITY_LEVEL,
    rob_success_chance,
)
from chudbot.games.spaceflight_bounties import BOUNTIES


MINIMUM_WAGER = 10
WORK_REWARD_MIN = 75
WORK_REWARD_MAX = 200
MEMORY_REWARD = 250
MEMORY_SYMBOLS = ("🚀", "🛰️", "🌕", "🪐", "☄️")
FISH_CATCHES = (
    ("an old boot", 20, 35, 18),
    ("a bluegill", 45, 75, 30),
    ("a trout", 70, 110, 25),
    ("a salmon", 100, 160, 17),
    ("a golden koi", 200, 300, 8),
    ("the legendary voidfish", 500, 750, 2),
)
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
        await send_ping(ctx, "Economy commands can only be used in a server.", ephemeral=True)
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
        await defer_ping(ctx)
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
                await send_ping(ctx, f"{subject.mention} hasn't joined the economy yet.")
                return
        await send_ping(ctx, f"💰 {subject.mention} has **{_format_coins(amount)}**.")

    @slash_command(name="leaderboard", description="See the server's 10 richest economy users")
    async def leaderboard_command(ctx: SlashContext):
        await defer_ping(ctx)
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
        await send_ping(ctx,
            "🏆 **Economy Leaderboard**\n" + "\n".join(lines),
            allowed_mentions={"parse": []},
        )

    @slash_command(name="work", description="Work for coins (3-minute cooldown)")
    async def work_command(ctx: SlashContext):
        await defer_ping(ctx)
        guild_id = await _require_guild(ctx)
        if guild_id is None:
            return
        reward = _random.randint(WORK_REWARD_MIN, WORK_REWARD_MAX)
        result = await store.work(guild_id, int(ctx.author.id), reward)
        if result.retry_after:
            await send_ping(ctx,
                f"⏳ You're tired. You can work again in **{_format_wait(result.retry_after)}**.",
                ephemeral=True,
            )
            return
        if result.garnished:
            await send_ping(ctx,
                f"🔨 You earned **{_format_coins(result.gross_earned)}**. "
                f"The bot garnished **{_format_coins(result.garnished)}** for your overdue loan, "
                f"so you received **{_format_coins(result.earned)}**. "
                f"Debt remaining: **{_format_coins(result.loan_remaining)}**. "
                f"Balance: **{_format_coins(result.balance)}**."
            )
            return
        await send_ping(ctx,
            f"🔨 You worked a shift and earned **{_format_coins(result.earned)}**. "
            f"Balance: **{_format_coins(result.balance)}**."
        )

    @slash_command(name="loan", description="Take a one-hour loan or check your current debt")
    @slash_option(
        name="amount",
        description=f"Coins to borrow (100-{MAX_LOAN_AMOUNT:,}); omit to check your loan",
        required=False,
        opt_type=OptionType.INTEGER,
    )
    async def loan_command(ctx: SlashContext, amount: Optional[int] = None):
        await defer_ping(ctx)
        guild_id = await _require_guild(ctx)
        if guild_id is None:
            return
        user_id = int(ctx.author.id)
        if amount is None:
            result = await store.loan_status(guild_id, user_id)
            if result.status == "none":
                await send_ping(ctx,
                    f"🏦 You have no active loan. You can borrow up to "
                    f"**{_format_coins(MAX_LOAN_AMOUNT)}**.",
                    ephemeral=True,
                )
                return
            await send_ping(ctx,
                f"🏦 You owe **{_format_coins(result.loan_balance)}**, due "
                f"<t:{result.loan_due}:R>. After that, half of each `/work` wage is garnished. "
                f"Balance: **{_format_coins(result.balance)}**.",
                ephemeral=True,
            )
            return

        result = await store.take_loan(guild_id, user_id, amount)
        if result.status == "invalid":
            await send_ping(ctx,
                f"Loans must be between **100** and **{_format_coins(MAX_LOAN_AMOUNT)}**.",
                ephemeral=True,
            )
        elif result.status == "active":
            await send_ping(ctx,
                f"You already owe **{_format_coins(result.loan_balance)}**, due "
                f"<t:{result.loan_due}:R>. Repay it before taking another loan.",
                ephemeral=True,
            )
        else:
            await send_ping(ctx,
                f"🏦 Loan approved for **{_format_coins(result.amount)}**. Repay it by "
                f"<t:{result.loan_due}:R>. Balance: **{_format_coins(result.balance)}**."
            )

    @slash_command(name="repay", description="Repay some or all of your bot loan")
    @slash_option(
        name="amount",
        description="Coins to repay; omit to repay as much as possible",
        required=False,
        opt_type=OptionType.INTEGER,
    )
    async def repay_command(ctx: SlashContext, amount: Optional[int] = None):
        await defer_ping(ctx)
        guild_id = await _require_guild(ctx)
        if guild_id is None:
            return
        result = await store.repay_loan(guild_id, int(ctx.author.id), amount)
        if result.status == "none":
            await send_ping(ctx, "You don't have an active loan.", ephemeral=True)
        elif result.status == "invalid":
            await send_ping(ctx,
                f"You can't make that payment. Balance: **{_format_coins(result.balance)}**.",
                ephemeral=True,
            )
        elif result.loan_balance:
            await send_ping(ctx,
                f"🏦 You repaid **{_format_coins(result.amount)}**. Remaining debt: "
                f"**{_format_coins(result.loan_balance)}**. Balance: "
                f"**{_format_coins(result.balance)}**."
            )
        else:
            await send_ping(ctx,
                f"🏦 Loan fully repaid with **{_format_coins(result.amount)}**. "
                f"Balance: **{_format_coins(result.balance)}**."
            )

    @slash_command(name="fish", description="Go fishing for coins (5-minute cooldown)")
    async def fish_command(ctx: SlashContext):
        await defer_ping(ctx)
        guild_id = await _require_guild(ctx)
        if guild_id is None:
            return
        user_id = int(ctx.author.id)
        started = await store.start_activity(guild_id, user_id, "fish")
        if not started.started:
            await send_ping(ctx,
                f"🎣 The fish aren't biting yet. Try again in "
                f"**{_format_wait(started.retry_after)}**.",
                ephemeral=True,
            )
            return
        catch = _random.choices(FISH_CATCHES, weights=[entry[3] for entry in FISH_CATCHES], k=1)[0]
        reward = _random.randint(catch[1], catch[2])
        balance = await store.credit_activity_reward(guild_id, user_id, reward)
        await send_ping(ctx,
            f"🎣 {ctx.author.mention} caught **{catch[0]}** worth "
            f"**{_format_coins(reward)}**. Balance: **{_format_coins(balance)}**."
        )

    @slash_command(name="memory", description="Repeat a hidden space sequence for coins")
    async def memory_command(ctx: SlashContext):
        await defer_ping(ctx)
        guild_id = await _require_guild(ctx)
        if guild_id is None:
            return
        user_id = int(ctx.author.id)
        started = await store.start_activity(guild_id, user_id, "memory")
        if not started.started:
            await send_ping(ctx,
                f"🧠 You can play memory again in **{_format_wait(started.retry_after)}**.",
                ephemeral=True,
            )
            return

        sequence = tuple(_random.choice(MEMORY_SYMBOLS) for _ in range(5))
        game_id = secrets.token_hex(8)
        buttons = [
            Button(
                custom_id=f"memory_{game_id}_{index}",
                style=ButtonStyle.PRIMARY,
                emoji=symbol,
            )
            for index, symbol in enumerate(MEMORY_SYMBOLS)
        ]
        message = await send_ping(ctx,
            f"🧠 {ctx.author.mention} memorize this sequence:\n\n"
            f"# {'  '.join(sequence)}\n\nIt disappears in **4 seconds**."
        )
        await asyncio.sleep(4)
        await message.edit(
            content="🧠 Repeat the five-symbol sequence:",
            components=buttons,
        )

        async def memory_player_only(component: Any) -> bool:
            if int(component.ctx.author.id) == user_id:
                return True
            await send_ping(component.ctx, "This isn't your memory game.", ephemeral=True)
            return False

        for position, expected in enumerate(sequence):
            try:
                component = await handler.bot.wait_for_component(
                    components=cast(Any, buttons),
                    check=memory_player_only,
                    timeout=20,
                )
            except asyncio.TimeoutError:
                for button in buttons:
                    button.disabled = True
                await message.edit(content="🧠 Time expired. No reward this round.", components=buttons)
                return
            await defer_ping(component.ctx, edit_origin=True)
            selected_index = int(component.ctx.custom_id.rsplit("_", 1)[1])
            if MEMORY_SYMBOLS[selected_index] != expected:
                for button in buttons:
                    button.disabled = True
                await component.ctx.edit_origin(
                    content=(
                        f"🧠 Wrong symbol. The sequence was **{' '.join(sequence)}**. "
                        "No reward this round."
                    ),
                    components=buttons,
                )
                return
            if position < len(sequence) - 1:
                await component.ctx.edit_origin(
                    content=f"🧠 Correct so far: **{position + 1}/5**",
                    components=buttons,
                )
                continue

            for button in buttons:
                button.disabled = True
            balance = await store.credit_activity_reward(guild_id, user_id, MEMORY_REWARD)
            await component.ctx.edit_origin(
                content=(
                    f"🧠 **Perfect memory!** You earned **{_format_coins(MEMORY_REWARD)}**. "
                    f"Balance: **{_format_coins(balance)}**."
                ),
                components=buttons,
            )

    @slash_command(name="bounty", description="Complete a spaceflight bounty for coins")
    async def bounty_command(ctx: SlashContext):
        await defer_ping(ctx)
        guild_id = await _require_guild(ctx)
        if guild_id is None:
            return
        user_id = int(ctx.author.id)
        started = await store.start_activity(guild_id, user_id, "bounty")
        if not started.started:
            await send_ping(ctx,
                f"🚀 No new bounty yet. Check again in **{_format_wait(started.retry_after)}**.",
                ephemeral=True,
            )
            return

        bounty = _random.choice(BOUNTIES)
        answers = [(bounty.correct_answer, True)] + [
            (answer, False) for answer in bounty.wrong_answers
        ]
        _random.shuffle(answers)
        game_id = secrets.token_hex(8)
        buttons = [
            Button(
                custom_id=f"bounty_{game_id}_{index}",
                style=ButtonStyle.PRIMARY,
                label=answer,
            )
            for index, (answer, _) in enumerate(answers)
        ]
        message = await send_ping(ctx,
            f"🚀 **Spaceflight Bounty** — {ctx.author.mention}\n{bounty.question}\n"
            "You have **45 seconds**.",
            components=buttons,
        )

        async def bounty_player_only(component: Any) -> bool:
            if int(component.ctx.author.id) == user_id:
                return True
            await send_ping(component.ctx, "This isn't your bounty.", ephemeral=True)
            return False

        try:
            component = await handler.bot.wait_for_component(
                components=cast(Any, buttons),
                check=bounty_player_only,
                timeout=45,
            )
        except asyncio.TimeoutError:
            for button in buttons:
                button.disabled = True
            await message.edit(
                content=f"🚀 Bounty expired. The answer was **{bounty.correct_answer}**.",
                components=buttons,
            )
            return

        await defer_ping(component.ctx, edit_origin=True)
        selected_index = int(component.ctx.custom_id.rsplit("_", 1)[1])
        for button in buttons:
            button.disabled = True
        if not answers[selected_index][1]:
            await component.ctx.edit_origin(
                content=f"🚀 Incorrect. The answer was **{bounty.correct_answer}**.",
                components=buttons,
            )
            return
        reward = _random.randint(175, 300)
        balance = await store.credit_activity_reward(guild_id, user_id, reward)
        await component.ctx.edit_origin(
            content=(
                f"🚀 **Bounty complete!** You earned **{_format_coins(reward)}**. "
                f"Balance: **{_format_coins(balance)}**."
            ),
            components=buttons,
        )

    @slash_command(name="gamble", description="Bet coins on a 50/50 game")
    @slash_option(
        name="amount",
        description="Number of coins to bet (minimum 10)",
        required=True,
        opt_type=OptionType.INTEGER,
    )
    async def gamble_command(ctx: SlashContext, amount: int):
        await defer_ping(ctx)
        guild_id = await _require_guild(ctx)
        if guild_id is None:
            return
        mention = ctx.author.mention
        if amount < MINIMUM_WAGER:
            await store.balance(guild_id, int(ctx.author.id))
            await send_ping(ctx,
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
            await send_ping(ctx,
                f"{mention} You can't bet {_format_coins(amount)}. Your balance is "
                f"**{_format_coins(result.balance)}**.",
                ephemeral=True,
            )
            return
        if result.won:
            await send_ping(ctx,
                f"{mention} 🎰 **You won!** You gained {_format_coins(amount)}. "
                f"Balance: **{_format_coins(result.balance)}**."
            )
        else:
            await send_ping(ctx,
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
        await defer_ping(ctx)
        guild_id = await _require_guild(ctx)
        if guild_id is None:
            return
        mention = ctx.author.mention
        if amount < MINIMUM_WAGER:
            await store.balance(guild_id, int(ctx.author.id))
            await send_ping(ctx,
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
            await send_ping(ctx,
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
        await send_ping(ctx,
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
        await defer_ping(ctx)
        guild_id = await _require_guild(ctx)
        if guild_id is None:
            return
        mention = ctx.author.mention
        selected_color = color.strip().lower()
        if selected_color not in {"red", "black", "green"}:
            await store.balance(guild_id, int(ctx.author.id))
            await send_ping(ctx,
                f"{mention} Choose **red**, **black**, or **green**.",
                ephemeral=True,
            )
            return
        if amount < MINIMUM_WAGER:
            await store.balance(guild_id, int(ctx.author.id))
            await send_ping(ctx,
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
            await send_ping(ctx,
                f"{mention} You can't bet {_format_coins(amount)}. Your balance is "
                f"**{_format_coins(result.balance)}**.",
                ephemeral=True,
            )
            return
        if won:
            outcome = f"You gained **{_format_coins(result.profit)}**!"
        else:
            outcome = f"You lost **{_format_coins(amount)}**."
        await send_ping(ctx,
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
            await send_ping(game_ctx, "Enter a whole number of coins.", ephemeral=True)
            return

        await defer_ping(game_ctx)
        mention = game_ctx.author.mention
        if amount < MINIMUM_WAGER:
            await store.balance(guild_id, int(ctx.author.id))
            await send_ping(game_ctx,
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
            await send_ping(game_ctx,
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
            await send_ping(game_ctx, await finish_game())
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
        message = await send_ping(game_ctx,
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
            await send_ping(component.ctx, "This isn't your blackjack hand.", ephemeral=True)
            return False

        while True:
            try:
                component = await handler.bot.wait_for_component(
                    components=cast(Any, buttons),
                    check=is_player,
                    timeout=60,
                )
            except asyncio.TimeoutError:
                hit_button.disabled = True
                stand_button.disabled = True
                await message.edit(content=await finish_game(timed_out=True), components=buttons)
                return

            await defer_ping(component.ctx, edit_origin=True)
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
        await defer_ping(ctx)
        guild_id = await _require_guild(ctx)
        if guild_id is None:
            return
        robber_id = int(ctx.author.id)
        target_id = int(user.id)
        if target_id == robber_id:
            await store.balance(guild_id, robber_id)
            await send_ping(ctx, "You can't rob yourself.", ephemeral=True)
            return
        if bool(getattr(user, "bot", False)):
            await store.balance(guild_id, robber_id)
            await send_ping(ctx, "You can't rob a bot.", ephemeral=True)
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
            await send_ping(ctx,
                f"⏳ Lie low for **{_format_wait(result.retry_after)}** before robbing again.",
                ephemeral=True,
            )
        elif result.status == "inactive":
            await send_ping(ctx,
                f"{user.mention} hasn't used an economy command in the last **15 minutes**.",
                ephemeral=True,
            )
        elif result.status == "broke":
            await send_ping(ctx, f"{user.mention} has nothing to steal.", ephemeral=True)
        elif result.status == "success":
            await send_ping(ctx,
                f"🦹 You stole **{_format_coins(result.amount)}** from {user.mention}! "
                f"Balance: **{_format_coins(result.robber_balance)}**."
            )
        else:
            await send_ping(ctx,
                f"🚓 You were caught and paid {user.mention} **{_format_coins(result.amount)}**. "
                f"Balance: **{_format_coins(result.robber_balance)}**."
            )

    @slash_command(name="security", description="Buy the next anti-rob security tier")
    async def security_command(ctx: SlashContext):
        await defer_ping(ctx)
        guild_id = await _require_guild(ctx)
        if guild_id is None:
            return
        result = await store.upgrade_security(guild_id, int(ctx.author.id))
        if result.status == "maxed":
            await send_ping(ctx,
                f"🛡️ Your security is already maxed at **tier {MAX_SECURITY_LEVEL}** "
                f"(**{result.protection_percent:.2f}%** protection). Balance: "
                f"**{_format_coins(result.balance)}**.",
                ephemeral=True,
            )
            return
        if result.status == "insufficient":
            await send_ping(ctx,
                f"🛡️ Security tier **{result.level + 1}** costs "
                f"**{_format_coins(result.cost)}**. You are tier **{result.level}/{MAX_SECURITY_LEVEL}** "
                f"with **{result.protection_percent:.2f}%** protection. Balance: "
                f"**{_format_coins(result.balance)}**.",
                ephemeral=True,
            )
            return

        success_percent = rob_success_chance(result.level) * 100
        await send_ping(ctx,
            f"🛡️ Security upgraded to **tier {result.level}/{MAX_SECURITY_LEVEL}**. "
            f"Protection: **{result.protection_percent:.2f}%**; robbers now have a "
            f"**{success_percent:.2f}%** chance to succeed (base "
            f"{BASE_ROB_SUCCESS_PERCENT:.0f}%). Cost: **{_format_coins(result.cost)}**. "
            f"Balance: **{_format_coins(result.balance)}**."
        )

    async def _send_upgrade_device_autocomplete(ctx: AutocompleteContext) -> None:
        search = (ctx.input_text or "").strip().casefold()
        for value, name in (
            ("security", "🛡️ Security"),
            ("dumpster", "🗑️ Dumpster refill"),
        ):
            if not search or search in name.casefold() or search in value.casefold():
                await send_ping(ctx, choices=[{"name": name, "value": value}])
                return
        await send_ping(ctx, choices=[])

    @slash_command(name="upgrade", description="Upgrade a security or dumpster-refill device to a tier")
    @slash_option(
        name="device",
        description="Device to upgrade",
        required=True,
        opt_type=OptionType.STRING,
        autocomplete=True,
    )
    @slash_option(
        name="tier",
        description="Target tier (1 = first upgrade, higher = skip ahead)",
        required=True,
        opt_type=OptionType.INTEGER,
    )
    async def upgrade_command(ctx: SlashContext, device: str, tier: int):
        await defer_ping(ctx)
        guild_id = await _require_guild(ctx)
        if guild_id is None:
            return
        device_key = device.strip().casefold()
        if device_key in ("security", "safe", "anti-rob", "rob"):
            result = await store.upgrade_security(
                guild_id, int(ctx.author.id), target_tier=tier
            )
            if result.status == "maxed":
                await send_ping(ctx,
                    f"🛡️ Your security is already at or above **tier {result.level}/"
                    f"{MAX_SECURITY_LEVEL}** (**{result.protection_percent:.2f}%** "
                    f"protection). Balance: **{_format_coins(result.balance)}**.",
                    ephemeral=True,
                )
                return
            if result.status == "insufficient":
                await send_ping(ctx,
                    f"🛡️ Raising security to **tier {tier}/{MAX_SECURITY_LEVEL}** costs "
                    f"**{_format_coins(result.cost)}**. You're tier "
                    f"**{result.level}/{MAX_SECURITY_LEVEL}** with "
                    f"**{result.protection_percent:.2f}%** protection. Balance: "
                    f"**{_format_coins(result.balance)}**.",
                    ephemeral=True,
                )
                return
            success_percent = rob_success_chance(result.level) * 100
            await send_ping(ctx,
                f"🛡️ Security upgraded to **tier {result.level}/{MAX_SECURITY_LEVEL}**. "
                f"Protection: **{result.protection_percent:.2f}%**; robbers now have a "
                f"**{success_percent:.2f}%** chance to succeed (base "
                f"{BASE_ROB_SUCCESS_PERCENT:.0f}%). Cost: **{_format_coins(result.cost)}**. "
                f"Balance: **{_format_coins(result.balance)}**."
            )
            return
        if device_key in ("dumpster", "refill", "dumpster-speed", "dumpster_speed"):
            result = await store.upgrade_dumpster_speed(
                guild_id, int(ctx.author.id), target_tier=tier
            )
            if result.status == "maxed":
                await send_ping(ctx,
                    f"🗑️ Your dumpster refill speed is already at **tier {result.level}/"
                    f"{MAX_DUMPSTER_SPEED_TIER}** (refill "
                    f"**{_format_wait(result.cooldown_seconds)}**). Balance: "
                    f"**{_format_coins(result.balance)}**.",
                    ephemeral=True,
                )
                return
            if result.status == "insufficient":
                await send_ping(ctx,
                    f"🗑️ Raising dumpster refill speed to **tier {tier}/"
                    f"{MAX_DUMPSTER_SPEED_TIER}** costs **{_format_coins(result.cost)}**. "
                    f"You're tier **{result.level}/{MAX_DUMPSTER_SPEED_TIER}** (refill "
                    f"**{_format_wait(result.cooldown_seconds)}**). Balance: "
                    f"**{_format_coins(result.balance)}**.",
                    ephemeral=True,
                )
                return
            await send_ping(ctx,
                f"🗑️ Dumpster refill speed upgraded to **tier {result.level}/"
                f"{MAX_DUMPSTER_SPEED_TIER}**. Refill time is now "
                f"**{_format_wait(result.cooldown_seconds)}**. Cost: "
                f"**{_format_coins(result.cost)}**. Balance: **{_format_coins(result.balance)}**."
            )
            return
        await send_ping(ctx,
            "Unknown device. Choose **security** or **dumpster** via autocomplete.",
            ephemeral=True,
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
        await defer_ping(ctx)
        guild_id = await _require_guild(ctx)
        if guild_id is None:
            return
        giver_id = int(ctx.author.id)
        recipient_id = int(user.id)
        if recipient_id == giver_id:
            await store.balance(guild_id, giver_id)
            await send_ping(ctx, "You can't gift coins to yourself.", ephemeral=True)
            return
        if amount < 1:
            await store.balance(guild_id, giver_id)
            await send_ping(ctx, "You must gift at least 1 coin.", ephemeral=True)
            return

        result = await store.gift(
            guild_id,
            giver_id,
            recipient_id,
            amount,
        )
        if not result.accepted:
            await send_ping(ctx,
                f"You can't gift {_format_coins(amount)}. Your balance is "
                f"**{_format_coins(result.giver_balance)}**.",
                ephemeral=True,
            )
            return
        await send_ping(ctx,
            f"🎁 You gifted **{_format_coins(result.amount)}** to {user.mention}. "
            f"Your balance: **{_format_coins(result.giver_balance)}**."
        )


    handler.register_slash_command(balance_command)
    handler.register_slash_command(leaderboard_command)
    handler.register_slash_command(work_command)
    handler.register_slash_command(loan_command)
    handler.register_slash_command(repay_command)
    handler.register_slash_command(fish_command)
    handler.register_slash_command(memory_command)
    handler.register_slash_command(bounty_command)
    handler.register_slash_command(gamble_command)
    handler.register_slash_command(slots_command)
    handler.register_slash_command(roulette_command)
    @upgrade_command.autocomplete("device")
    async def upgrade_device_autocomplete(ctx: AutocompleteContext):
        await _send_upgrade_device_autocomplete(ctx)

    handler.register_slash_command(blackjack_command)
    handler.register_slash_command(rob_command)
    handler.register_slash_command(security_command)
    handler.register_slash_command(upgrade_command)
    handler.register_slash_command(gift_command)
    handler.register_listener(open_economy_pool)
