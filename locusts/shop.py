"""Things a pet can buy with money, loaded from locusts/items.toml.

Owned items and running subscriptions add their `effects` to the pet's perks, the same way career
tiers do (see locusts/perks.py). Only perks that make sense for anyone are allowed on items.
"""

import tomllib
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

from locusts.perks import effect_errors

ITEMS_FILE = Path(__file__).parent / "items.toml"

ITEM_EFFECTS = {"training_xp", "task_pay", "work_energy_saving", "sleep_regen", "rest_bonus", "travel_speed",
                "home_meals", "meal_discount"}
# Where on the pet a wearable item goes, in the order they're drawn: a coat over a shirt, headphones over a cap.
WEAR_SLOTS = ("shirt", "outer", "head", "ears", "eyes")


@dataclass(frozen=True)
class Item:
    id: str
    name: str
    category: str
    price: float
    blurb: str
    effects: dict = field(default_factory=dict)
    weekly: bool = False
    swag: float = 0.0
    replaces: str | None = None
    swipes: int = 0
    wear: str | None = None


@dataclass(frozen=True)
class Catalog:
    # Category id -> display name, in shop order.
    categories: dict[str, str]
    items: dict[str, Item]


class ShopError(ValueError):
    pass


_FIELDS = {"id", "name", "category", "price", "blurb", "effects", "weekly", "swag", "replaces", "swipes", "wear"}


def load_catalog(path: Path = ITEMS_FILE) -> Catalog:
    data = tomllib.loads(path.read_text())
    categories = data.get("categories", {})
    items: dict[str, Item] = {}
    for raw in data.get("item", []):
        unknown = set(raw) - _FIELDS
        if unknown:
            raise ShopError(f"item {raw.get('id')} has unknown fields {sorted(unknown)}")
        if raw["id"] in items:
            raise ShopError(f"duplicate item {raw['id']}")
        items[raw["id"]] = Item(**raw)
    _validate(Catalog(categories, items))
    return Catalog(categories, items)


def _validate(catalog: Catalog) -> None:
    for item in catalog.items.values():
        where = f"item {item.id}"
        if item.category not in catalog.categories:
            raise ShopError(f"{where}: unknown category {item.category}")
        if isinstance(item.price, bool) or not isinstance(item.price, int | float) or item.price <= 0:
            raise ShopError(f"{where}: price must be a positive number")
        not_allowed = set(item.effects) - ITEM_EFFECTS
        if not_allowed:
            raise ShopError(f"{where}: items can't have {sorted(not_allowed)}")
        errors = effect_errors(item.effects, {})
        if errors:
            raise ShopError(f"{where}: {'; '.join(errors)}")
        if item.weekly and (item.swag or item.replaces):
            raise ShopError(f"{where}: subscriptions can't have swag or replace items")
        if not item.weekly and item.swipes:
            raise ShopError(f"{where}: only subscriptions come with swipes")
        if not item.weekly and not 0 <= item.swag < item.price:
            raise ShopError(f"{where}: swag must be less than the price, so buying isn't free swag")
        if item.wear is not None and (item.weekly or item.wear not in WEAR_SLOTS):
            raise ShopError(f"{where}: wear must be one of {', '.join(WEAR_SLOTS)}, on something owned for good")
        if item.replaces is not None:
            old = catalog.items.get(item.replaces)
            if old is None or old.weekly:
                raise ShopError(f"{where}: replaces unknown item {item.replaces}")
            if any(other.replaces == item.replaces for other in catalog.items.values() if other is not item):
                raise ShopError(f"{where}: {item.replaces} already has an upgrade")


@cache
def catalog() -> Catalog:
    return load_catalog()


def upgrade_of(item_id: str) -> Item | None:
    """The item that replaces `item_id`, if any."""
    return next((i for i in catalog().items.values() if i.replaces == item_id), None)


def owned_effects(items: list[str], subscriptions: dict[str, dict]) -> list[dict]:
    """The effects tables of everything a pet owns or subscribes to, skipping ids no longer sold."""
    known = catalog().items
    return [known[i].effects for i in [*items, *subscriptions] if i in known]


def swag(items: list[str]) -> float:
    known = catalog().items
    return sum(known[i].swag for i in items if i in known)


def wearing(items: list[str]) -> list[str]:
    """What a pet with `items` wears, in drawing order: in each slot, the one with the most swag."""
    known = catalog().items
    worn = []
    for slot in WEAR_SLOTS:
        options = [known[i] for i in items if i in known and known[i].wear == slot]
        if options:
            worn.append(max(options, key=lambda item: item.swag).id)
    return worn


def value(items: list[str]) -> float:
    """What a pet paid for the things it owns for good: each item's price, and an upgrade's includes the item
    traded in for it. Subscriptions, food, and coffee are spent, not owned."""
    known = catalog().items

    def paid(item_id: str) -> float:
        item = known[item_id]
        return item.price + (paid(item.replaces) if item.replaces in known else 0)

    return sum(paid(i) for i in items if i in known and not known[i].weekly)
