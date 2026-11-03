import pytest

from locusts.minigames.hey_day_stacker import (
    DEFAULT_STACKER,
    UNITS_PER_PX,
    Course,
    Run,
    StackerConfig,
    generate,
    new_run,
    replay,
    step,
)

C = DEFAULT_STACKER
U = UNITS_PER_PX


def play(course: Course, want, config: StackerConfig = C) -> list[int]:
    """Drop each block on the first tick `want(run)` holds for it, logging drops like the client."""
    run = new_run(course, config)
    inputs = []
    while not run.over:
        drop = run.x is not None and want(run)
        if drop:
            inputs.append(run.tick)
        step(run, drop)
    return inputs


def perfectly(run: Run) -> bool:
    return abs(run.x - run.top_x) <= run.config.perfect_px * U


def spawned(seed: int = 1) -> Run:
    """A run whose first block has just appeared."""
    run = new_run(generate(seed))
    step(run, False)
    assert run.x is not None
    return run


def test_same_seed_same_course():
    assert generate(7) == generate(7)
    assert len({generate(seed).sides for seed in range(5)}) == 5
    assert set(generate(7).sides) == {0, 1}


def test_there_is_a_side_for_every_block_that_can_appear():
    endless = StackerConfig(max_hits=10_000)
    course = generate(3, seconds=10, config=endless)
    inputs = play(course, lambda run: True, endless)
    assert len(inputs) >= len(course.sides) - 2
    assert replay(course, inputs, endless).ticks_played == course.ticks


def test_perfect_drops_never_miss_and_score_a_bonus():
    course = generate(11)
    inputs = play(course, perfectly)
    r = replay(course, inputs)
    assert r.hits == 0
    assert r.perfects == r.floors == len(inputs) > 40
    assert r.score == r.floors * (C.floor_points + C.perfect_points)
    assert r.ticks_played == course.ticks


def test_doing_nothing_scores_nothing():
    course = generate(5)
    assert replay(course, []) == replay(course, []) and replay(course, []).score == 0
    assert replay(course, []).ticks_played == course.ticks


def test_overhang_is_trimmed_off():
    run = spawned()
    top_x = run.top_x
    run.x = top_x + 10 * U
    drop = step(run, True)
    assert (drop.kind, drop.x, drop.width) == ("trim", top_x + 10 * U, C.block)
    assert (run.top_x, run.top_w, run.floors, run.perfects) == (top_x + 10 * U, C.block - 10 * U, 1, 0)


def test_a_full_miss_costs_a_hit_and_keeps_the_tower():
    run = spawned()
    top = (run.top_x, run.top_w)
    run.x = 0 if run.top_x >= run.top_w else run.config.field - run.top_w
    assert step(run, True).kind == "miss"
    assert (run.hits, run.floors, (run.top_x, run.top_w)) == (1, 0, top)


def test_slivers_fall_off_as_misses():
    run = spawned()
    run.x = run.top_x + run.top_w - (C.min_px * U - 1)
    assert step(run, True).kind == "miss"
    run = spawned()
    run.x = run.top_x + run.top_w - C.min_px * U
    assert step(run, True).kind == "trim"
    assert run.top_w == C.min_px * U


def test_perfect_drops_snap_and_a_streak_regrows_the_tower():
    run = spawned()
    run.top_w -= 10 * U
    start = run.top_x
    for n in range(1, C.grow_every + 1):
        while run.x is None:
            step(run, False)
        run.x = run.top_x + C.perfect_px * U
        assert step(run, True).kind == "perfect"
        assert run.top_x <= start
        if n < C.grow_every:
            assert run.top_w == C.block - 10 * U
    assert run.top_w == C.block - 10 * U + C.grow_px * U
    assert run.top_x == start - C.grow_px * U // 2


def test_regrowing_stops_at_the_starting_width():
    course = generate(2)
    run = new_run(course)
    inputs = play(course, perfectly)
    assert replay(course, inputs).perfects >= C.grow_every
    while not run.over:
        step(run, run.tick in inputs)
        assert run.top_w <= C.block


def test_blocks_speed_up_with_every_floor():
    assert C.speed(0) == C.speed_start
    assert C.speed(5) == C.speed_start + 5 * C.speed_step
    assert C.speed(10_000) == C.speed_max


def test_blocks_bounce_off_the_edges_of_the_field():
    run = spawned()
    seen = set()
    for _ in range(400):
        step(run, False)
        assert 0 <= run.x <= run.config.field - run.top_w
        seen.add(run.direction)
    assert seen == {1, -1}


def test_losing_every_hit_ends_the_run():
    course = generate(4)
    misses = play(course, lambda run: abs(run.x - run.top_x) >= run.top_w)
    r = replay(course, misses)
    assert (r.hits, r.floors, r.score) == (C.max_hits, 0, 0)
    assert r.ticks_played == misses[-1] + 1 < course.ticks


def test_drops_between_blocks_are_ignored():
    course = generate(6)
    inputs = play(course, perfectly)
    mashed = sorted(set(inputs) | {t + 1 for t in inputs} | {0})
    assert replay(course, mashed) == replay(course, inputs)


@pytest.mark.parametrize("inputs", [
    "12", [1.5], [True], [[3]], [5, 5], [9, 3], [-1], [3600], {"a": 1},
])
def test_malformed_inputs_are_rejected(inputs):
    with pytest.raises(ValueError):
        replay(generate(1), inputs)


def test_config_rejects_impossible_fields():
    with pytest.raises(ValueError):
        StackerConfig(block_px=144)
    with pytest.raises(ValueError):
        StackerConfig(min_px=0)
    with pytest.raises(ValueError):
        StackerConfig(field_px=50, block_px=48, speed_max=64)
    with pytest.raises(ValueError):
        StackerConfig(spawn_delay_ticks=0)


def test_course_and_config_serialize_for_the_client():
    course = generate(8, seconds=20)
    assert course.to_dict() == {"seed": 8, "ticks": 1200, "sides": list(course.sides)}
    assert C.to_dict()["units_per_px"] == U
    assert C.to_dict()["field_px"] == C.field_px
