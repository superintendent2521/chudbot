"""Code-defined crafting recipes and display metadata.

Add a :class:`CraftingRecipe` to ``RECIPES`` to expose it in ``/craft``.
Recipe and item keys are stable storage identifiers; display names can change
without affecting existing inventories.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CraftingItem:
    key: str
    name: str
    emoji: str


@dataclass(frozen=True)
class RecipeIngredient:
    item_key: str
    quantity: int


@dataclass(frozen=True)
class CraftingRecipe:
    key: str
    output: CraftingItem
    output_quantity: int
    ingredients: tuple[RecipeIngredient, ...]
    description: str = ""

    @property
    def ingredient_quantities(self) -> dict[str, int]:
        return {ingredient.item_key: ingredient.quantity for ingredient in self.ingredients}


RECIPES = (
    CraftingRecipe(
        "insulated_cable",
        CraftingItem("insulated_cable", "Insulated Cable", "🧵"),
        1,
        (RecipeIngredient("scrap_wiring", 3), RecipeIngredient("thermal_blanket", 1)),
        "A dependable cable assembled from recovered insulation and wiring.",
    ),
    CraftingRecipe(
        "pressure_regulator",
        CraftingItem("pressure_regulator", "Pressure Regulator", "⚙️"),
        1,
        (RecipeIngredient("ball_valve", 2), RecipeIngredient("scrap_wiring", 1)),
        "A compact regulator made from reclaimed fluid hardware.",
    ),
    CraftingRecipe(
        "repair_kit",
        CraftingItem("repair_kit", "Orbital Repair Kit", "🛠️"),
        1,
        (
            RecipeIngredient("scrap_wiring", 2),
            RecipeIngredient("thermal_blanket", 2),
            RecipeIngredient("ball_valve", 1),
        ),
        "A general-purpose kit for repairs in unpleasant places.",
    ),
    CraftingRecipe(
        "tracking_array",
        CraftingItem("tracking_array", "Tracking Array", "📡"),
        1,
        (RecipeIngredient("star_tracker", 1), RecipeIngredient("scrap_wiring", 4)),
        "A salvaged optical tracker fitted with a new wiring harness.",
    ),
    CraftingRecipe(
        "eva_patch_kit",
        CraftingItem("eva_patch_kit", "EVA Patch Kit", "🩹"),
        1,
        (RecipeIngredient("thermal_blanket", 3), RecipeIngredient("spacesuit_glove", 1)),
        "Layered thermal material suitable for emergency suit repairs.",
    ),
    CraftingRecipe(
        "engine_controller",
        CraftingItem("engine_controller", "Engine Controller", "🎛️"),
        1,
        (
            RecipeIngredient("apollo_guidance_computer", 1),
            RecipeIngredient("star_tracker", 1),
            RecipeIngredient("scrap_wiring", 5),
        ),
        "Vintage computing repurposed into a questionable engine controller.",
    ),
    CraftingRecipe(
        "capsule_refit_kit",
        CraftingItem("capsule_refit_kit", "Capsule Refit Kit", "🧰"),
        1,
        (
            RecipeIngredient("mission_patch", 2),
            RecipeIngredient("thermal_blanket", 4),
            RecipeIngredient("toolbox", 1),
        ),
        "Everything needed to make an old capsule look almost spaceworthy.",
    ),
    CraftingRecipe(
        "ion_fuel_cell",
        CraftingItem("ion_fuel_cell", "Ion Fuel Cell", "🔋"),
        1,
        (
            RecipeIngredient("pressure_regulator", 1),
            RecipeIngredient("thermal_blanket", 2),
            RecipeIngredient("scrap_wiring", 2),
        ),
        "A volatile fuel cell that gives an asteroid expedition extra range.",
    ),
    CraftingRecipe(
        "hull_patch_plating",
        CraftingItem("hull_patch_plating", "Hull Patch Plating", "🛡️"),
        1,
        (
            RecipeIngredient("eva_patch_kit", 1),
            RecipeIngredient("ball_valve", 1),
            RecipeIngredient("scrap_wiring", 2),
        ),
        "Emergency plating that softens the impact of asteroid hazards.",
    ),
    CraftingRecipe(
        "quantum_scanner",
        CraftingItem("quantum_scanner", "Quantum Scanner", "📡"),
        1,
        (
            RecipeIngredient("tracking_array", 1),
            RecipeIngredient("star_tracker", 1),
            RecipeIngredient("insulated_cable", 2),
        ),
        "A tuned scanner that makes rare salvage easier to identify.",
    ),
)

RECIPES_BY_KEY = {recipe.key: recipe for recipe in RECIPES}
CRAFTED_ITEMS_BY_KEY = {recipe.output.key: recipe.output for recipe in RECIPES}


def _validate_recipes() -> None:
    if len(RECIPES_BY_KEY) != len(RECIPES):
        raise ValueError("Crafting recipe keys must be unique")
    for recipe in RECIPES:
        if not recipe.key or not recipe.output.key or recipe.output_quantity <= 0:
            raise ValueError(f"Invalid crafting recipe: {recipe.key!r}")
        if not recipe.ingredients or any(
            not ingredient.item_key or ingredient.quantity <= 0
            for ingredient in recipe.ingredients
        ):
            raise ValueError(f"Recipe {recipe.key!r} has invalid ingredients")
        ingredient_keys = [ingredient.item_key for ingredient in recipe.ingredients]
        if len(set(ingredient_keys)) != len(ingredient_keys):
            raise ValueError(f"Recipe {recipe.key!r} repeats an ingredient")


_validate_recipes()
