"""Spaceflight loot and rules for the interactive dumpster game."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from chudbot.economy.crafting import CRAFTED_ITEMS_BY_KEY


@dataclass(frozen=True)
class DumpsterLoot:
    key: str
    name: str
    emoji: str
    rarity: int
    coin_value: int


@dataclass(frozen=True)
class DumpsterLocation:
    key: str
    name: str
    description: str
    emoji: str
    hazard_chance: float
    weights: tuple[float, ...]


@dataclass(frozen=True)
class DumpsterEquipment:
    item_key: str
    description: str
    hazard_reduction: float = 0.0
    rarity_bonus: float = 0.0
    extra_rounds: int = 0
    extra_fuel: int = 0
    unlocks_special_location: bool = False


LOOT = (
    DumpsterLoot("scrap_wiring", "Scrap Wiring Bundle", "🔌", 1, 5),
    DumpsterLoot("thermal_blanket", "Thermal Blanket Scrap", "🟨", 1, 8),
    DumpsterLoot("ball_valve", "Ball Valve", "🔩", 2, 25),
    DumpsterLoot("mission_patch", "Mission Patch", "🪡", 2, 30),
    DumpsterLoot("star_tracker", "Star Tracker", "✨", 3, 80),
    DumpsterLoot("spacesuit_glove", "Spacesuit Glove", "🧤", 3, 100),
    DumpsterLoot("flashlight", "High-Power Flashlight", "🔦", 2, 40),
    DumpsterLoot("toolbox", "Salvage Toolbox", "🧰", 3, 120),
    DumpsterLoot("access_card", "Restricted Access Card", "💳", 4, 400),
    DumpsterLoot("apollo_guidance_computer", "Apollo Guidance Computer", "🖥️", 4, 350),
    DumpsterLoot("saturn_v_f1", "Saturn V F-1 Engine", "🔥", 4, 500),
    DumpsterLoot("S36_COPV", "Ship 36 COPV", "🔥", 4, 450),
    DumpsterLoot("gemini_capsule", "Gemini Capsule", "🛰️", 5, 1_200),
    DumpsterLoot("artemis_ii_rs25", "Artemis II RS-25", "🚀", 5, 1_500),
)

LOOT_BY_KEY = {item.key: item for item in LOOT}

LOCATIONS = (
    DumpsterLocation(
        "contractor",
        "Orbital Salvage Ring",
        "Balanced salvage with fewer security patrols.",
        "🏭",
        0.06,
        (30, 28, 20, 14, 7, 6, 10, 4, 0.5, 2, 1.5, 1, 0.35, 0.2),
    ),
    DumpsterLocation(
        "test_stand",
        "Ion Engine Graveyard",
        "More propulsion hardware, but it is still warm.",
        "🔥",
        0.12,
        (24, 22, 27, 7, 7, 4, 6, 8, 0.8, 1.5, 4, 2, 0.2, 0.8),
    ),
    DumpsterLocation(
        "museum",
        "Lunar Cargo Quarantine",
        "Better odds for historic hardware and angry guards.",
        "🏛️",
        0.14,
        (20, 20, 12, 25, 8, 8, 5, 3, 2, 5, 2, 0.8, 1.2, 0.15),
    ),
)

LOCATIONS_BY_KEY = {location.key: location for location in LOCATIONS}

SPECIAL_LOCATION = DumpsterLocation(
    "vehicle_assembly_building",
    "Derelict Assembly Spire",
    "Restricted high-value salvage with heavy security.",
    "🏗️",
    0.22,
    (12, 12, 15, 10, 10, 8, 4, 6, 2, 8, 7, 5, 2.5, 2),
)

EQUIPMENT = (
    DumpsterEquipment(
        "spacesuit_glove",
        "Reduces every hazard chance by 10 percentage points.",
        hazard_reduction=0.10,
    ),
    DumpsterEquipment(
        "flashlight",
        "Improves the rarity weighting of every item roll.",
        rarity_bonus=0.45,
    ),
    DumpsterEquipment(
        "toolbox",
        "Adds one extra search round.",
        extra_rounds=1,
    ),
    DumpsterEquipment(
        "access_card",
        "Unlocks the restricted Derelict Assembly Spire.",
        unlocks_special_location=True,
    ),
    DumpsterEquipment(
        "ion_fuel_cell",
        "Adds three fuel to the expedition.",
        extra_fuel=3,
    ),
    DumpsterEquipment(
        "hull_patch_plating",
        "Reduces asteroid hazard chance by 8 percentage points.",
        hazard_reduction=0.08,
    ),
    DumpsterEquipment(
        "quantum_scanner",
        "Improves the rarity weighting of every item roll.",
        rarity_bonus=0.35,
    ),
)

EQUIPMENT_BY_KEY = {equipment.item_key: equipment for equipment in EQUIPMENT}


def resolve_loot(query: str) -> DumpsterLoot | None:
    """Resolve an item by stable key or case-insensitive display name."""
    normalized = query.strip().casefold().replace(" ", "_")
    for item in LOOT:
        if item.key.casefold() == normalized or item.name.casefold() == query.strip().casefold():
            return item
    return None


def resolve_equipment(query: str) -> DumpsterEquipment | None:
    item = resolve_loot(query)
    if item is None:
        normalized = query.strip().casefold().replace(" ", "_")
        item = CRAFTED_ITEMS_BY_KEY.get(normalized)
    return None if item is None else EQUIPMENT_BY_KEY.get(item.key)


def roll_loot(
    location: DumpsterLocation,
    *,
    deep: bool,
    rng: Any,
    rarity_bonus: float = 0.0,
) -> tuple[DumpsterLoot, ...]:
    """Roll one normal item or two rarity-boosted deep-search items."""
    rarity_boost = (1.55 if deep else 1.0) + max(0.0, rarity_bonus)
    weights = tuple(
        weight * rarity_boost ** (item.rarity - 1)
        for item, weight in zip(LOOT, location.weights)
    )
    count = 2 if deep else 1
    return tuple(rng.choices(LOOT, weights=weights, k=count))


def hazard_chance(
    location: DumpsterLocation, *, deep: bool, hazard_reduction: float = 0.0
) -> float:
    return min(
        0.95,
        max(
            0.0,
            location.hazard_chance
            + (0.18 if deep else 0.0)
            - max(0.0, hazard_reduction),
        ),
    )


def locations_for_equipment(equipment: DumpsterEquipment | None) -> tuple[DumpsterLocation, ...]:
    if equipment is not None and equipment.unlocks_special_location:
        return (*LOCATIONS, SPECIAL_LOCATION)
    return LOCATIONS


def lose_half(
    haul: dict[str, int], *, rng: Any
) -> tuple[dict[str, int], dict[str, int]]:
    """Randomly discard half the individual items, rounded up."""
    units = [key for key, quantity in haul.items() for _ in range(quantity)]
    rng.shuffle(units)
    loss_count = (len(units) + 1) // 2
    lost_units = units[:loss_count]
    kept_units = units[loss_count:]
    kept: dict[str, int] = {}
    lost: dict[str, int] = {}
    for key in kept_units:
        kept[key] = kept.get(key, 0) + 1
    for key in lost_units:
        lost[key] = lost.get(key, 0) + 1
    return kept, lost
