"""Job fairs. The first fair here opens Wednesday Oct 7 2026 at 9 AM and its results come out Thursday at
9:05 AM; the next opens Oct 14."""

import dataclasses
from datetime import datetime

import pytest
from helpers import HOUR, et

from locusts import actions, fairs
from locusts.config import DEFAULT_CONFIG as C
from locusts.db import PetStore
from locusts.fairs import FairError, first_window, match, posted_roles, run_rules
from locusts.minigames import hey_day_stacker, locust_walk
from locusts.pet import Activity, advance, new_pet, perks_of
from locusts.upgrades import careers_for

MON_9AM = et(2026, 10, 5, 9)
WED_9AM = et(2026, 10, 7, 9)
WED_NOON = et(2026, 10, 7, 12)
THU_9AM = et(2026, 10, 8, 9)
RESULTS = THU_9AM + 5 * 60
NEXT_RESULTS = et(2026, 10, 15, 9, 5)
WEEKLY = C.internship_pay_per_week
CAREERS = careers_for()


@pytest.fixture
def store(tmp_path):
    return PetStore(str(tmp_path / "fairs.db"))


def hatch(store, career, upgrades=None, at=MON_9AM, name="larva", **fields) -> int:
    pet = dataclasses.replace(new_pet(name, at, career=career), upgrades={career: upgrades or {}}, **fields)
    return store.create(pet).id


def open_fair(store, roles):
    """Post exactly `roles` at Wednesday's fair."""
    with store.transaction() as tx:
        return tx.add_fair(WED_9AM, THU_9AM, RESULTS, roles)


def act(store, pet_id, now, fn):
    fairs.settle(store, now)
    with store.transaction() as tx:
        pet = advance(tx.pet(pet_id), now)[0]
        return fn(tx, pet)


def apply(store, pet_id, role, now=WED_NOON, instead_of=None):
    act(store, pet_id, now, lambda tx, p: fairs.apply(tx, p, role, now, instead_of=instead_of))


def entry(store, fair_id, pet_id):
    return store.read(lambda tx: tx.entry(fair_id, pet_id))


def scored(store, fair, pet_id, raw, at=WED_NOON, multiplier=1.0):
    """Record a submitted run without playing it."""
    with store.transaction() as tx:
        a = tx.add_attempt(fair.id, pet_id, 1, 60, 3, multiplier, at)
        a.submitted_at, a.raw_score, a.score = at, raw, raw * multiplier
        tx.save_attempt(a)


def pet(store, pet_id, at=None):
    p = store.get(pet_id)
    return advance(p, at)[0] if at else p


def money(p) -> float:
    """The pet's money plus what it spent on DoorDash, since these pets are never fed and keep ordering it."""
    return p.money + p.doordashes * C.doordash_price


# --- Schedule ---


def test_fairs_open_wednesday_morning_for_a_day():
    w = first_window(MON_9AM)
    assert (w.opens_at, w.closes_at, w.results_at) == (WED_9AM, THU_9AM, RESULTS)
    assert first_window(WED_NOON) == w
    assert first_window(RESULTS - 1) == w
    assert first_window(RESULTS).opens_at == et(2026, 10, 14, 9)


def test_fairs_stay_at_9am_across_daylight_saving():
    w = first_window(et(2026, 10, 30, 9))
    assert w.opens_at == et(2026, 11, 4, 9)
    assert datetime.fromtimestamp(w.opens_at, C.timezone).hour == 9
    assert w.closes_at - w.opens_at == 24 * HOUR


def test_each_fair_posts_an_open_role_and_a_few_more_per_career():
    roles = posted_roles(WED_9AM)
    assert roles == posted_roles(WED_9AM)
    for career in CAREERS.values():
        mine = [career.roles[r] for r in roles if r in career.roles]
        regular = [r for r in mine if r.regular]
        assert len(regular) == C.fair_roles_per_career
        assert any(not r.requires for r in regular)
        assert {r.id for r in mine if not r.regular} == {
            r.id for r in career.roles.values() if not r.regular and not r.reserved}
    assert "pa_supreme_clerk" not in roles
    weeks = {tuple(posted_roles(first_window(WED_9AM + n * 7 * 24 * HOUR).opens_at)) for n in range(6)}
    assert len(weeks) > 1


