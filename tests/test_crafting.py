import unittest

from chudbot.economy.crafting import CRAFTED_ITEMS_BY_KEY, RECIPES, RECIPES_BY_KEY
from chudbot.games.spaceflight_dumpster import LOOT_BY_KEY


class CraftingRecipeTests(unittest.TestCase):
    def test_recipe_registry_uses_unique_stable_keys(self) -> None:
        self.assertEqual(len(RECIPES), len(RECIPES_BY_KEY))
        self.assertEqual(len(RECIPES), len(CRAFTED_ITEMS_BY_KEY))

    def test_every_recipe_has_valid_quantities_and_known_ingredients(self) -> None:
        known_items = set(LOOT_BY_KEY) | set(CRAFTED_ITEMS_BY_KEY)
        for recipe in RECIPES:
            self.assertGreater(recipe.output_quantity, 0)
            self.assertTrue(recipe.ingredients)
            for ingredient in recipe.ingredients:
                self.assertIn(ingredient.item_key, known_items)
                self.assertGreater(ingredient.quantity, 0)

    def test_enough_recipes_exist_to_exercise_pagination(self) -> None:
        self.assertGreater(len(RECIPES), 5)


if __name__ == "__main__":
    unittest.main()
