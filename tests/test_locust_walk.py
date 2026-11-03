from collections import Counter

import pytest

from locusts.minigames.locust_walk import (
    DEFAULT_WALK,
    UNITS_PER_PX,
    Course,
    Kind,
    Obstacle,
    WalkConfig,
    collides,
    generate,
    lanes_at,
    perfect_rears,
    replay,
)

PX = UNITS_PER_PX
SEEDS = range(150)
# The default course is never close to impassable, so this one forces the generator to reject rows.
HARD = WalkConfig(blocked_max=4, move_cooldown_ticks=12)


def solve(course: Course, config: WalkConfig = DEFAULT_WALK) -> list[list[int]] | None:
    """Search for inputs that finish the course without a hit, independently of the generator."""
    rears = perfect_rears(course.ticks, config)
    solid = [o for o in course.obstacles if o.kind is not Kind.SWAG]
    # Per tick: lane -> (lowest cooldown, previous lane, direction moved).
    frontier = {config.start_lane: (0, None, 0)}
    history = []
    nearby = 0
    for tick in range(course.ticks):
        while nearby < len(solid) and solid[nearby].y + solid[nearby].depth <= rears[tick]:
            nearby += 1
        close = [o for o in solid[nearby:nearby + 12]]
        nxt = {}
        for lane, (wait, _, _) in frontier.items():
            options = [(lane, max(0, wait - 1), 0)]
            if wait == 0:
                options += [(lane + d, config.move_cooldown_ticks - 1, d) for d in (-1, 1)
                            if 0 <= lane + d < config.lanes]
            for new_lane, new_wait, d in options:
                if any(collides(o, tick, rears[tick], new_lane, config) for o in close):
                    continue
                if new_lane not in nxt or new_wait < nxt[new_lane][0]:
                    nxt[new_lane] = (new_wait, lane, d)
        if not nxt:
            return None
        history.append(nxt)
        frontier = nxt
    inputs = []
    lane = next(iter(frontier))
    for tick in range(course.ticks - 1, -1, -1):
        _, previous, d = history[tick][lane]
        if d:
            inputs.append([tick, d])
        lane = previous
    return inputs[::-1]


def rows(course: Course, config: WalkConfig = DEFAULT_WALK) -> list[tuple[int, int]]:
    """Each row's distance and how many lanes it blocks, which the schedule fixes for every seed."""
    blocked = Counter()
    for o in course.obstacles:
        blocked[o.y] += 0 if o.kind is Kind.SWAG else o.width
    return sorted(blocked.items())


def single(kind: Kind, lane: int, y_px: int, config: WalkConfig = DEFAULT_WALK, ticks: int = 600, **bike) -> Course:
    ob = Obstacle(kind, lane, y_px * PX, config.depths_px[kind] * PX, config.widths[kind], **bike)
    return Course(0, ticks, (ob,))


def test_same_seed_same_course():
    assert generate(7) == generate(7)
    assert generate(7) != generate(8)


def test_schedule_is_the_same_for_every_seed():
    expected = rows(generate(0))
    assert len(expected) > 50
    for seed in range(1, 40):
        assert rows(generate(seed)) == expected


def test_courses_use_every_kind():
    kinds = {o.kind for seed in range(10) for o in generate(seed).obstacles}
    assert kinds == set(Kind)


@pytest.mark.parametrize("seed", SEEDS)
def test_every_course_has_a_perfect_run(seed):
    course = generate(seed)
    inputs = solve(course)
    assert inputs is not None
    result = replay(course, inputs)
    assert result.hits == 0
    assert result.distance_px == perfect_rears(course.ticks)[-1] // PX


@pytest.mark.parametrize("seed", range(40))
def test_hard_courses_reject_rows_until_passable(seed):
    course = generate(seed, config=HARD)
    assert rows(course, HARD) == rows(generate(0, config=HARD), HARD)
    inputs = solve(course, HARD)
    assert inputs is not None
    assert replay(course, inputs, HARD).hits == 0