def test_settle_creates_fairs_and_resolves_them_when_results_are_due(store):
    fairs.settle(store, MON_9AM)
    first = store.read(lambda tx: tx.latest_fair())
    assert (first.opens_at, first.resolved) == (WED_9AM, False)
    fairs.settle(store, RESULTS - 1)
    assert store.read(lambda tx: tx.latest_fair()).id == first.id

    fairs.settle(store, RESULTS + 3 * 7 * 24 * HOUR)
    latest = store.read(lambda tx: tx.latest_fair())
    assert latest.opens_at == et(2026, 11, 4, 9)
    assert store.read(lambda tx: tx.fair(first.id)).resolved
    assert latest.id == first.id + 4


def test_fairs_take_turns_between_the_minigames(store):
    fairs.settle(store, MON_9AM)
    fairs.settle(store, RESULTS + 3 * 7 * 24 * HOUR)
    games = [store.read(lambda tx, i=i: tx.fair(i)).game for i in range(1, 6)]
    assert games == ["locust-walk", "hey-day-stacker", "locust-walk", "hey-day-stacker", "locust-walk"]


# --- Applying ---


def test_applying_needs_an_open_fair_a_posted_role_and_the_tiers(store):
    fair = open_fair(store, ["comcast_swe", "google_step", "meta_swe"])
    pid = hatch(store, "tech", {"industry": 1})
    with pytest.raises(FairError, match="no job fair is open"):
        apply(store, pid, "comcast_swe", now=MON_9AM)
    with pytest.raises(FairError, match="isn't posted"):
        apply(store, pid, "amazon_sde")
    with pytest.raises(FairError, match="isn't posted"):
        apply(store, pid, "lazard_sa")
    with pytest.raises(FairError, match="needs Big Tech tier 2"):
        apply(store, pid, "meta_swe")
    apply(store, pid, "google_step")
    apply(store, pid, "comcast_swe")
    assert entry(store, fair.id, pid).roles == ["google_step", "comcast_swe"]
    with pytest.raises(FairError, match="already applied"):
        apply(store, pid, "google_step")


def test_two_applications_unless_handshake_resume_adds_one_at_two_fairs(store):
    fair = open_fair(store, ["comcast_swe", "pennovation_startup", "google_step"])
    plain = hatch(store, "tech", {"industry": 1})
    for role in ("comcast_swe", "pennovation_startup"):
        apply(store, plain, role)
    with pytest.raises(FairError, match="apply to 2 roles"):
        apply(store, plain, "google_step")

    resume = hatch(store, "tech", {"industry": 2})
    for role in ("comcast_swe", "pennovation_startup", "google_step"):
        apply(store, resume, role)
    assert len(entry(store, fair.id, resume).roles) == 3

    with store.transaction() as tx:
        for week in (1, 2):
            opens = WED_9AM + week * 7 * 24 * HOUR
            later = tx.add_fair(opens, opens + 24 * HOUR, opens + 24 * HOUR + 300, ["comcast_swe"])
            if week == 1:
                tx.save_entry(fairs.Entry(later.id, resume, ["a", "b", "c"]))
        p = tx.pet(resume)
        perks = perks_of(p)
        others = tx.pet_entries(resume)
        assert fairs.application_limit(perks, fairs.Entry(later.id, resume), others) == 2


def test_a_referral_covers_one_missing_tier_once_per_fair(store):
    fair = open_fair(store, ["palantir_fde", "grasp_ra", "msr_intern", "comcast_swe"])
    pid = hatch(store, "tech", {"industry": 3, "grind": 1})
    with pytest.raises(FairError, match="needs The Lab tier 2"):
        apply(store, pid, "msr_intern")
    apply(store, pid, "palantir_fde")
    assert entry(store, fair.id, pid).referral == "palantir_fde"
    with pytest.raises(FairError, match="referral is already used"):
        apply(store, pid, "grasp_ra")
    apply(store, pid, "grasp_ra", instead_of="palantir_fde")
    e = entry(store, fair.id, pid)
    assert (e.roles, e.referral) == (["grasp_ra"], "grasp_ra")

    without = hatch(store, "tech", {"industry": 2, "grind": 1})
    with pytest.raises(FairError, match="needs The Grind tier 2"):
        apply(store, without, "palantir_fde")


