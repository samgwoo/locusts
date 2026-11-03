"""Locust Walk: the pet runs up Locust Walk and switches between lanes to dodge obstacles.

Positions are integer units and time is integer ticks, so a server replay of logged inputs matches the
client exactly. Obstacles sit at fixed distances in rows. The schedule (where rows are, how many lanes
each blocks, and how fast the pet runs) is the same for every seed, and the seed only picks which
obstacles fill a row and which lanes they take. Every course is generated so a perfect run takes no hits.
"""

from bisect import bisect_right
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from itertools import accumulate

from locusts.minigames.rng import Rng

UNITS_PER_PX = 16


class Kind(StrEnum):
    FLYER = "flyer"
    TOUR = "tour"
    BIKE = "bike"
    BUTTON = "button"
    LOVE = "love"
    SWAG = "swag"


LANDMARKS = (Kind.BUTTON, Kind.LOVE)


@dataclass(frozen=True)
class WalkConfig:
    ticks_per_second: int = 60
    seconds: int = 60
    lanes: int = 5
    start_lane: int = 2
    # A lane change can only happen this many ticks after the last one.
    move_cooldown_ticks: int = 6

    # Run speed in units per tick, ramping up by one every speed_ramp_ticks.
    speed_start: int = 32
    speed_max: int = 64
    speed_ramp_ticks: int = 75

    pet_depth_px: int = 16
    stun_ticks: int = 45
    max_hits: int = 3
    swag_px: int = 50

    lead_in_px: int = 400
    row_gap_start_px: int = 240
    row_gap_min_px: int = 120
    row_gap_step_px: int = 2
    blocked_start: int = 1
    blocked_max: int = 3
    blocked_every_rows: int = 6
    tour_from_row: int = 4
    bike_from_row: int = 10
    landmark_every_rows: int = 15
    swag_every_rows: int = 3
    bike_ticks_per_lane: int = 8
    reroll_limit: int = 50

    widths: dict[Kind, int] = field(default_factory=lambda: {
        Kind.FLYER: 1, Kind.TOUR: 2, Kind.BIKE: 1, Kind.BUTTON: 2, Kind.LOVE: 2, Kind.SWAG: 1})
    depths_px: dict[Kind, int] = field(default_factory=lambda: {
        Kind.FLYER: 12, Kind.TOUR: 40, Kind.BIKE: 12, Kind.BUTTON: 32, Kind.LOVE: 32, Kind.SWAG: 8})

    def __post_init__(self):
        # The pet must clear one row before reaching the next, which lets rows be checked one at a time.
        if self.row_gap_min_px < self.pet_depth_px + max(self.depths_px.values()):
            raise ValueError("row_gap_min_px must fit the pet plus the deepest obstacle")
        # Otherwise a fast pet could step over a thin obstacle between ticks.
        if self.speed_max >= (self.pet_depth_px + min(self.depths_px.values())) * UNITS_PER_PX:
            raise ValueError("speed_max is fast enough to skip over obstacles")
        if not 0 <= self.start_lane < self.lanes or self.blocked_max >= self.lanes:
            raise ValueError("start_lane and blocked_max must leave room in the lanes")
        if self.landmark_every_rows <= 0 or self.swag_every_rows <= 0 or self.blocked_every_rows <= 0:
            raise ValueError("row intervals must be positive")

    @property
    def pet_depth(self) -> int:
        return self.pet_depth_px * UNITS_PER_PX

    def speed(self, tick: int) -> int:
        return min(self.speed_max, self.speed_start + tick // self.speed_ramp_ticks)

    def blocked(self, row: int) -> int:
        return min(self.blocked_max, self.blocked_start + row // self.blocked_every_rows)

    def row_gap(self, row: int) -> int:
        return max(self.row_gap_min_px, self.row_gap_start_px - row * self.row_gap_step_px) * UNITS_PER_PX

    def to_dict(self) -> dict:
        """What the client needs to run the same simulation as replay()."""
        return asdict(self) | {"units_per_px": UNITS_PER_PX, "pet_depth": self.pet_depth}


DEFAULT_WALK = WalkConfig()


@dataclass(frozen=True)
class Obstacle:
    kind: Kind
    lane: int
    y: int
    depth: int
    width: int = 1
    # Bikes enter `lane` at tick t0 and move one lane in `direction` every bike_ticks_per_lane ticks.
    t0: int = 0
    direction: int = 0


@dataclass(frozen=True)
class Course:
    seed: int
    ticks: int
    obstacles: tuple[Obstacle, ...]

    def to_dict(self) -> dict:
        return {"seed": self.seed, "ticks": self.ticks, "obstacles": [
            {"kind": str(o.kind), "lane": o.lane, "y": o.y, "depth": o.depth, "width": o.width,
             "t0": o.t0, "direction": o.direction} for o in self.obstacles]}


@dataclass(frozen=True)
class Result:
    score: int
    distance_px: int
    hits: int
    swag: int
    ticks_played: int


def lanes_at(ob: Obstacle, tick: int, config: WalkConfig = DEFAULT_WALK) -> range:
    if ob.kind is Kind.BIKE:
        step = (tick - ob.t0) // config.bike_ticks_per_lane
        if not 0 <= step < config.lanes:
            return range(0)
        lane = ob.lane + ob.direction * step
        return range(lane, lane + 1)
    return range(ob.lane, ob.lane + ob.width)


def collides(ob: Obstacle, tick: int, rear: int, lane: int, config: WalkConfig = DEFAULT_WALK) -> bool:
    """Whether a pet whose back edge is at `rear` overlaps the obstacle at this tick."""
    return rear < ob.y + ob.depth and rear + config.pet_depth > ob.y and lane in lanes_at(ob, tick, config)


def perfect_rears(ticks: int, config: WalkConfig = DEFAULT_WALK) -> list[int]:
    """The pet's back edge after each tick of a run with no hits."""
    return list(accumulate(config.speed(t) for t in range(ticks)))


def generate(seed: int, seconds: int | None = None, config: WalkConfig = DEFAULT_WALK) -> Course:
    ticks = (seconds or config.seconds) * config.ticks_per_second
    rears = perfect_rears(ticks, config)
    end = rears[-1] + config.pet_depth
    rng = Rng(seed)

    def arrival(y: int) -> int:
        return bisect_right(rears, y - config.pet_depth)

    obstacles: list[Obstacle] = []
    # The lowest cooldown a perfect run can have in each lane it can reach.
    reachable = {config.start_lane: 0}
    row, y = 0, config.lead_in_px * UNITS_PER_PX
    tick = arrival(y)
    while y < end:
        next_y = y + config.row_gap(row)
        next_tick = arrival(next_y)
        for attempt in range(config.reroll_limit + 1):
            candidate = _row(rng, row, y, tick, config) if attempt < config.reroll_limit else []
            survivors = _propagate(reachable, tick, next_tick, candidate, rears, config)
            if survivors:
                break
        obstacles += candidate
        reachable, row, y, tick = survivors, row + 1, next_y, next_tick
    return Course(seed, ticks, tuple(sorted(obstacles, key=lambda o: o.y)))


def _row(rng: Rng, row: int, y: int, tick: int, config: WalkConfig) -> list[Obstacle]:
    def make(kind: Kind, lane: int, **bike) -> Obstacle:
        return Obstacle(kind, lane, y, config.depths_px[kind] * UNITS_PER_PX, config.widths[kind], **bike)

    free = list(range(config.lanes))
    out: list[Obstacle] = []
    if row and row % config.landmark_every_rows == 0:
        kind = LANDMARKS[(row // config.landmark_every_rows - 1) % len(LANDMARKS)]
        lane = rng.below(config.lanes - config.widths[kind] + 1)
        out.append(make(kind, lane))
        free = [l for l in free if l not in range(lane, lane + config.widths[kind])]
    else:
        budget = config.blocked(row)
        while budget:
            pairs = [l for l in free if l + 1 in free]
            kinds = [Kind.FLYER]
            if row >= config.tour_from_row and budget >= 2 and pairs:
                kinds.append(Kind.TOUR)
            if row >= config.bike_from_row and not any(o.kind is Kind.BIKE for o in out):
                kinds.append(Kind.BIKE)
            kind = rng.choice(kinds)
            if kind is Kind.BIKE:
                direction = rng.choice((1, -1))
                lanes_crossed_on_arrival = rng.below(config.lanes)
                t0 = tick - lanes_crossed_on_arrival * config.bike_ticks_per_lane - config.bike_ticks_per_lane // 2
                out.append(make(kind, 0 if direction == 1 else config.lanes - 1, t0=t0, direction=direction))
                budget -= 1
            else:
                lane = rng.choice(pairs if kind is Kind.TOUR else free)
                out.append(make(kind, lane))
                free = [l for l in free if l not in range(lane, lane + config.widths[kind])]
                budget -= config.widths[kind]
    if row % config.swag_every_rows == 0 and free:
        out.append(make(Kind.SWAG, rng.choice(free)))
    return out


def _propagate(reachable: dict[int, int], start: int, stop: int, row: list[Obstacle],
               rears: list[int], config: WalkConfig) -> dict[int, int]:
    """Step a perfect run's reachable lanes through ticks [start, stop), dodging `row`."""
    solid = [o for o in row if o.kind is not Kind.SWAG]
    for tick in range(start, stop):
        blocked = {lane for o in solid for lane in range(config.lanes) if collides(o, tick, rears[tick], lane, config)}
        nxt: dict[int, int] = {}
        for lane, wait in reachable.items():
            options = [(lane, max(0, wait - 1))]
            if wait == 0:
                options += [(l, config.move_cooldown_ticks - 1) for l in (lane - 1, lane + 1) if 0 <= l < config.lanes]
            for l, w in options:
                if l not in blocked and w < nxt.get(l, config.move_cooldown_ticks):
                    nxt[l] = w
        if not nxt:
            return {}
        reachable = nxt
    return reachable


def parse_inputs(inputs, ticks: int) -> dict[int, int]:
    """Lane changes logged by the client as [tick, -1 or 1] pairs in increasing tick order."""
    moves: dict[int, int] = {}
    last = -1
    for entry in inputs:
        if not (isinstance(entry, (list, tuple)) and len(entry) == 2):
            raise ValueError("each input must be a [tick, direction] pair")
        tick, direction = entry
        if type(tick) is not int or type(direction) is not int:
            raise ValueError("tick and direction must be integers")
        if direction not in (-1, 1):
            raise ValueError("direction must be -1 or 1")
        if not last < tick < ticks:
            raise ValueError("ticks must increase and fall inside the run")
        moves[tick] = direction
        last = tick
    return moves


def replay(course: Course, inputs, config: WalkConfig = DEFAULT_WALK) -> Result:
    """Score a run. Lane changes during a cooldown, off the edge, or while stunned are ignored, as on the client."""
    moves = parse_inputs(inputs, course.ticks)
    obstacles = course.obstacles
    lane, rear, hits, swag = config.start_lane, 0, 0, 0
    next_move = stunned_until = behind = 0
    gone: set[int] = set()
    played = course.ticks
    for tick in range(course.ticks):
        if tick < stunned_until:
            continue
        direction = moves.get(tick)
        if direction and tick >= next_move and 0 <= lane + direction < config.lanes:
            lane += direction
            next_move = tick + config.move_cooldown_ticks
        rear += config.speed(tick)
        while behind < len(obstacles) and obstacles[behind].y + obstacles[behind].depth <= rear:
            behind += 1
        for i in range(behind, len(obstacles)):
            ob = obstacles[i]
            if ob.y >= rear + config.pet_depth:
                break
            if i in gone or not collides(ob, tick, rear, lane, config):
                continue
            gone.add(i)
            if ob.kind is Kind.SWAG:
                swag += 1
                continue
            hits += 1
            stunned_until = tick + 1 + config.stun_ticks
            break
        if hits >= config.max_hits:
            played = tick + 1
            break
    distance_px = rear // UNITS_PER_PX
    return Result(distance_px + swag * config.swag_px, distance_px, hits, swag, played)
