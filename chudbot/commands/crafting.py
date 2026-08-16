"""Interactive inventory crafting command."""

from __future__ import annotations

import asyncio
import math
import secrets
from typing import Any, Optional, cast

from interactions import (
    ActionRow,
    Button,
    ButtonStyle,
    SlashContext,
    StringSelectMenu,
    StringSelectOption,
    slash_command,
)
from interactions.models import Embed

from chudbot.command_handler import CommandHandler
from chudbot.economy.crafting import CRAFTED_ITEMS_BY_KEY, RECIPES, RECIPES_BY_KEY
from chudbot.economy.responses import defer_ping, send_ping
from chudbot.games.spaceflight_dumpster import LOOT_BY_KEY


RECIPES_PER_PAGE = 5
CRAFT_TIMEOUT = 60


def _guild_id(ctx: SlashContext) -> Optional[int]:
    raw_id = getattr(ctx, "guild_id", None)
    if raw_id is None:
        raw_id = getattr(getattr(ctx, "guild", None), "id", None)
    return None if raw_id is None else int(raw_id)


def _item_display(item_key: str) -> tuple[str, str]:
    loot = LOOT_BY_KEY.get(item_key)
    if loot is not None:
        return loot.name, loot.emoji
    crafted = CRAFTED_ITEMS_BY_KEY.get(item_key)
    if crafted is not None:
        return crafted.name, crafted.emoji
    return item_key.replace("_", " ").title(), "📦"


def _recipe_requirements(recipe: Any, quantities: dict[str, int]) -> str:
    lines = []
    for ingredient in recipe.ingredients:
        name, emoji = _item_display(ingredient.item_key)
        owned = quantities.get(ingredient.item_key, 0)
        marker = "✅" if owned >= ingredient.quantity else "❌"
        lines.append(
            f"{marker} {emoji} {name} — **{owned:,}/{ingredient.quantity:,}**"
        )
    return "\n".join(lines)


def _craft_embed(page: int, quantities: dict[str, int]) -> Embed:
    page_count = max(1, math.ceil(len(RECIPES) / RECIPES_PER_PAGE))
    recipes = RECIPES[page * RECIPES_PER_PAGE : (page + 1) * RECIPES_PER_PAGE]
    embed = Embed(
        title="🛠️ Crafting Bench",
        description="Choose a recipe below. Ingredients are consumed immediately.",
        color=0xD18B32,
    )
    for recipe in recipes:
        output = (
            f" ×{recipe.output_quantity:,}" if recipe.output_quantity != 1 else ""
        )
        description = f"{recipe.description}\n" if recipe.description else ""
        embed.add_field(
            name=f"{recipe.output.emoji} {recipe.output.name}{output}",
            value=description + _recipe_requirements(recipe, quantities),
            inline=False,
        )
    embed.set_footer(text=f"Page {page + 1}/{page_count} • Session expires after inactivity")
    return embed


def _craft_components(page: int, session_id: str) -> list[Any]:
    page_count = max(1, math.ceil(len(RECIPES) / RECIPES_PER_PAGE))
    recipes = RECIPES[page * RECIPES_PER_PAGE : (page + 1) * RECIPES_PER_PAGE]
    options = [
        StringSelectOption(
            label=recipe.output.name,
            value=recipe.key,
            description=f"Craft {recipe.output_quantity:,} from {len(recipe.ingredients)} ingredient types",
            emoji=recipe.output.emoji,
        )
        for recipe in recipes
    ]
    menu = StringSelectMenu(
        *options,
        custom_id=f"craft_select_{session_id}",
        placeholder="Choose something to craft…",
        min_values=1,
        max_values=1,
    )
    previous = Button(
        custom_id=f"craft_previous_{session_id}",
        style=ButtonStyle.SECONDARY,
        label="Previous",
        emoji="◀️",
        disabled=page == 0,
    )
    following = Button(
        custom_id=f"craft_next_{session_id}",
        style=ButtonStyle.SECONDARY,
        label="Next",
        emoji="▶️",
        disabled=page >= page_count - 1,
    )
    # A select menu consumes an entire Discord action row. Keep navigation on
    # its own row so the library cannot auto-pack incompatible components.
    return [ActionRow(menu), ActionRow(previous, following)]


def setup(handler: CommandHandler) -> None:
    store = handler.resources.economy_store

    @slash_command(name="craft", description="Craft inventory items from salvage")
    async def craft_command(ctx: SlashContext):
        await defer_ping(ctx)
        guild_id = _guild_id(ctx)
        if guild_id is None:
            await send_ping(ctx, "Crafting can only be used in a server.", ephemeral=True)
            return

        user_id = int(ctx.author.id)
        session_id = secrets.token_hex(8)
        page = 0
        entries = await store.inventory(guild_id, user_id)
        quantities = {entry.item_key: entry.quantity for entry in entries}
        embed = _craft_embed(page, quantities)
        components = _craft_components(page, session_id)
        message = await send_ping(
            ctx,
            "Use the menu to choose a recipe.",
            embed=embed,
            components=components,
        )

        async def crafter_only(component: Any) -> bool:
            if int(component.ctx.author.id) == user_id:
                return True
            await send_ping(component.ctx, "This isn't your crafting bench.", ephemeral=True)
            return False

        while True:
            try:
                component = await handler.bot.wait_for_component(
                    components=cast(Any, components),
                    check=crafter_only,
                    timeout=CRAFT_TIMEOUT,
                )
            except asyncio.TimeoutError:
                for row in components:
                    for interactive in row.components:
                        interactive.disabled = True
                embed.set_footer(text="Crafting session expired • Run /craft to reopen it")
                await message.edit(
                    content="This crafting session has expired.",
                    embed=embed,
                    components=components,
                )
                return

            await defer_ping(component.ctx, edit_origin=True)
            custom_id = component.ctx.custom_id
            status = "Use the menu to choose a recipe."
            if custom_id.startswith("craft_previous_"):
                page = max(0, page - 1)
            elif custom_id.startswith("craft_next_"):
                page = min(
                    math.ceil(len(RECIPES) / RECIPES_PER_PAGE) - 1,
                    page + 1,
                )
            else:
                selected = getattr(component.ctx, "values", ())
                recipe = RECIPES_BY_KEY.get(selected[0] if selected else "")
                if recipe is None:
                    status = "That recipe is no longer available."
                else:
                    result = await store.craft_inventory_item(
                        guild_id,
                        user_id,
                        recipe.output.key,
                        recipe.output_quantity,
                        recipe.ingredient_quantities,
                        recipe_key=recipe.key,
                    )
                    if result.status == "crafted":
                        status = (
                            f"✅ Crafted **{result.output_quantity:,}× "
                            f"{recipe.output.name}**. You now have **{result.output_total:,}**."
                        )
                    elif result.status == "insufficient":
                        missing = ", ".join(
                            f"{_item_display(entry.item_key)[1]} "
                            f"{_item_display(entry.item_key)[0]} ×{entry.quantity:,}"
                            for entry in result.missing
                        )
                        status = f"❌ Missing: **{missing}**."
                    else:
                        status = "That recipe is invalid and could not be crafted."

            entries = await store.inventory(guild_id, user_id)
            quantities = {entry.item_key: entry.quantity for entry in entries}
            embed = _craft_embed(page, quantities)
            components = _craft_components(page, session_id)
            await component.ctx.edit_origin(
                content=f"{ctx.author.mention} {status}",
                embed=embed,
                components=components,
            )

    handler.register_slash_command(craft_command)