def test_exclusive_roles_need_their_unlock(store):
    open_fair(store, ["comcast_swe", "jane_street_quant"])
    with pytest.raises(FairError, match="isn't posted"):
        apply(store, hatch(store, "tech", {"grind": 3}), "jane_street_quant")
    apply(store, hatch(store, "tech", {"grind": 4}), "jane_street_quant")


def test_starting_a_scored_run_locks_applications(store):
    open_fair(store, ["comcast_swe", "pennovation_startup"])
    pid = hatch(store, "tech")
    start = lambda tx, p: fairs.start_run(tx, p, WED_NOON)  # noqa: E731
    with pytest.raises(FairError, match="apply to a role before playing"):
        act(store, pid, WED_NOON, start)
    apply(store, pid, "comcast_swe")
    act(store, pid, WED_NOON, start)
    with pytest.raises(FairError, match="applications locked"):
        apply(store, pid, "pennovation_startup")
    with pytest.raises(FairError, match="applications locked"):
        act(store, pid, WED_NOON, lambda tx, p: fairs.withdraw(tx, p, "comcast_swe", WED_NOON))
    with pytest.raises(FairError, match="used your scored run"):
        act(store, pid, WED_NOON, start)


def test_withdrawing_before_playing(store):
    fair = open_fair(store, ["comcast_swe", "pennovation_startup"])
    pid = hatch(store, "tech")
    apply(store, pid, "comcast_swe")
    act(store, pid, WED_NOON, lambda tx, p: fairs.withdraw(tx, p, "comcast_swe", WED_NOON))
    assert entry(store, fair.id, pid).roles == []


def test_due_diligence_swaps_one_application_after_locking(store):
    fair = open_fair(store, ["wharton_pro_bono", "accenture_strategy", "oliver_wyman"])
    pid = hatch(store, "consulting", {"case_prep": 3})
    apply(store, pid, "wharton_pro_bono")
    apply(store, pid, "accenture_strategy")
    act(store, pid, WED_NOON, lambda tx, p: fairs.start_run(tx, p, WED_NOON))
    with pytest.raises(FairError, match="only swap one"):
        apply(store, pid, "oliver_wyman")
    apply(store, pid, "oliver_wyman", instead_of="wharton_pro_bono")
    e = entry(store, fair.id, pid)
    assert (e.roles, e.late_changes) == (["oliver_wyman", "accenture_strategy"], 1)
    with pytest.raises(FairError, match="only swap one"):
        apply(store, pid, "wharton_pro_bono", instead_of="accenture_strategy")


# --- Scored runs ---


def test_scored_runs_replay_on_the_server(store, monkeypatch):
    monkeypatch.setattr(fairs, "new_seed", lambda: 1234)
    open_fair(store, ["comcast_swe"])
    pid = hatch(store, "tech", {"grind": 2})
    apply(store, pid, "comcast_swe")
    attempt, game, course, walk = act(store, pid, WED_NOON, lambda tx, p: fairs.start_run(tx, p, WED_NOON))
    assert game.id == "locust-walk"
    assert (attempt.seconds, attempt.max_hits, walk.max_hits, course.ticks) == (66, 4, 4, 66 * 60)

    inputs = [[30, 1], [200, -1], [400, -1]]
    expected = locust_walk.replay(locust_walk.generate(1234, 66, walk), inputs, walk)
    t = WED_NOON + 90
    done = act(store, pid, t, lambda tx, p: fairs.submit_run(tx, p, attempt.id, inputs, t))
    assert (done.raw_score, done.score, done.hits) == (expected.score, expected.score, expected.hits)
    assert done.stats == {"distance": expected.distance_px, "swag": expected.swag}
    assert store.read(lambda tx: tx.attempt(attempt.id)).stats == done.stats
    with pytest.raises(FairError, match="already submitted"):
        act(store, pid, t, lambda tx, p: fairs.submit_run(tx, p, attempt.id, inputs, t))

    t = WED_NOON + 100
    retry, _, _, _ = act(store, pid, t, lambda tx, p: fairs.start_run(tx, p, t))
    with pytest.raises(FairError, match="used all 2 scored runs"):
        act(store, pid, t, lambda tx, p: fairs.start_run(tx, p, t))
    late = t + 66 + C.fair_submit_seconds + 1
    with pytest.raises(FairError, match="too late"):
        act(store, pid, late, lambda tx, p: fairs.submit_run(tx, p, retry.id, [], late))
    with pytest.raises(fairs.NotFound):
        act(store, hatch(store, "tech"), t, lambda tx, p: fairs.submit_run(tx, p, retry.id, [], t))


