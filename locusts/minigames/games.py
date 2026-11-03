"""The job fair minigames, in the order fairs take turns with them."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from locusts.minigames import hey_day_stacker, locust_walk


@dataclass(frozen=True)
class Game:
    id: str
    name: str
    # The page that plays it; "?fair" on the end plays scored runs.
    page: str
    config: Any
    generate: Callable
    replay: Callable
    # What a scored run's result records besides score and hits.
    stats: Callable[[Any], dict]


GAMES: dict[str, Game] = {g.id: g for g in (
    Game("locust-walk", "Locust Walk", "/walk", locust_walk.DEFAULT_WALK, locust_walk.generate, locust_walk.replay,
         lambda r: {"distance": r.distance_px, "swag": r.swag}),
    Game("hey-day-stacker", "Hey Day Stacker", "/stacker", hey_day_stacker.DEFAULT_STACKER,
         hey_day_stacker.generate, hey_day_stacker.replay, lambda r: {"floors": r.floors, "perfects": r.perfects}),
)}


def next_game(previous: str | None) -> str:
    """The game after `previous` in the rotation, starting over from the first."""
    order = list(GAMES)
    if previous not in GAMES:
        return order[0]
    return order[(order.index(previous) + 1) % len(order)]
