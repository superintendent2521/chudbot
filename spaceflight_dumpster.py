"""Spaceflight loot and rules for the interactive dumpster game."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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


LOOT = (
    DumpsterLoot("scrap_wiring", "Scrap Wiring Bundle", "🔌", 1, 5),
    DumpsterLoot("thermal_blanket", "Thermal Blanket Scrap", "🟨", 1, 8),
    DumpsterLoot("ball_valve", "Ball Valve", "🔩", 2, 25),
    DumpsterLoot("mission_patch", "Mission Patch", "🪡", 2, 30),
    DumpsterLoot("star_tracker", "Star Tracker", "✨", 3, 80),
    DumpsterLoot("spacesuit_glove", "Spacesuit Glove", "🧤", 3, 100),
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
        "Contractor Scrap Yard",
        "Balanced salvage with fewer security patrols.",
        "🏭",
        0.06,
        (30, 28, 20, 14, 7, 6, 2, 1.5, 1, 0.35, 0.2),
    ),
    DumpsterLocation(
        "test_stand",
        "Test Stand Dumpster",
        "More propulsion hardware, but it is still warm.",
        "🔥",
        0.12,
        (24, 22, 27, 7, 7, 4, 1.5, 4, 2, 0.2, 0.8),
    ),
    DumpsterLocation(
        "museum",
        "Museum Loading Dock",
        "Better odds for historic hardware and angry guards.",
        "🏛️",
        0.14,
        (20, 20, 12, 25, 8, 8, 5, 2, 0.8, 1.2, 0.15),
    ),
)

LOCATIONS_BY_KEY = {location.key: location for location in LOCATIONS}


def resolve_loot(query: str) -> DumpsterLoot | None:
    """Resolve an item by stable key or case-insensitive display name."""
    normalized = query.strip().casefold().replace(" ", "_")
    for item in LOOT:
        if item.key.casefold() == normalized or item.name.casefold() == query.strip().casefold():
            return item
    return None


def roll_loot(location: DumpsterLocation, *, deep: bool, rng: Any) -> tuple[DumpsterLoot, ...]:
    """Roll one normal item or two rarity-boosted deep-search items."""
    rarity_boost = 1.55 if deep else 1.0
    weights = tuple(
        weight * rarity_boost ** (item.rarity - 1)
        for item, weight in zip(LOOT, location.weights)
    )
    count = 2 if deep else 1
    return tuple(rng.choices(LOOT, weights=weights, k=count))


def hazard_chance(location: DumpsterLocation, *, deep: bool) -> float:
    return min(0.95, location.hazard_chance + (0.18 if deep else 0.0))


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