def test_bad_inputs_are_rejected(store):
    open_fair(store, ["comcast_swe"])
    pid = hatch(store, "tech")
    apply(store, pid, "comcast_swe")
    attempt, _, _, _ = act(store, pid, WED_NOON, lambda tx, p: fairs.start_run(tx, p, WED_NOON))
    with pytest.raises(FairError, match="direction must be -1 or 1"):
        act(store, pid, WED_NOON, lambda tx, p: fairs.submit_run(tx, p, attempt.id, [[5, 2]], WED_NOON))


def test_stacker_fairs_score_stacker_runs(store, monkeypatch):
    monkeypatch.setattr(fairs, "new_seed", lambda: 99)
    with store.transaction() as tx:
        tx.add_fair(WED_9AM, THU_9AM, RESULTS, ["comcast_swe"], "hey-day-stacker")
    pid = hatch(store, "tech", {"grind": 2})
    apply(store, pid, "comcast_swe")
    with pytest.raises(FairError, match="this fair plays Hey Day Stacker"):
        act(store, pid, WED_NOON, lambda tx, p: fairs.start_run(tx, p, WED_NOON, expected_game="locust-walk"))
    attempt, game, course, config = act(
        store, pid, WED_NOON, lambda tx, p: fairs.start_run(tx, p, WED_NOON, expected_game="hey-day-stacker"))
    assert (game.id, config.max_hits, course.ticks) == ("hey-day-stacker", 4, 66 * 60)

    inputs = list(range(40, 3000, 45))
    expected = hey_day_stacker.replay(hey_day_stacker.generate(99, 66, config), inputs, config)
    assert expected.floors > 0
    t = WED_NOON + 90
    done = act(store, pid, t, lambda tx, p: fairs.submit_run(tx, p, attempt.id, inputs, t))
    assert (done.raw_score, done.hits) == (expected.score, expected.hits)
    assert done.stats == {"floors": expected.floors, "perfects": expected.perfects}
    view = store.read(lambda tx: fairs.fair_view(tx, tx.pet(pid), t))
    assert view["game"] == {"id": "hey-day-stacker", "name": "Hey Day Stacker", "page": "/stacker"}
    assert view["runs"]["best"]["stats"] == done.stats


def test_tiers_add_hits_and_perks_add_time_and_score():
    grind = dataclasses.replace(new_pet("x", MON_9AM, career="tech"), upgrades={"tech": {"grind": 5, "industry": 2}})
    rules = run_rules(grind, perks_of(grind), MON_9AM, rejected=False)
    assert (rules.seconds, rules.max_hits, rules.multiplier) == (66, 6, 1.0)

    lab = dataclasses.replace(grind, upgrades={"tech": {"research": 3}})
    rules = run_rules(lab, perks_of(lab), MON_9AM, rejected=True)
    assert (rules.seconds, rules.max_hits, rules.multiplier) == (60, 4, 1.25)
    assert run_rules(lab, perks_of(lab), MON_9AM, rejected=False).multiplier == 1.0


def test_leetcode_streak_counts_consecutive_training_days():
    p = dataclasses.replace(new_pet("x", MON_9AM, career="tech"), upgrades={"tech": {"grind": 3}})
    for day in range(4):
        t = MON_9AM + day * 24 * HOUR
        p = actions.recall(actions.train(p, t, "towne", "grind"), t + HOUR)
    assert (p.train_day, p.train_streak) == (MON_9AM + 3 * 24 * HOUR, 4)
    t = MON_9AM + 3 * 24 * HOUR + 2 * HOUR
    assert run_rules(p, perks_of(p), t, False).boosts == (("4-day training streak", pytest.approx(0.12)),)
    assert run_rules(p, perks_of(p), t + 24 * HOUR, False).multiplier == pytest.approx(1.12)
    assert run_rules(p, perks_of(p), t + 48 * HOUR, False).multiplier == 1.0

    t = MON_9AM + 5 * 24 * HOUR
    p = actions.recall(actions.train(p, t, "towne", "grind"), t + HOUR)
    assert p.train_streak == 1
    capped = dataclasses.replace(p, train_streak=14)
    assert run_rules(capped, perks_of(capped), t, False).multiplier == pytest.approx(1.30)


