"""Hey Day Stacker: blocks slide back and forth above a tower and the player drops each one onto it.

Whatever hangs past the block below is trimmed off, so the tower narrows with every sloppy drop. A drop
within perfect_px of the block below snaps into place, and enough perfect drops in a row widen the tower
again. Missing the tower, or leaving a sliver thinner than min_px, costs a hit. The score is the tower's
height plus a bonus for every perfect drop.

Positions are integer units and time is integer ticks, so a server replay of the logged drops matches the
client exactly. The speed schedule is the same for every seed, which only picks the side each block
slides in from.
"""

from dataclasses import asdict, dataclass

from locusts.minigames.rng import Rng

UNITS_PER_PX = 16


@dataclass(frozen=True)
class StackerConfig:
    ticks_per_second: int = 60
    seconds: int = 60
    max_hits: int = 3

    # Over three blocks wide, so a block can miss the starting tower completely.
    field_px: int = 144
    # The tower's starting width, which perfect drops can regrow it to but never past.
    block_px: int = 40
    min_px: int = 2
    block_height_px: int = 8

    # Slide speed in units per tick, rising by speed_step with every floor.
    speed_start: int = 24
    speed_step: int = 1
    speed_max: int = 64
    # Ticks between one drop and the next block appearing.
    spawn_delay_ticks: int = 20

    perfect_px: int = 2
    grow_every: int = 3
    grow_px: int = 4
    floor_points: int = 10
    perfect_points: int = 5

    def __post_init__(self):
        if not 0 < self.min_px <= self.block_px < self.field_px:
            raise ValueError("blocks must be at least min_px wide and narrower than the field")
        # Otherwise one tick could carry a block past both edges.
        if self.speed_max > self.field - self.block:
            raise ValueError("speed_max must be less than the room a block has to slide")
        if self.spawn_delay_ticks < 1 or self.grow_every < 1:
            raise ValueError("spawn_delay_ticks and grow_every must be positive")

    @property
    def field(self) -> int:
        return self.field_px * UNITS_PER_PX

    @property
    def block(self) -> int:
        return self.block_px * UNITS_PER_PX

    def speed(self, floors: int) -> int:
        return min(self.speed_max, self.speed_start + floors * self.speed_step)

    def to_dict(self) -> dict:
        """What the client needs to run the same simulation as replay()."""
        return asdict(self) | {"units_per_px": UNITS_PER_PX}


DEFAULT_STACKER = StackerConfig()


@dataclass(frozen=True)
class Course:
    seed: int
    ticks: int
    # 0 if the block slides in from the left edge, 1 if from the right, for every block that could appear.
    sides: tuple[int, ...]

    def to_dict(self) -> dict:
        return {"seed": self.seed, "ticks": self.ticks, "sides": list(self.sides)}


@dataclass(frozen=True)
class Result:
    score: int
    floors: int
    perfects: int
    hits: int
    ticks_played: int


@dataclass(frozen=True)
class Drop:
    kind: str  # "perfect", "trim", or "miss"
    x: int
    width: int


@dataclass
class Run:
    course: Course
    config: StackerConfig
    top_x: int
    top_w: int
    tick: int = 0
    floors: int = 0
    perfects: int = 0
    streak: int = 0
    hits: int = 0
    spawned: int = 0
    # The sliding block's left edge, or None between a drop and the next block.
    x: int | None = None
    direction: int = 0
    next_spawn: int = 0
    over: bool = False


def generate(seed: int, seconds: int | None = None, config: StackerConfig = DEFAULT_STACKER) -> Course:
    ticks = (seconds or config.seconds) * config.ticks_per_second
    # A block appears at least spawn_delay_ticks + 1 ticks after the one before it.
    count = ticks // (config.spawn_delay_ticks + 1) + 1
    rng = Rng(seed)
    return Course(seed, ticks, tuple(rng.below(2) for _ in range(count)))


def new_run(course: Course, config: StackerConfig = DEFAULT_STACKER) -> Run:
    return Run(course, config, top_x=(config.field - config.block) // 2, top_w=config.block)


def step(run: Run, drop: bool) -> Drop | None:
    """Advance one tick. A block can't be dropped on the tick it appears."""
    c = run.config
    placed = None
    if run.x is None:
        if run.tick >= run.next_spawn:
            side = run.course.sides[run.spawned]
            run.spawned += 1
            run.x = 0 if side == 0 else c.field - run.top_w
            run.direction = 1 if side == 0 else -1
    elif drop:
        placed = _place(run)
        run.next_spawn = run.tick + c.spawn_delay_ticks
    else:
        _slide(run)
    run.tick += 1
    if run.hits >= c.max_hits or run.tick >= run.course.ticks:
        run.over = True
    return placed


def _slide(run: Run) -> None:
    room = run.config.field - run.top_w
    x = run.x + run.direction * run.config.speed(run.floors)
    if x < 0:
        x, run.direction = -x, 1
    elif x > room:
        x, run.direction = 2 * room - x, -1
    run.x = x


def _place(run: Run) -> Drop:
    c = run.config
    x, w = run.x, run.top_w
    left, right = max(x, run.top_x), min(x + w, run.top_x + w)
    run.x = None
    if right - left < c.min_px * UNITS_PER_PX:
        run.hits += 1
        run.streak = 0
        return Drop("miss", x, w)
    run.floors += 1
    if abs(x - run.top_x) <= c.perfect_px * UNITS_PER_PX:
        run.perfects += 1
        run.streak += 1
        if run.streak % c.grow_every == 0:
            grow = min(c.grow_px * UNITS_PER_PX, c.block - w)
            run.top_w = w + grow
            run.top_x = min(max(0, run.top_x - grow // 2), c.field - run.top_w)
        return Drop("perfect", x, w)
    run.streak = 0
    run.top_x, run.top_w = left, right - left
    return Drop("trim", x, w)


def result(run: Run) -> Result:
    c = run.config
    score = run.floors * c.floor_points + run.perfects * c.perfect_points
    return Result(score, run.floors, run.perfects, run.hits, run.tick)


def parse_inputs(inputs, ticks: int) -> set[int]:
    """Drops logged by the client as tick numbers in increasing order."""
    if not isinstance(inputs, (list, tuple)):
        raise ValueError("inputs must be a list of ticks")
    drops: set[int] = set()
    last = -1
    for tick in inputs:
        if type(tick) is not int:
            raise ValueError("each input must be a tick number")
        if not last < tick < ticks:
            raise ValueError("ticks must increase and fall inside the run")
        drops.add(tick)
        last = tick
    return drops


def replay(course: Course, inputs, config: StackerConfig = DEFAULT_STACKER) -> Result:
    """Score a run. Drops between blocks, or on the tick a block appears, are ignored, as on the client."""
    drops = parse_inputs(inputs, course.ticks)
    run = new_run(course, config)
    while not run.over:
        step(run, run.tick in drops)
    return result(run)