def test_solver_reports_a_wall():
    wall = tuple(Obstacle(Kind.FLYER, lane, 100 * PX, 12 * PX) for lane in range(DEFAULT_WALK.lanes))
    assert solve(Course(0, 600, wall)) is None


@pytest.mark.parametrize("seed", range(20))
def test_longer_runs_from_ocaml_trauma_are_passable_and_go_further(seed):
    short, long = generate(seed), generate(seed, seconds=66)
    assert long.ticks == 66 * 60
    inputs = solve(long)
    assert inputs is not None
    assert replay(long, inputs).hits == 0
    assert replay(long, inputs).distance_px > replay(short, solve(short)).distance_px


def test_standing_still_eventually_ends_the_run():
    result = replay(generate(3), [])
    assert result.hits == DEFAULT_WALK.max_hits
    assert result.ticks_played < generate(3).ticks


def test_a_hit_stuns_the_pet():
    clear = replay(Course(0, 600, ()), [])
    hit = replay(single(Kind.FLYER, 2, 100), [])
    assert hit.hits == 1
    lost = clear.distance_px - hit.distance_px
    assert DEFAULT_WALK.stun_ticks * DEFAULT_WALK.speed_start // PX <= lost <= DEFAULT_WALK.stun_ticks * 5


def test_dodging_avoids_the_hit():
    assert replay(single(Kind.FLYER, 2, 100), [[10, 1]]).hits == 0


def test_tour_groups_block_two_lanes():
    course = single(Kind.TOUR, 2, 100)
    assert replay(course, [[10, 1]]).hits == 1
    assert replay(course, [[10, -1]]).hits == 0


def test_swag_adds_to_the_score():
    course = single(Kind.SWAG, 2, 100)
    result = replay(course, [])
    assert result.swag == 1 and result.hits == 0
    assert result.score == result.distance_px + DEFAULT_WALK.swag_px


def test_bikes_cross_the_lanes():
    bike = Obstacle(Kind.BIKE, 0, 0, 12 * PX, t0=100, direction=1)
    assert list(lanes_at(bike, 99)) == []
    assert list(lanes_at(bike, 100)) == [0]
    assert list(lanes_at(bike, 100 + 2 * DEFAULT_WALK.bike_ticks_per_lane)) == [2]
    assert list(lanes_at(bike, 100 + 5 * DEFAULT_WALK.bike_ticks_per_lane)) == []


def test_cooldown_and_edges_ignore_lane_changes():
    course = single(Kind.FLYER, 0, 100)
    # The second change comes during the cooldown, so the pet stays in lane 1 and hits nothing.
    assert replay(course, [[1, -1], [2, -1]]).hits == 0
    # Two changes far enough apart reach lane 0 and hit the flyer.
    assert replay(course, [[1, -1], [1 + DEFAULT_WALK.move_cooldown_ticks, -1]]).hits == 1
    # Changes past the edge are ignored.
    edge = single(Kind.FLYER, 4, 100)
    assert replay(edge, [[1, 1], [10, 1], [20, 1]]).hits == 1


def test_stunned_pets_ignore_input():
    course = single(Kind.FLYER, 2, 30)
    hit = replay(course, [])
    assert replay(course, [[20, 1]]).distance_px == hit.distance_px


@pytest.mark.parametrize("inputs", [
    [[5, 0]],
    [[5, 2]],
    [[-1, 1]],
    [[600, 1]],
    [[5, 1], [5, -1]],
    [[6, 1], [5, -1]],
    [[5.0, 1]],
    [[True, 1]],
    [[5, 1, 1]],
    ["left"],
])
def test_malformed_inputs_are_rejected(inputs):
    with pytest.raises(ValueError):
        replay(Course(0, 600, ()), inputs)


def test_config_rejects_rows_too_close_to_dodge_separately():
    with pytest.raises(ValueError):
        WalkConfig(row_gap_min_px=40)
    with pytest.raises(ValueError):
        WalkConfig(speed_max=1000)


def test_course_serializes_for_the_client():
    data = generate(1).to_dict()
    assert data["ticks"] == 3600
    assert {"kind", "lane", "y", "depth", "width", "t0", "direction"} == set(data["obstacles"][0])