# --- Results ---


def test_match_gives_each_pet_the_best_role_it_can_win():
    scores = {1: 100, 2: 90, 3: 80}
    rank = lambda pid, rid: (scores[pid],)  # noqa: E731
    assert match({1: ["x"], 2: ["x", "y"], 3: ["y"]}, rank) == {1: "x", 2: "y"}
    assert match({1: ["y", "x"], 2: ["x"], 3: ["x", "y"]}, rank) == {1: "y", 2: "x"}
    assert match({1: [], 2: ["x"]}, rank) == {2: "x"}


def test_roles_go_to_the_best_scores_in_order_of_preference(store):
    fair = open_fair(store, ["comcast_swe", "pennovation_startup", "google_step"])
    a, b, c, no_run, idle = (hatch(store, "tech", name=n) for n in ("a", "b", "c", "no_run", "idle"))
    apply(store, a, "comcast_swe")
    apply(store, b, "comcast_swe")
    apply(store, b, "pennovation_startup")
    apply(store, c, "pennovation_startup")
    apply(store, no_run, "comcast_swe")
    for pid, raw in ((a, 500), (b, 600), (c, 400)):
        scored(store, fair, pid, raw)
    fairs.settle(store, RESULTS)

    won = {pid: (entry(store, fair.id, pid) or fairs.Entry(0, 0)).won for pid in (a, b, c, no_run, idle)}
    assert won == {a: None, b: "comcast_swe", c: "pennovation_startup", no_run: None, idle: None}
    job = pet(store, b).internship
    assert (job["role"], job["start"], job["until"], job["kept"]) == ("comcast_swe", RESULTS, NEXT_RESULTS, 0)
    assert job["pay"] == pytest.approx(0.8 * WEEKLY / 168)
    assert pet(store, a).internship is None
    assert pet(store, idle).updated_at == RESULTS


def test_an_internship_pays_by_the_hour_until_the_next_fairs_results(store):
    fair = open_fair(store, ["comcast_swe"])
    pid = hatch(store, "tech")
    apply(store, pid, "comcast_swe")
    scored(store, fair, pid, 100)
    fairs.settle(store, RESULTS)
    p = pet(store, pid)
    assert money(advance(p, RESULTS + 24 * HOUR)[0]) == pytest.approx(money(p) + 0.8 * WEEKLY / 7)
    assert money(advance(p, NEXT_RESULTS + 24 * HOUR)[0]) == pytest.approx(money(p) + 0.8 * WEEKLY)


def test_top_cutoffs_compare_raw_scores_across_the_fair(store):
    fair = open_fair(store, ["jane_street_quant", "comcast_swe", "wharton_pro_bono"])
    for raw in (1000, 900, 800):
        other = hatch(store, "consulting")
        apply(store, other, "wharton_pro_bono")
        scored(store, fair, other, raw)
    maker = hatch(store, "tech", {"grind": 5})
    quant = hatch(store, "tech", {"grind": 4})
    apply(store, maker, "jane_street_quant")
    apply(store, quant, "jane_street_quant")
    scored(store, fair, maker, 700)
    scored(store, fair, quant, 650, multiplier=2.0)
    fairs.settle(store, RESULTS)
    assert entry(store, fair.id, maker).won == "jane_street_quant"
    assert entry(store, fair.id, quant).won is None


def test_first_author_wins_biomed_roles_without_playing(store):
    fair = open_fair(store, ["wistar_fellow", "penn_med_cra"])
    author = hatch(store, "premed", {"wetlab": 5})
    badge = hatch(store, "premed", {"wetlab": 4})
    apply(store, author, "wistar_fellow")
    apply(store, badge, "wistar_fellow")
    scored(store, fair, badge, 900)
    fairs.settle(store, RESULTS)
    assert entry(store, fair.id, author).won == "wistar_fellow"
    assert entry(store, fair.id, badge).won is None


