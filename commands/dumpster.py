"""Interactive spaceflight dumpster diving and persistent inventory commands."""

from __future__ import annotations

import asyncio
import random
import secrets
from typing import Any, Optional

from interactions import (
    AutocompleteContext,
    Button,
    ButtonStyle,
    Member,
    OptionType,
    SlashContext,
    slash_command,
    slash_option,
)

from command_handler import CommandHandler
from economy_store import BUY_ORDER_TTL_SECONDS, MAX_OPEN_BUY_ORDERS
from spaceflight_dumpster import (
    EQUIPMENT_BY_KEY,
    LOCATIONS,
    LOOT_BY_KEY,
    DumpsterLocation,
    hazard_chance,
    locations_for_equipment,
    lose_half,
    resolve_equipment,
    resolve_loot,
    roll_loot,
)


MAX_ROUNDS = 3
LOCATION_TIMEOUT = 15
ACTION_TIMEOUT = 20
EQUIPMENT_MIN_USES = 5
EQUIPMENT_MAX_USES = 10
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


def _format_items(
    items: dict[str, int], *, empty: str = "Nothing yet", show_rarity: bool = False
) -> str:
    if not items:
        return empty
    lines = []
    for item_key, quantity in items.items():
        item = LOOT_BY_KEY.get(item_key)
        emoji = item.emoji if item is not None else "📦"
        name = item.name if item is not None else item_key.replace("_", " ").title()
        rarity = (
            f" — {_rarity_name(item.rarity)} {'★' * item.rarity}"
            if show_rarity and item is not None
            else ""
        )
        lines.append(f"{emoji} **{name}** ×{quantity}{rarity}")
    return "\n".join(lines)


def _add_to_haul(haul: dict[str, int], item_keys: list[str]) -> None:
    for item_key in item_keys:
        haul[item_key] = haul.get(item_key, 0) + 1


def _format_coins(amount: int) -> str:
    return f"{amount:,} coin{'s' if amount != 1 else ''}"


def _rarity_name(rarity: int) -> str:
    return {
        1: "Common",
        2: "Uncommon",
        3: "Rare",
        4: "Epic",
        5: "Legendary",
    }.get(rarity, "Unknown")


async def _send_item_autocomplete(ctx: AutocompleteContext) -> None:
    search = (ctx.input_text or "").strip().casefold()
    matches = [
        item
        for item in LOOT_BY_KEY.values()
        if not search
        or search in item.name.casefold()
        or search in item.key.casefold()
    ][:25]
    await ctx.send(
        choices=[
            {"name": f"{item.emoji} {item.name}", "value": item.name}
            for item in matches
        ]
    )


async def _send_equipment_autocomplete(ctx: AutocompleteContext) -> None:
    search = (ctx.input_text or "").strip().casefold()
    choices = []
    for equipment in EQUIPMENT_BY_KEY.values():
        item = LOOT_BY_KEY[equipment.item_key]
        if search and search not in item.name.casefold():
            continue
        choices.append({"name": f"{item.emoji} {item.name}", "value": item.name})
    await ctx.send(choices=choices[:25])


