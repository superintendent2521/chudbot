"""Interactive spaceflight dumpster diving and persistent inventory commands."""

from __future__ import annotations

import asyncio
import random
import secrets
from typing import Any, Optional

from interactions import (
    Button,
    ButtonStyle,
    Member,
    OptionType,
    SlashContext,
    slash_command,
    slash_option,
)

from command_handler import CommandHandler
from spaceflight_dumpster import (
    LOCATIONS,
    LOOT_BY_KEY,
    DumpsterLocation,
    hazard_chance,
    lose_half,
    resolve_loot,
    roll_loot,
)


MAX_ROUNDS = 5
LOCATION_TIMEOUT = 30
ACTION_TIMEOUT = 45
_random = random.SystemRandom()


def _guild_id(ctx: SlashContext) -> Optional[int]:
    raw_id = getattr(ctx, "guild_id", None)
    if raw_id is None:
        raw_id = getattr(getattr(ctx, "guild", None), "id", None)
    return None if raw_id is None else int(raw_id)


def _format_wait(seconds: int) -> str:
    minutes, remaining = divmod(max(1, seconds), 60)
    if minutes:
        return f"{minutes}m {remaining}s" if remaining else f"{minutes}m"
    return f"{remaining}s"


def _format_items(items: dict[str, int], *, empty: str = "Nothing yet") -> str:
    if not items:
        return empty
    lines = []
    for item_key, quantity in items.items():
        item = LOOT_BY_KEY.get(item_key)
        emoji = item.emoji if item is not None else "📦"
        name = item.name if item is not None else item_key.replace("_", " ").title()
        lines.append(f"{emoji} **{name}** ×{quantity}")
    return "\n".join(lines)


def _add_to_haul(haul: dict[str, int], item_keys: list[str]) -> None:
    for item_key in item_keys:
        haul[item_key] = haul.get(item_key, 0) + 1


def _format_coins(amount: int) -> str:
    return f"{amount:,} coin{'s' if amount != 1 else ''}"


async def _require_item(ctx: SlashContext, query: str):
    item = resolve_loot(query)
    if item is None:
        await ctx.send(
            "Unknown item. Use its name or the key shown by `/inventory`.",
            ephemeral=True,
        )
    return item