def test_landmark_case_reserves_a_clerkship_for_a_pet_that_wins_nothing(store):
    fair = open_fair(store, ["phila_bar_clerk", "third_circuit_clerk"])
    idle = hatch(store, "prelaw", {"public_interest": 5})
    winner = hatch(store, "prelaw", {"public_interest": 5})
    clerk = hatch(store, "prelaw", {"public_interest": 4})
    apply(store, winner, "third_circuit_clerk")
    scored(store, fair, winner, 100)
    fairs.settle(store, RESULTS)
    assert pet(store, idle).internship["role"] == "pa_supreme_clerk"
    assert pet(store, idle).internship["pay"] == pytest.approx(0.7 * WEEKLY / 168)
    assert entry(store, fair.id, idle).won == "pa_supreme_clerk"
    assert pet(store, winner).internship["role"] == "third_circuit_clerk"
    assert pet(store, clerk).internship is None


def test_ib_internships_end_when_the_pet_gets_exhausted(store):
    fair = open_fair(store, ["goldman_ib", "wigg_analyst"])
    pid = hatch(store, "finance", {"banking": 4})
    apply(store, pid, "goldman_ib")
    scored(store, fair, pid, 100)
    fairs.settle(store, RESULTS)
    p = pet(store, pid)
    assert p.internship["lost_on_exhaustion"]
    p = actions.train(p, RESULTS + HOUR, "huntsman", "banking")
    p, events = advance(p, RESULTS + 12 * HOUR)
    lost = next(e for e in events if e.kind == "internship_lost")
    assert p.internship["until"] == lost.at
    assert [e.kind for e in events if e.at == lost.at][-2:] == ["exhausted", "internship_lost"]
    assert money(advance(p, p.updated_at + 24 * HOUR)[0]) == pytest.approx(money(p))


def test_lockstep_pays_more_for_each_fair_the_same_internship_is_kept(store):
    fair = open_fair(store, ["phila_bar_clerk"])
    pid = hatch(store, "prelaw", {"biglaw": 4})
    apply(store, pid, "phila_bar_clerk")
    scored(store, fair, pid, 100)
    fairs.settle(store, RESULTS)
    assert pet(store, pid).internship["kept"] == 0

    wed = et(2026, 10, 14, 12)
    fairs.settle(store, wed)
    second = store.read(lambda tx: tx.latest_fair())
    assert "phila_bar_clerk" in second.roles
    apply(store, pid, "phila_bar_clerk", now=wed)
    scored(store, second, pid, 100, at=wed)
    fairs.settle(store, NEXT_RESULTS)
    p = pet(store, pid)
    assert p.internship["kept"] == 1
    before = p.money
    rate = 0.8 * WEEKLY / 168 * (1 + 0.4 + 0.2)
    document_review = 0.7 * C.task_pay_per_hour
    assert advance(p, NEXT_RESULTS + HOUR)[0].money == pytest.approx(before + rate + document_review)


def test_exit_opp_pays_a_lump_sum_when_an_internship_ends(store):
    fair = open_fair(store, ["wharton_pro_bono"])
    pid = hatch(store, "consulting", {"billable": 5})
    apply(store, pid, "wharton_pro_bono")
    scored(store, fair, pid, 100)
    fairs.settle(store, RESULTS)
    start = money(pet(store, pid))
    fairs.settle(store, NEXT_RESULTS)
    weekly = 0.8 * WEEKLY * 1.2
    assert money(pet(store, pid)) == pytest.approx(start + weekly + 1.5 * weekly)


def test_expense_report_makes_food_free_while_interning(store):
    fair = open_fair(store, ["wharton_pro_bono"])
    pid = hatch(store, "consulting", {"billable": 3})
    apply(store, pid, "wharton_pro_bono")
    scored(store, fair, pid, 100)
    fairs.settle(store, RESULTS)
    t = RESULTS + 8 * HOUR
    p = advance(pet(store, pid), t)[0]
    assert actions.feed(p, t).money == pytest.approx(p.money)
    p = dataclasses.replace(advance(p, NEXT_RESULTS + HOUR)[0], money=50.0)
    assert actions.feed(p, NEXT_RESULTS + HOUR).money == pytest.approx(45)


