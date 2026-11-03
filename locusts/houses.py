"""College houses, loaded from locusts/houses.toml.

A pet's house is its home on the map, adds its `effects` to the pet's perks (see locusts/perks.py), and
groups pets for the house leaderboard.
"""

import tomllib
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

from locusts.perks import effect_errors
from locusts.shop import ITEM_EFFECTS

HOUSES_FILE = Path(__file__).parent / "houses.toml"


@dataclass(frozen=True)
class House:
    id: str
    name: str
    map_name: str
    blurb: str
    effects: dict = field(default_factory=dict)


class HouseError(ValueError):
    pass


_FIELDS = {"id", "name", "map_name", "blurb", "effects"}


def load_houses(path: Path = HOUSES_FILE) -> dict[str, House]:
    houses: dict[str, House] = {}
    for raw in tomllib.loads(path.read_text()).get("house", []):
        unknown = set(raw) - _FIELDS
        if unknown:
            raise HouseError(f"house {raw.get('id')} has unknown fields {sorted(unknown)}")
        if raw["id"] in houses:
            raise HouseError(f"duplicate house {raw['id']}")
        house = House(**raw)
        not_allowed = set(house.effects) - ITEM_EFFECTS
        if not_allowed:
            raise HouseError(f"house {house.id}: houses can't have {sorted(not_allowed)}")
        errors = effect_errors(house.effects, {})
        if errors:
            raise HouseError(f"house {house.id}: {'; '.join(errors)}")
        houses[house.id] = house
    if not houses:
        raise HouseError("no houses")
    return houses


@cache
def houses() -> dict[str, House]:
    return load_houses()