async def _require_item(ctx: SlashContext, query: str):
    item = resolve_loot(query)
    if item is None:
        await ctx.send(
            "Unknown item. Use the item name shown by `/inventory`.",
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
        if int(subject.id) != int(ctx.author.id) and await store.inventory_is_private(
            guild_id, int(subject.id)
        ):
            await ctx.send("🔒 That inventory is private.", ephemeral=True)
            return
        entries, active_equipment = await asyncio.gather(
            store.inventory(guild_id, int(subject.id)),
            store.equipment_uses(guild_id, int(subject.id)),
        )
        quantities = {entry.item_key: entry.quantity for entry in entries}
        if not quantities and not active_equipment:
            await ctx.send(f"📦 {subject.mention}'s inventory is empty.")
            return
        subject_name = getattr(subject, "display_name", None) or getattr(
            subject, "username", "Unknown User"
        )
        inventory_lines = []
        total_value = 0
        sorted_inventory = sorted(
            quantities.items(),
            key=lambda entry: (
                -(LOOT_BY_KEY[entry[0]].rarity if entry[0] in LOOT_BY_KEY else 0),
                entry[0],
            ),
        )
        current_rarity = None
        for item_key, quantity in sorted_inventory:
            item = LOOT_BY_KEY.get(item_key)
            if item is None:
                inventory_lines.append(f"📦 **Unknown Salvage** ×{quantity}")
            else:
                if item.rarity != current_rarity:
                    current_rarity = item.rarity
                    inventory_lines.append(
                        f"\n**{_rarity_name(item.rarity)} {'★' * item.rarity}**"
                    )
                total_value += item.coin_value * quantity
                inventory_lines.append(
                    f"{item.emoji} **{item.name}** ×{quantity} — "
                    f"auto-sell {_format_coins(item.coin_value)} each"
                )
        if active_equipment:
            inventory_lines.append("\n**Active Dumpster Equipment**")
            for entry in active_equipment:
                item = LOOT_BY_KEY.get(entry.item_key)
                name = "Unknown Equipment" if item is None else item.name
                emoji = "🧰" if item is None else item.emoji
                inventory_lines.append(
                    f"{emoji} **{name}** — {entry.quantity} dumpster uses left"
                )
        await ctx.send(
            f"📦 **{subject_name}'s Spaceflight Inventory**\n"
            + "\n".join(inventory_lines)
            + f"\n\nTotal automated value: **{_format_coins(total_value)}**"
        )

    @slash_command(name="inventoryprivacy", description="Make your inventory public or private")
    @slash_option(
        name="private", description="Hide inventory from other users", required=True,
        opt_type=OptionType.BOOLEAN,
    )
    async def inventory_privacy_command(ctx: SlashContext, private: bool):
        guild_id = _guild_id(ctx)
        if guild_id is None:
            await ctx.send("Inventory privacy only applies in a server.", ephemeral=True)
            return
        await store.set_inventory_private(guild_id, int(ctx.author.id), private)
        visibility = "private" if private else "public"
        await ctx.send(f"🔒 Your inventory is now **{visibility}**.", ephemeral=True)

    @slash_command(name="items", description="Browse spaceflight salvage and fixed prices")
    async def items_command(ctx: SlashContext):
        lines = ["📚 **Spaceflight Salvage Catalogue**"]
        for item in sorted(LOOT_BY_KEY.values(), key=lambda value: (value.rarity, value.name)):
            equipment = EQUIPMENT_BY_KEY.get(item.key)
            equipment_text = (
                f" — consumable: {equipment.description}" if equipment is not None else ""
            )
            lines.append(
                f"{item.emoji} **{item.name}** — {_rarity_name(item.rarity)} "
                f"{'★' * item.rarity} — {_format_coins(item.coin_value)}{equipment_text}"
            )
        lines.append("All listed items can currently appear at each dumpster location.")
        await ctx.send("\n".join(lines))

    @slash_command(name="transferitem", description="Transfer an inventory item to another user")
    @slash_option(
        name="user", description="Recipient", required=True, opt_type=OptionType.USER
    )
    @slash_option(
        name="item", description="Item name", required=True,
        opt_type=OptionType.STRING, autocomplete=True,
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
        name="item", description="Item name", required=True,
        opt_type=OptionType.STRING, autocomplete=True,
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
        confirmation = None
        confirmation_buttons = []
        if loot.rarity >= 4 and quantity > 0:
            confirmation_id = secrets.token_hex(8)
            confirm_button = Button(
                custom_id=f"sell_confirm_{confirmation_id}",
                style=ButtonStyle.RED,
                label="Confirm Sale",
            )
            cancel_button = Button(
                custom_id=f"sell_cancel_{confirmation_id}",
                style=ButtonStyle.PRIMARY,
                label="Keep Item",
            )
            confirmation_buttons = [confirm_button, cancel_button]
            confirmation_message = await ctx.send(
                f"⚠️ **{_rarity_name(loot.rarity)} item confirmation**\n"
                f"Sell **{quantity:,}× {loot.name}** for "
                f"**{_format_coins(quantity * loot.coin_value)}**?",
                components=confirmation_buttons,
                ephemeral=True,
            )

            async def sale_owner_only(component: Any) -> bool:
                return int(component.ctx.author.id) == int(ctx.author.id)

            try:
                confirmation = await handler.bot.wait_for_component(
                    components=confirmation_buttons,
                    check=sale_owner_only,
                    timeout=30,
                )
            except asyncio.TimeoutError:
                for button in confirmation_buttons:
                    button.disabled = True
                await confirmation_message.edit(
                    content="Sale confirmation expired. Your item was not sold.",
                    components=confirmation_buttons,
                )
                return
            for button in confirmation_buttons:
                button.disabled = True
            if confirmation.ctx.custom_id == cancel_button.custom_id:
                await confirmation.ctx.edit_origin(
                    content="Sale cancelled. Your item remains in your inventory.",
                    components=confirmation_buttons,
                )
                return
        result = await store.sell_inventory_item(
            guild_id, int(ctx.author.id), loot.key, quantity, loot.coin_value
        )
        if result.status == "invalid":
            response = "Choose a positive quantity."
        elif result.status == "insufficient":
            response = f"You only have **{result.remaining:,}× {loot.name}**."
        else:
            response = (
                f"🏪 Sold **{result.quantity:,}× {loot.name}** for "
                f"**{_format_coins(result.payout)}**. Balance: "
                f"**{_format_coins(result.balance)}**."
            )
        if confirmation is None:
            await ctx.send(response, ephemeral=result.status != "sold")
        else:
            await confirmation.ctx.edit_origin(
                content=response,
                components=confirmation_buttons,
            )

    @slash_command(name="buyorder", description="Post an escrowed player-market buy order")
    @slash_option(
        name="item", description="Item name", required=True,
        opt_type=OptionType.STRING, autocomplete=True,
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
        elif result.status == "limit":
            await ctx.send(
                f"You already have the maximum of **{MAX_OPEN_BUY_ORDERS} "
                "open buy orders**.",
                ephemeral=True,
            )
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
                f"**{_format_coins(total)}** is held in escrow for "
                f"**{BUY_ORDER_TTL_SECONDS // 86_400} days**."
            )

    @slash_command(name="market", description="View open player buy orders")
    @slash_option(
        name="item", description="Optional item name", required=False,
        opt_type=OptionType.STRING, autocomplete=True,
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
                f"**{_format_coins(order.price_each)} each** — buyer ID `{order.buyer_id}` — "
                f"expires <t:{order.expires_at}:R>"
            )
        lines.append("Use `/fillorder` to sell into an order.")
        await ctx.send("\n".join(lines))

    @slash_command(name="myorders", description="View your open market buy orders")
    async def my_orders_command(ctx: SlashContext):
        guild_id = _guild_id(ctx)
        if guild_id is None:
            await ctx.send("Orders can only be viewed in a server.", ephemeral=True)
            return
        orders = await store.buy_orders(
            guild_id,
            buyer_id=int(ctx.author.id),
            limit=20,
        )
        if not orders:
            await ctx.send("📉 You have no open buy orders.", ephemeral=True)
            return
        lines = ["📋 **Your Open Buy Orders**"]
        total_escrow = 0
        for order in orders:
            loot = LOOT_BY_KEY.get(order.item_key)
            item_name = loot.name if loot is not None else order.item_key
            escrow = order.quantity_remaining * order.price_each
            total_escrow += escrow
            lines.append(
                f"**#{order.order_id}** — {item_name} ×{order.quantity_remaining:,} — "
                f"{_format_coins(order.price_each)} each — "
                f"{_format_coins(escrow)} escrowed — <t:{order.expires_at}:R>"
            )
        lines.append(f"Total escrow: **{_format_coins(total_escrow)}**")
        await ctx.send("\n".join(lines), ephemeral=True)

    @slash_command(name="pricehistory", description="View recent player-market sale prices")
    @slash_option(
        name="item", description="Item name", required=True,
        opt_type=OptionType.STRING, autocomplete=True,
    )
    async def price_history_command(ctx: SlashContext, item: str):
        guild_id = _guild_id(ctx)
        if guild_id is None:
            await ctx.send("Price history can only be viewed in a server.", ephemeral=True)
            return
        loot = await _require_item(ctx, item)
        if loot is None:
            return
        sales = await store.market_sales(guild_id, loot.key, limit=10)
        open_orders = await store.buy_orders(guild_id, item_key=loot.key, limit=1)
        highest_bid = open_orders[0].price_each if open_orders else None
        if not sales:
            bid_text = (
                f"\nHighest open bid: **{_format_coins(highest_bid)}**."
                if highest_bid is not None
                else ""
            )
            await ctx.send(
                f"📊 No player-market sales have completed for **{loot.name}** yet.\n"
                f"Automated value: **{_format_coins(loot.coin_value)}**.{bid_text}"
            )
            return
        total_quantity = sum(sale.quantity for sale in sales)
        weighted_total = sum(sale.quantity * sale.price_each for sale in sales)
        average = weighted_total // total_quantity
        lines = [
            f"📊 **{loot.name} Price History**",
            f"Recent weighted average: **{_format_coins(average)} each**",
            f"Automated value: **{_format_coins(loot.coin_value)} each**",
            (
                f"Highest open bid: **{_format_coins(highest_bid)} each**"
                if highest_bid is not None
                else "Highest open bid: **None**"
            ),
        ]
        for sale in sales:
            lines.append(
                f"Order **#{sale.order_id}** — {sale.quantity:,} sold at "
                f"**{_format_coins(sale.price_each)} each** — <t:{sale.sold_at}:R>"
            )
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
    @slash_option(
        name="equipment", description="Optional consumable equipment", required=False,
        opt_type=OptionType.STRING, autocomplete=True,
    )
    async def dumpster_command(ctx: SlashContext, equipment: Optional[str] = None):
        guild_id = _guild_id(ctx)
        if guild_id is None:
            await ctx.send("Dumpster diving can only be done in a server.", ephemeral=True)
            return
        user_id = int(ctx.author.id)
        equipment_rule = None
        equipment_item = None
        if equipment is not None:
            equipment_rule = resolve_equipment(equipment)
            if equipment_rule is None:
                await ctx.send(
                    "Unknown equipment. Choose one of the suggested consumables.",
                    ephemeral=True,
                )
                return
            equipment_item = LOOT_BY_KEY[equipment_rule.item_key]
            availability = await store.equipment_availability(
                guild_id, user_id, equipment_rule.item_key
            )
            if availability.inventory_quantity < 1 and availability.uses_remaining < 1:
                await ctx.send(
                    f"You do not have a **{equipment_item.name}** to use.",
                    ephemeral=True,
                )
                return
        started = await store.start_activity(guild_id, user_id, "dumpster")
        if not started.started:
            await ctx.send(
                f"🗑️ The dumpsters need time to refill. Try again in "
                f"**{_format_wait(started.retry_after)}**.",
                ephemeral=True,
            )
            return

        game_id = secrets.token_hex(8)
        run_locations = locations_for_equipment(equipment_rule)
        location_buttons = [
            Button(
                custom_id=f"dumpster_location_{game_id}_{location.key}",
                style=ButtonStyle.PRIMARY,
                label=location.name,
                emoji=location.emoji,
            )
            for location in run_locations
        ]
        location_by_custom_id = {
            button.custom_id: location
            for button, location in zip(location_buttons, run_locations)
        }
        message = await ctx.send(
            f"🗑️ **Spaceflight Dumpster Dive** — {ctx.author.mention}\n"
            f"Choose where to search. You have **{LOCATION_TIMEOUT} seconds**.\n\n"
            + "\n".join(
                f"{location.emoji} **{location.name}:** {location.description}"
                for location in run_locations
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
        equipment_use = None
        if equipment_rule is not None:
            equipment_use = await store.use_inventory_equipment(
                guild_id,
                user_id,
                equipment_rule.item_key,
                _random.randint(EQUIPMENT_MIN_USES, EQUIPMENT_MAX_USES),
                source="dumpster_equipment",
            )
            if equipment_use.status != "used":
                await component.ctx.edit_origin(
                    content=(
                        f"Your **{equipment_item.name}** was no longer available. "
                        "The dive was cancelled."
                    ),
                    components=location_buttons,
                )
                return

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
        max_rounds = MAX_ROUNDS + (
            0 if equipment_rule is None else equipment_rule.extra_rounds
        )
        equipment_text = (
            "No equipment"
            if equipment_rule is None
            else (
                f"{equipment_item.emoji} {equipment_item.name}: "
                f"{equipment_rule.description} "
                f"(**{equipment_use.uses_remaining} uses remain**)"
            )
        )

        await component.ctx.edit_origin(
            content=(
                f"{location.emoji} **{location.name}** — Round **1/{max_rounds}**\n"
                f"Equipment: **{equipment_text}**\n"
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

        for round_number in range(1, max_rounds + 1):
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
            if _random.random() < hazard_chance(
                location,
                deep=deep,
                hazard_reduction=(
                    0.0 if equipment_rule is None else equipment_rule.hazard_reduction
                ),
            ):
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

            found = roll_loot(
                location,
                deep=deep,
                rng=_random,
                rarity_bonus=(
                    0.0 if equipment_rule is None else equipment_rule.rarity_bonus
                ),
            )
            _add_to_haul(haul, [item.key for item in found])
            found_quantities: dict[str, int] = {}
            _add_to_haul(found_quantities, [item.key for item in found])
            found_text = _format_items(found_quantities, show_rarity=True)
            discovery = (
                "\n🌟 **LEGENDARY SALVAGE!** The whole yard goes quiet."
                if any(item.rarity == 5 for item in found)
                else ""
            )

            if round_number == max_rounds:
                for button in action_buttons:
                    button.disabled = True
                saved = await bank_haul()
                await component.ctx.edit_origin(
                    content=(
                        f"🎉 Final search found:\n{found_text}{discovery}\n\n"
                        f"You cleared all **{max_rounds} rounds**.\n{saved}"
                    ),
                    components=action_buttons,
                )
                return

            await component.ctx.edit_origin(
                content=(
                    f"{location.emoji} **{location.name}** — Round "
                    f"**{round_number + 1}/{max_rounds}**\n"
                    f"You found:\n{found_text}{discovery}\n\n"
                    f"Current unbanked haul:\n{_format_items(haul)}\n\n"
                    "Search again or leave safely?"
                ),
                components=action_buttons,
            )

    @transfer_item_command.autocomplete("item")
    async def transfer_item_autocomplete(ctx: AutocompleteContext):
        await _send_item_autocomplete(ctx)

    @sell_item_command.autocomplete("item")
    async def sell_item_autocomplete(ctx: AutocompleteContext):
        await _send_item_autocomplete(ctx)

    @buy_order_command.autocomplete("item")
    async def buy_order_autocomplete(ctx: AutocompleteContext):
        await _send_item_autocomplete(ctx)

    @market_command.autocomplete("item")
    async def market_autocomplete(ctx: AutocompleteContext):
        await _send_item_autocomplete(ctx)

    @price_history_command.autocomplete("item")
    async def price_history_autocomplete(ctx: AutocompleteContext):
        await _send_item_autocomplete(ctx)

    @dumpster_command.autocomplete("equipment")
    async def dumpster_equipment_autocomplete(ctx: AutocompleteContext):
        await _send_equipment_autocomplete(ctx)

    handler.register_slash_command(inventory_command)
    handler.register_slash_command(inventory_privacy_command)
    handler.register_slash_command(items_command)
    handler.register_slash_command(transfer_item_command)
    handler.register_slash_command(sell_item_command)
    handler.register_slash_command(buy_order_command)
    handler.register_slash_command(market_command)
    handler.register_slash_command(my_orders_command)
    handler.register_slash_command(price_history_command)
    handler.register_slash_command(fill_order_command)
    handler.register_slash_command(cancel_order_command)
    handler.register_slash_command(dumpster_command)