def test_rest_and_vest_keeps_paying_after_the_internship_ends(store):
    fair = open_fair(store, ["comcast_swe"])
    pid = hatch(store, "tech", {"industry": 5})
    apply(store, pid, "comcast_swe")
    scored(store, fair, pid, 100)
    fairs.settle(store, RESULTS)
    fairs.settle(store, NEXT_RESULTS)
    p = pet(store, pid)
    assert p.internship["until"] == NEXT_RESULTS
    per_day = 0.75 * 0.8 * WEEKLY * 1.5 / 7
    assert money(advance(p, NEXT_RESULTS + 24 * HOUR)[0]) == pytest.approx(money(p) + per_day)


def test_equity_partner_earns_a_share_of_every_internship(store):
    fair = open_fair(store, ["phila_bar_clerk", "comcast_swe"])
    partner = hatch(store, "prelaw", {"biglaw": 5})
    other = hatch(store, "tech")
    apply(store, partner, "phila_bar_clerk")
    apply(store, other, "comcast_swe")
    scored(store, fair, partner, 100)
    scored(store, fair, other, 100)
    fairs.settle(store, RESULTS)
    base = 0.8 * WEEKLY / 168
    total = base * (1 + 0.4 + 0.2) + base
    assert pet(store, partner).bonuses == [{"name": "Equity Partner", "per_hour": pytest.approx(0.05 * total),
                                            "start": RESULTS, "until": NEXT_RESULTS}]
    assert pet(store, other).bonuses == []


def test_reviewer_2_boosts_the_fair_after_a_rejection(store):
    fair = open_fair(store, ["comcast_swe"])
    lab = hatch(store, "tech", {"research": 3})
    rival = hatch(store, "tech")
    apply(store, lab, "comcast_swe")
    apply(store, rival, "comcast_swe")
    scored(store, fair, lab, 100)
    scored(store, fair, rival, 900)
    fairs.settle(store, et(2026, 10, 14, 12))
    with store.transaction() as tx:
        second = tx.latest_fair()
        assert fairs.rejected_last_fair(tx, second, lab)
        assert not fairs.rejected_last_fair(tx, second, rival)


def test_pennovation_spinout_pays_all_the_time():
    p = dataclasses.replace(new_pet("x", MON_9AM, career="tech"), upgrades={"tech": {"research": 5}})
    assert money(advance(p, MON_9AM + 24 * HOUR)[0]) == pytest.approx(50 + 0.333 * WEEKLY / 7)


def test_internship_pay_does_not_depend_on_how_often_the_pet_is_looked_at():
    job = {"role": "goldman_ib", "company": "c", "title": "t", "pay": 2.0, "start": MON_9AM + 5 * HOUR,
           "until": MON_9AM + 50 * HOUR, "kept": 1, "lost_on_exhaustion": True, "fair": 1}
    p = dataclasses.replace(
        new_pet("x", MON_9AM, career="finance"), upgrades={"finance": {"banking": 4}}, internship=job,
        bonuses=[{"name": "b", "per_hour": 0.5, "start": MON_9AM + 3 * HOUR, "until": MON_9AM + 30 * HOUR}])
    p = actions.train(p, MON_9AM + 6 * HOUR, "huntsman", "banking")
    end = MON_9AM + 70 * HOUR
    once = advance(p, end)[0]
    often = p
    while often.updated_at + 37 * 60 < end:
        often = advance(often, often.updated_at + 37 * 60)[0]
    often = advance(often, end)[0]
    assert often.money == pytest.approx(once.money)
    assert often.internship == once.internship
    assert often.activity is once.activity is Activity.SLEEPING


# --- Moves on GPA and on other pets ---

def test_committee_letter_covers_any_role_one_tier_short_while_gpa_is_high(store):
    fair = open_fair(store, ["hup_scribe", "penn_ems", "kaplan_tutor"])
    honors = hatch(store, "premed", {"gunner": 3}, gpa=3.8)
    apply(store, honors, "hup_scribe")
    apply(store, honors, "kaplan_tutor")
    assert entry(store, fair.id, honors).referral is None
    with pytest.raises(FairError, match="needs"):
        apply(store, hatch(store, "premed", {"gunner": 3}, gpa=3.8), "penn_ems")
    with pytest.raises(FairError, match="needs"):
        apply(store, hatch(store, "premed", {"gunner": 3}, gpa=3.6), "hup_scribe")


