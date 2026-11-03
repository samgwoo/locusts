"""Events that come up while a pet works, loaded from locusts/events.toml.

After every Config.event_hours of work (the gap drawn fresh each time) the pet simulation offers one of the
events that fit what the pet is doing. It waits Config.event_window_minutes for the player: answering
applies the chosen option's effects, and an unanswered event just goes away. Which event comes up, and when,
comes from the pet's id and how many it has seen, so it's the same however often the pet is looked at.
"""

import random
import tomllib
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from locusts.config import DEFAULT_CONFIG, MAX_STAT, Config

EVENTS_FILE = Path(__file__).parent / "events.toml"
EFFECTS = {"xp", "money", "energy", "fullness", "gpa"}
WHEN = {"training", "task", "work"}


class EventError(ValueError):
    pass


@dataclass(frozen=True)
class Option:
    label: str
    effects: dict[str, float]


@dataclass(frozen=True)
class Template:
    id: str
    title: str
    text: str
    when: str
    options: tuple[Option, ...]
    careers: tuple[str, ...] = ()
    buildings: tuple[str, ...] = ()

    def fits(self, activity: str, career: str | None, building: str | None) -> bool:
        return ((self.when == "work" or self.when == activity)
                and (not self.careers or career in self.careers)
                and (not self.buildings or building in self.buildings))


_FIELDS = {"id", "title", "text", "when", "options", "careers", "buildings"}


def load_events(path: Path = EVENTS_FILE, config: Config = DEFAULT_CONFIG) -> dict[str, Template]:
    events: dict[str, Template] = {}
    for raw in tomllib.loads(path.read_text()).get("event", []):
        where = f"event {raw.get('id')}"
        unknown = set(raw) - _FIELDS
        if unknown:
            raise EventError(f"{where} has unknown fields {sorted(unknown)}")
        if raw["id"] in events:
            raise EventError(f"duplicate {where}")
        options = tuple(Option(o["label"], dict(o["effects"])) for o in raw["options"])
        event = Template(raw["id"], raw["title"], raw["text"], raw["when"], options,
                         tuple(raw.get("careers", ())), tuple(raw.get("buildings", ())))
        _validate(event, where, config)
        events[event.id] = event
    return events


def _validate(event: Template, where: str, config: Config) -> None:
    if event.when not in WHEN:
        raise EventError(f"{where}: when must be one of {sorted(WHEN)}")
    unknown_buildings = set(event.buildings) - set(config.buildings)
    if unknown_buildings:
        raise EventError(f"{where}: unknown buildings {sorted(unknown_buildings)}")
    if len(event.options) not in (1, 2):
        raise EventError(f"{where}: needs one option (a free chance) or two (a tradeoff)")
    for option in event.options:
        unknown = set(option.effects) - EFFECTS
        if unknown:
            raise EventError(f"{where}: options can't have {sorted(unknown)}")
        if "xp" in option.effects and event.when != "training":
            raise EventError(f"{where}: only training events can give or take xp")
        gains = [v for v in option.effects.values() if v > 0]
        losses = [v for v in option.effects.values() if v < 0]
        if len(event.options) == 1 and (losses or not gains):
            raise EventError(f"{where}: a free chance is all upside")
        if len(event.options) == 2 and not (gains and losses):
            raise EventError(f"{where}: each side of a tradeoff gains one thing and gives up another")


@cache
def templates() -> dict[str, Template]:
    return load_events()


def _rng(pet, purpose: str) -> random.Random:
    return random.Random(f"locusts-event:{pet.id}:{pet.events_seen}:{purpose}")


def gap_hours(pet, config: Config = DEFAULT_CONFIG) -> float:
    """Hours of work until the pet's next event."""
    return _rng(pet, "gap").uniform(*config.event_hours)


def offer(pet, t: float, config: Config = DEFAULT_CONFIG) -> dict | None:
    """An event for what the pet is doing at `t`, as stored on the pet while it waits, or None if none fit."""
    activity = pet.activity.value
    fitting = sorted(e.id for e in templates().values() if e.fits(activity, pet.career, pet.building))
    if not fitting:
        return None
    return {"id": _rng(pet, "pick").choice(fitting), "at": t, "expires": t + config.event_window_minutes * 60,
            "path": pet.path, "building": pet.building}


def apply(pet, pending: dict, option: int, perks, config: Config = DEFAULT_CONFIG) -> dict:
    """Apply option `option` of the pending event to the pet, returning the effects as they landed."""
    from locusts.pet import max_energy   # pet imports this module

    event = templates()[pending["id"]]
    landed = {}
    for key, value in event.options[option].effects.items():
        if key == "gpa" and value < 0:
            value *= 1 - perks.gpa_loss_saving
        if key == "xp":
            path = pending.get("path")
            if not (pet.career and path):
                continue
            value *= config.xp_per_hour
            bank = pet.xp.setdefault(pet.career, {})
            bank[path] = max(0.0, bank.get(path, 0.0) + value)
        elif key == "money":
            pet.money += value
        elif key == "energy":
            pet.energy = max(0.0, min(max_energy(pet, config), pet.energy + value))
        elif key == "fullness":
            pet.fullness = max(0.0, min(MAX_STAT, pet.fullness + value))
        elif key == "gpa":
            pet.gpa = max(0.0, min(config.gpa_max, pet.gpa + value))
        landed[key] = round(value, 3)
    return landed


def describe(pending: dict | None, now: float, perks, config: Config = DEFAULT_CONFIG) -> dict | None:
    """A pending event as the player sees it: what happened, the options with their effects (in the units
    the HUD shows: XP, not hours), and how long is left to answer."""
    if pending is None or pending["expires"] <= now:
        return None
    event = templates()[pending["id"]]

    def shown(effects: dict) -> dict:
        out = {}
        for key, value in effects.items():
            if key == "xp":
                value *= config.xp_per_hour
            if key == "gpa" and value < 0:
                value *= 1 - perks.gpa_loss_saving
            out[key] = round(value, 3)
        return out

    return {
        "id": event.id, "title": event.title, "text": event.text, "building": pending.get("building"),
        "choice": len(event.options) == 2,
        "options": [{"label": o.label, "effects": shown(o.effects)} for o in event.options],
        "expires_at": pending["expires"],
    }