def setup(handler: CommandHandler) -> None:
    store = handler.resources.economy_store

    @slash_command(name="inventory", description="View collected spaceflight salvage")
    @slash_option(
        name="user",
        description="User whose inventory you want to view",
        required=False,
        opt_type=OptionType.USER,
    )
    async def inventory_command(ctx: SlashContext, user: Optional[Member] = None):
        guild_id = _guild_id(ctx)
        if guild_id is None:
            await ctx.send("Inventories can only be viewed in a server.", ephemeral=True)
            return
        subject = user or ctx.author
        entries = await store.inventory(guild_id, int(subject.id))
        quantities = {entry.item_key: entry.quantity for entry in entries}
        if not quantities:
            await ctx.send(f"📦 {subject.mention}'s inventory is empty.")
            return
        subject_name = getattr(subject, "display_name", None) or getattr(
            subject, "username", "Unknown User"
        )
        inventory_lines = []
        for item_key, quantity in quantities.items():
            item = LOOT_BY_KEY.get(item_key)
            if item is None:
                inventory_lines.append(f"📦 **{item_key}** ×{quantity}")
            else:
                inventory_lines.append(
                    f"{item.emoji} **{item.name}** ×{quantity} — `{item.key}` — "
                    f"auto-sell {_format_coins(item.coin_value)} each"
                )
        await ctx.send(
            f"📦 **{subject_name}'s Spaceflight Inventory**\n"
            + "\n".join(inventory_lines)
        )

    @slash_command(name="transferitem", description="Transfer an inventory item to another user")
    @slash_option(
        name="user", description="Recipient", required=True, opt_type=OptionType.USER
    )
    @slash_option(
        name="item", description="Item name or inventory key", required=True,
        opt_type=OptionType.STRING,
    )
    @slash_option(
        name="quantity", description="Quantity to transfer", required=True,
        opt_type=OptionType.INTEGER,
    )
    async def transfer_item_command(
        ctx: SlashContext, user: Member, item: str, quantity: int
    ):
        guild_id = _guild_id(ctx)
        if guild_id is None:
            await ctx.send("Items can only be transferred in a server.", ephemeral=True)
            return
        loot = await _require_item(ctx, item)
        if loot is None:
            return
        result = await store.transfer_inventory_item(
            guild_id, int(ctx.author.id), int(user.id), loot.key, quantity
        )
        if result.status == "invalid":
            await ctx.send(
                "Choose a positive quantity and a recipient other than yourself.", ephemeral=True
            )
        elif result.status == "insufficient":
            await ctx.send(
                f"You only have **{result.remaining:,}× {loot.name}**.", ephemeral=True
            )
        else:
            await ctx.send(
                f"📦 Transferred **{result.quantity:,}× {loot.name}** to {user.mention}."
            )

    @slash_command(name="sellitem", description="Sell an item at its automated fixed price")
    @slash_option(
        name="item", description="Item name or inventory key", required=True,
        opt_type=OptionType.STRING,
    )
    @slash_option(
        name="quantity", description="Quantity to sell", required=True,
        opt_type=OptionType.INTEGER,
    )
    async def sell_item_command(ctx: SlashContext, item: str, quantity: int):
        guild_id = _guild_id(ctx)
        if guild_id is None:
            await ctx.send("Items can only be sold in a server.", ephemeral=True)
            return
        loot = await _require_item(ctx, item)
        if loot is None:
            return
        result = await store.sell_inventory_item(
            guild_id, int(ctx.author.id), loot.key, quantity, loot.coin_value
        )
        if result.status == "invalid":
            await ctx.send("Choose a positive quantity.", ephemeral=True)
        elif result.status == "insufficient":
            await ctx.send(
                f"You only have **{result.remaining:,}× {loot.name}**.", ephemeral=True
            )
        else:
            await ctx.send(
                f"🏪 Sold **{result.quantity:,}× {loot.name}** for "
                f"**{_format_coins(result.payout)}**. Balance: "
                f"**{_format_coins(result.balance)}**."
            )

    @slash_command(name="buyorder", description="Post an escrowed player-market buy order")
    @slash_option(
        name="item", description="Item name or inventory key", required=True,
        opt_type=OptionType.STRING,
    )
    @slash_option(
        name="quantity", description="Quantity wanted", required=True,
        opt_type=OptionType.INTEGER,
    )
    @slash_option(
        name="price", description="Coins offered per item", required=True,
        opt_type=OptionType.INTEGER,
    )
    async def buy_order_command(ctx: SlashContext, item: str, quantity: int, price: int):
        guild_id = _guild_id(ctx)
        if guild_id is None:
            await ctx.send("Buy orders can only be posted in a server.", ephemeral=True)
            return
        loot = await _require_item(ctx, item)
        if loot is None:
            return
        result = await store.create_buy_order(
            guild_id, int(ctx.author.id), loot.key, quantity, price
        )
        if result.status == "invalid":
            await ctx.send("Quantity and price must be positive.", ephemeral=True)
        elif result.status == "insufficient":
            total = max(0, quantity) * max(0, price)
            await ctx.send(
                f"That order requires **{_format_coins(total)}** in escrow. "
                f"Your balance is **{_format_coins(result.balance)}**.",
                ephemeral=True,
            )
        else:
            total = result.quantity * result.price_each
            await ctx.send(
                f"📈 Buy order **#{result.order_id}** posted for "
                f"**{result.quantity:,}× {loot.name}** at "
                f"**{_format_coins(result.price_each)} each**. "
                f"**{_format_coins(total)}** is held in escrow."
            )

    @slash_command(name="market", description="View open player buy orders")
    @slash_option(
        name="item", description="Optional item name or key", required=False,
        opt_type=OptionType.STRING,
    )
    async def market_command(ctx: SlashContext, item: Optional[str] = None):
        guild_id = _guild_id(ctx)
        if guild_id is None:
            await ctx.send("The market can only be viewed in a server.", ephemeral=True)
            return
        loot = None
        if item is not None:
            loot = await _require_item(ctx, item)
            if loot is None:
                return
        orders = await store.buy_orders(
            guild_id, item_key=None if loot is None else loot.key
        )
        if not orders:
            await ctx.send("📉 There are no matching open buy orders.")
            return
        lines = ["📊 **Player Buy Orders**"]
        for order in orders:
            order_item = LOOT_BY_KEY.get(order.item_key)
            item_name = order_item.name if order_item is not None else order.item_key
            lines.append(
                f"**#{order.order_id}** — {item_name} ×{order.quantity_remaining:,} — "
                f"**{_format_coins(order.price_each)} each** — <@{order.buyer_id}>"
            )
        lines.append("Use `/fillorder` to sell into an order.")
        await ctx.send("\n".join(lines))

    @slash_command(name="fillorder", description="Sell inventory into a player buy order")
    @slash_option(
        name="order_id", description="Market order number", required=True,
        opt_type=OptionType.INTEGER,
    )
    @slash_option(
        name="quantity", description="Quantity to sell", required=True,
        opt_type=OptionType.INTEGER,
    )
    async def fill_order_command(ctx: SlashContext, order_id: int, quantity: int):
        guild_id = _guild_id(ctx)
        if guild_id is None:
            await ctx.send("Orders can only be filled in a server.", ephemeral=True)
            return
        result = await store.fill_buy_order(
            guild_id, int(ctx.author.id), order_id, quantity
        )
        if result.status == "unavailable":
            await ctx.send("That order is no longer available.", ephemeral=True)
        elif result.status == "invalid":
            await ctx.send(
                "Use a positive quantity no larger than the remaining order. "
                "You cannot fill your own order.",
                ephemeral=True,
            )
        elif result.status == "insufficient":
            await ctx.send("You do not have enough of that item.", ephemeral=True)
        else:
            loot = LOOT_BY_KEY.get(result.item_key or "")
            item_name = loot.name if loot is not None else result.item_key
            await ctx.send(
                f"🤝 Filled **{result.quantity:,}× {item_name}** on order "
                f"**#{result.order_id}** for **{_format_coins(result.payout)}**. "
                f"Balance: **{_format_coins(result.seller_balance)}**."
            )

    @slash_command(name="cancelorder", description="Cancel your buy order and refund escrow")
    @slash_option(
        name="order_id", description="Your market order number", required=True,
        opt_type=OptionType.INTEGER,
    )
    async def cancel_order_command(ctx: SlashContext, order_id: int):
        guild_id = _guild_id(ctx)
        if guild_id is None:
            await ctx.send("Orders can only be cancelled in a server.", ephemeral=True)
            return
        result = await store.cancel_buy_order(guild_id, int(ctx.author.id), order_id)
        if result.status == "unavailable":
            await ctx.send("That open order does not belong to you.", ephemeral=True)
        else:
            await ctx.send(
                f"📉 Cancelled order **#{order_id}** and refunded "
                f"**{_format_coins(result.refund)}**. Balance: "
                f"**{_format_coins(result.balance)}**."
            )

    @slash_command(name="dumpster", description="Dive for discarded spaceflight hardware")
    async def dumpster_command(ctx: SlashContext):
        guild_id = _guild_id(ctx)
        if guild_id is None:
            await ctx.send("Dumpster diving can only be done in a server.", ephemeral=True)
            return
        user_id = int(ctx.author.id)
        started = await store.start_activity(guild_id, user_id, "dumpster")
        if not started.started:
            await ctx.send(
                f"🗑️ The dumpsters need time to refill. Try again in "
                f"**{_format_wait(started.retry_after)}**.",
                ephemeral=True,
            )
            return

        game_id = secrets.token_hex(8)
        location_buttons = [
            Button(
                custom_id=f"dumpster_location_{game_id}_{location.key}",
                style=ButtonStyle.PRIMARY,
                label=location.name,
                emoji=location.emoji,
            )
            for location in LOCATIONS
        ]
        location_by_custom_id = {
            button.custom_id: location
            for button, location in zip(location_buttons, LOCATIONS)
        }
        message = await ctx.send(
            f"🗑️ **Spaceflight Dumpster Dive** — {ctx.author.mention}\n"
            "Choose where to search. You have **30 seconds**.\n\n"
            + "\n".join(
                f"{location.emoji} **{location.name}:** {location.description}"
                for location in LOCATIONS
            ),
            components=location_buttons,
        )

        async def player_only(component: Any) -> bool:
            if int(component.ctx.author.id) == user_id:
                return True
            await component.ctx.send("This isn't your dumpster dive.", ephemeral=True)
            return False

        try:
            component = await handler.bot.wait_for_component(
                components=location_buttons,
                check=player_only,
                timeout=LOCATION_TIMEOUT,
            )
        except asyncio.TimeoutError:
            for button in location_buttons:
                button.disabled = True
            await message.edit(
                content="🗑️ You took too long to choose a dumpster. The opportunity passed.",
                components=location_buttons,
            )
            return

        location: DumpsterLocation = location_by_custom_id[component.ctx.custom_id]
        for button in location_buttons:
            button.disabled = True

        rummage_button = Button(
            custom_id=f"dumpster_rummage_{game_id}",
            style=ButtonStyle.GREEN,
            label="Rummage",
            emoji="🔎",
        )
        deep_button = Button(
            custom_id=f"dumpster_deep_{game_id}",
            style=ButtonStyle.PRIMARY,
            label="Dig Deeper",
            emoji="⛏️",
        )
        leave_button = Button(
            custom_id=f"dumpster_leave_{game_id}",
            style=ButtonStyle.RED,
            label="Leave",
            emoji="🏃",
        )
        action_buttons = [rummage_button, deep_button, leave_button]
        haul: dict[str, int] = {}

        await component.ctx.edit_origin(
            content=(
                f"{location.emoji} **{location.name}** — Round **1/{MAX_ROUNDS}**\n"
                "**Rummage** finds one item with lower risk. **Dig Deeper** finds two "
                "with better rare-item odds, but adds an 18% hazard risk.\n\n"
                "Current haul: Nothing yet"
            ),
            components=action_buttons,
        )

        async def bank_haul() -> str:
            if not haul:
                return "Your inventory was unchanged."
            await store.add_inventory_items(
                guild_id,
                user_id,
                haul,
                source="dumpster",
            )
            return "Saved to your inventory:\n" + _format_items(haul)

        for round_number in range(1, MAX_ROUNDS + 1):
            try:
                component = await handler.bot.wait_for_component(
                    components=action_buttons,
                    check=player_only,
                    timeout=ACTION_TIMEOUT,
                )
            except asyncio.TimeoutError:
                for button in action_buttons:
                    button.disabled = True
                saved = await bank_haul()
                await message.edit(
                    content=f"⌛ Time expired, so you left the dumpster.\n\n{saved}",
                    components=action_buttons,
                )
                return

            if component.ctx.custom_id == leave_button.custom_id:
                for button in action_buttons:
                    button.disabled = True
                saved = await bank_haul()
                await component.ctx.edit_origin(
                    content=f"🏃 You left with your haul intact.\n\n{saved}",
                    components=action_buttons,
                )
                return

            deep = component.ctx.custom_id == deep_button.custom_id
            if _random.random() < hazard_chance(location, deep=deep):
                kept, lost = lose_half(haul, rng=_random)
                haul = kept
                for button in action_buttons:
                    button.disabled = True
                saved = await bank_haul()
                await component.ctx.edit_origin(
                    content=(
                        "🚨 **Security patrol!** You escaped, but dropped half your haul.\n"
                        f"Lost:\n{_format_items(lost, empty='Nothing')}\n\n{saved}"
                    ),
                    components=action_buttons,
                )
                return

            found = roll_loot(location, deep=deep, rng=_random)
            _add_to_haul(haul, [item.key for item in found])
            found_quantities: dict[str, int] = {}
            _add_to_haul(found_quantities, [item.key for item in found])
            found_text = _format_items(found_quantities)

            if round_number == MAX_ROUNDS:
                for button in action_buttons:
                    button.disabled = True
                saved = await bank_haul()
                await component.ctx.edit_origin(
                    content=(
                        f"🎉 Final search found:\n{found_text}\n\n"
                        f"You cleared all **{MAX_ROUNDS} rounds**.\n{saved}"
                    ),
                    components=action_buttons,
                )
                return

            await component.ctx.edit_origin(
                content=(
                    f"{location.emoji} **{location.name}** — Round "
                    f"**{round_number + 1}/{MAX_ROUNDS}**\n"
                    f"You found:\n{found_text}\n\n"
                    f"Current unbanked haul:\n{_format_items(haul)}\n\n"
                    "Search again or leave safely?"
                ),
                components=action_buttons,
            )

    handler.register_slash_command(inventory_command)
    handler.register_slash_command(transfer_item_command)
    handler.register_slash_command(sell_item_command)
    handler.register_slash_command(buy_order_command)
    handler.register_slash_command(market_command)
    handler.register_slash_command(fill_order_command)
    handler.register_slash_command(cancel_order_command)
    handler.register_slash_command(dumpster_command)