def test_curve_wrecker_cuts_lower_gpa_applicants_from_its_roles(store):
    fair = open_fair(store, ["penn_med_cra"])
    wrecker = hatch(store, "premed", {"gunner": 5}, gpa=3.9)
    lower = hatch(store, "premed", gpa=3.5)
    for pid in (wrecker, lower):
        apply(store, pid, "penn_med_cra")
    scored(store, fair, wrecker, 100)
    scored(store, fair, lower, 900)
    fairs.settle(store, RESULTS)
    assert entry(store, fair.id, wrecker).won == "penn_med_cra"


def test_curve_wrecker_doesnt_cut_a_higher_gpa(store):
    fair = open_fair(store, ["penn_med_cra"])
    wrecker = hatch(store, "premed", {"gunner": 5}, gpa=3.7)
    higher = hatch(store, "premed", gpa=3.95)
    for pid in (wrecker, higher):
        apply(store, pid, "penn_med_cra")
    scored(store, fair, wrecker, 100)
    scored(store, fair, higher, 900)
    fairs.settle(store, RESULTS)
    assert entry(store, fair.id, higher).won == "penn_med_cra"


def short(store, pid, target, stake):
    act(store, pid, WED_NOON, lambda tx, p: fairs.short(tx, p, target, stake, WED_NOON))


@pytest.mark.parametrize(("target_applies", "target_wins", "back"), [
    (True, False, 2.0), (True, True, 0.0), (False, False, 1.0)])
def test_a_short_pays_double_if_the_target_wins_nothing(store, target_applies, target_wins, back):
    fair = open_fair(store, ["comcast_swe"])
    seller = hatch(store, "finance", {"markets": 5}, money=100.0, swipes=99)
    target = hatch(store, "tech")
    rival = hatch(store, "tech")
    short(store, seller, target, 30)
    assert money(pet(store, seller)) == pytest.approx(70)
    if target_applies:
        apply(store, target, "comcast_swe")
        scored(store, fair, target, 900 if target_wins else 100)
    apply(store, rival, "comcast_swe")
    scored(store, fair, rival, 500)
    fairs.settle(store, RESULTS)
    assert money(pet(store, seller)) == pytest.approx(70 + back * 30)
    assert entry(store, fair.id, seller).short_payout == pytest.approx(back * 30)


def test_a_short_is_once_per_fair_and_at_most_half_your_money(store):
    open_fair(store, ["comcast_swe"])
    seller = hatch(store, "finance", {"markets": 5}, money=100.0, swipes=99)
    target = hatch(store, "tech")
    with pytest.raises(FairError, match="at most"):
        short(store, seller, target, 60)
    with pytest.raises(FairError, match="another pet"):
        short(store, seller, seller, 10)
    short(store, seller, target, 10)
    with pytest.raises(FairError, match="already"):
        short(store, seller, target, 10)
    with pytest.raises(FairError, match="can't short"):
        short(store, target, seller, 10)


def back(store, pid, target):
    act(store, pid, WED_NOON, lambda tx, p: fairs.back(tx, p, target, WED_NOON))


def start(store, pid):
    return act(store, pid, WED_NOON, lambda tx, p: fairs.start_run(tx, p, WED_NOON))[0]


def test_amicus_brief_boosts_the_backer_and_the_backed(store):
    open_fair(store, ["phila_bar_clerk", "comcast_swe"])
    lawyer = hatch(store, "prelaw", {"public_interest": 3})
    friend = hatch(store, "tech")
    apply(store, lawyer, "phila_bar_clerk")
    apply(store, friend, "comcast_swe")
    back(store, lawyer, friend)
    assert start(store, lawyer).multiplier == pytest.approx(1.2)
    assert start(store, friend).multiplier == pytest.approx(1.1)
    with pytest.raises(FairError, match="already backed"):
        back(store, lawyer, friend)


def test_amicus_brief_has_to_come_before_your_first_scored_run(store):
    open_fair(store, ["phila_bar_clerk"])
    lawyer = hatch(store, "prelaw", {"public_interest": 3})
    friend = hatch(store, "tech")
    apply(store, lawyer, "phila_bar_clerk")
    start(store, lawyer)
    with pytest.raises(FairError, match="before your first scored run"):
        back(store, lawyer, friend)
