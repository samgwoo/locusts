"""The campus traffic: the street graph and sprites render_campus.py writes, and the browser's simulation."""

import json
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from locusts import campus_map

MAP_DIR = campus_map.MAP_DIR
TRAFFIC = Path(__file__).parent.parent / "locusts" / "static" / "campus" / "traffic.mjs"
LANE_MIN_PX = 2.6 * 5     # render_campus.py's LANE_MIN_M at 5 px per meter
CAR_WIDTH_PX = 1.9 * 5


def png_size(path: Path) -> tuple[int, int]:
    return struct.unpack(">II", path.read_bytes()[16:24])


def test_the_street_graph_and_sprites_fit_together():
    data = json.loads((MAP_DIR / "traffic.json").read_text())
    campus = campus_map.load()
    nodes = data["nodes"]
    assert data["edges"] and any(inside for _, _, inside, _ in nodes) and not all(inside for _, _, inside, _ in nodes)
    for e in data["edges"]:
        assert 0 <= e["a"] < len(nodes) and 0 <= e["b"] < len(nodes)
        assert len(e["pts"]) >= 2 and e["fwd"]
        assert e["pts"][0] == nodes[e["a"]][:2] and e["pts"][-1] == nodes[e["b"]][:2]
    cars = data["cars"]
    cw, ch = cars["cell"]
    assert png_size(MAP_DIR / "cars.png") == (cw * cars["paints"], ch * cars["headings"])
    assert len(cars["anchors"]) == cars["headings"]
    assert png_size(MAP_DIR / "occluders.png") == (campus.width, campus.height)


def test_lanes_are_wide_enough_that_cars_never_share_one():
    for e in json.loads((MAP_DIR / "traffic.json").read_text())["edges"]:
        # Every lane center across the street, in one frame: forward offsets as given, back ones mirrored.
        centers = sorted(e["fwd"] + [-o for o in e["back"]])
        assert all(b - a >= LANE_MIN_PX - 0.2 for a, b in zip(centers, centers[1:])), e
        for park, lanes in ((e["park_fwd"], e["fwd"]), (e["park_back"], e["back"])):
            if park is not None:
                assert park - max(lanes) >= CAR_WIDTH_PX, e


SCRIPT = f"""
import {{ readFileSync }} from "node:fs";
import {{ Traffic }} from {json.dumps(TRAFFIC.as_uri())};
const data = JSON.parse(readFileSync({json.dumps(str(MAP_DIR / "traffic.json"))}));
const t = new Traffic(data, {{}}, {{}});
const S = data.px_per_m, gy = data.ground_y;
const box = (car) => {{
  const p = t.pose(car);
  let dx = p.dx, dy = p.dy / gy;
  const n = Math.hypot(dx, dy);
  return {{ x: p.x / S, y: p.y / (S * gy), ax: [dx / n, dy / n], ay: [-dy / n, dx / n], hidden: t.underBridge(car, p.x, p.y) }};
}};
// Separating axis test on 4.4 x 1.8 m footprints: a hair under a car, so touching bumpers don't count.
const overlap = (a, b) => [a.ax, a.ay, b.ax, b.ay].every((axis) => {{
  const r = (q) => Math.abs(q.ax[0] * axis[0] + q.ax[1] * axis[1]) * 2.2 + Math.abs(q.ay[0] * axis[0] + q.ay[1] * axis[1]) * 0.9;
  return Math.abs((b.x - a.x) * axis[0] + (b.y - a.y) * axis[1]) <= r(a) + r(b);
}});
const start = t.agents.map((a) => t.pose(a));
const parked = new Set(), styles = new Set(t.agents.map((a) => a.style));
let samples = 0, overlaps = 0, speed = 0;
for (let i = 0; i < 60 * 240; i++) {{
  t.update(1 / 60);
  for (const a of t.agents) if (a.parked !== null) parked.add(a);
  if (i % 30) continue;
  samples++;
  const live = t.agents.filter((a) => !a.pending).map(box).filter((b) => !b.hidden);
  for (let p = 0; p < live.length; p++) for (let q = p + 1; q < live.length; q++) {{
    if (Math.abs(live[p].x - live[q].x) < 6 && Math.abs(live[p].y - live[q].y) < 6 && overlap(live[p], live[q])) overlaps++;
  }}
  const moving = t.agents.filter((a) => !a.pending && a.parked === null);
  if (i > 60 * 180) speed += moving.reduce((s, a) => s + a.speed, 0) / moving.length;
}}
const poses = t.agents.map((a) => t.pose(a));
const residents = t.agents.filter((a) => a.style === "resident");
console.log(JSON.stringify({{
  cars: t.agents.length - residents.length,
  residents: residents.length,
  residentsStayed: residents.every((a) => a.parked === Infinity && a.lateral === 1),
  finite: poses.every((p) => Number.isFinite(p.x) && Number.isFinite(p.y) && Number.isFinite(p.dx)),
  moved: poses.filter((p, i) => t.agents[i].style !== "resident" && Math.hypot(p.x - start[i].x, p.y - start[i].y) > 20).length,
  parked: parked.size,
  styles: [...styles].sort(),
  overlapsPerSample: overlaps / samples,
  lateSpeed: speed / 120,
}}));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_cars_keep_to_their_lanes_and_keep_moving():
    out = subprocess.run(["node", "--input-type=module", "-e", SCRIPT], capture_output=True, text=True, check=True)
    result = json.loads(out.stdout)
    assert result["cars"] > 100
    assert result["finite"]
    assert result["moved"] > result["cars"] * 0.8
    assert result["parked"] > 0
    assert result["styles"] == ["commuter", "cruiser", "looper", "parker", "resident"]
    # Residents line the curbs and never leave.
    assert result["residents"] > 50 and result["residentsStayed"]
    # Corners of turning cars can brush for a moment, but cars don't drive through each other.
    assert result["overlapsPerSample"] < 0.15
    # And traffic doesn't lock up: cars still average a decent clip after four minutes.
    assert result["lateSpeed"] > 3


UTURN_SCRIPT = f"""
import {{ readFileSync }} from "node:fs";
import {{ Traffic }} from {json.dumps(TRAFFIC.as_uri())};
const t = new Traffic(JSON.parse(readFileSync({json.dumps(str(MAP_DIR / "traffic.json"))})), {{}}, {{}});
let trips = 0, uturns = 0;
for (const lane of t.lanes) {{
  if (lane.internal || !t.ways(lane).some((way) => way.length > 1)) continue;
  for (let i = 0; i < 200; i++) {{
    let hop = t.chooseNext(t.fresh({{}}, lane, 0, lane.length));
    if (!hop) continue;
    while (hop.plan && hop.plan !== "exit") hop = hop.plan;
    trips++;
    if (t.turnKind(lane, hop.lane) === "back") uturns++;
  }}
}}
console.log(JSON.stringify({{ trips, uturns }}));
"""


SWING_SCRIPT = f"""
import {{ readFileSync }} from "node:fs";
import {{ Traffic }} from {json.dumps(TRAFFIC.as_uri())};
const data = JSON.parse(readFileSync({json.dumps(str(MAP_DIR / "traffic.json"))}));
const t = new Traffic(data, {{}}, {{}});
let turns = 0, worst = 0;
for (const lane of t.lanes) for (const way of t.ways(lane)) {{
  let hops = t.driven(lane, way);
  const kind = t.turnKind(lane, hops.at(-1));
  if (kind !== "left" && kind !== "right") continue;
  turns++;
  const k = kind === "left" ? lane.slots.length - 1 : 0;
  const a = t.locate(lane, lane.holds[k]), p0 = t.meters(t.shifted(a, lane.slots[k]));
  const hx = a.dx, hy = a.dy / data.ground_y, n = Math.hypot(hx, hy);
  let prev = lane, from = k, s = lane.holds[k];
  for (const hop of hops) {{
    const slot = kind === "left" ? hop.slots.length - 1 : 0;
    const {{ at }} = t.makePath(prev, s, from, hop, slot);
    for (let i = 0; i <= 20; i++) {{
      const q = t.meters(at(i / 20));
      const right = (hx * (q.y - p0.y) - hy * (q.x - p0.x)) / n;
      worst = Math.max(worst, kind === "left" ? right : -right);
    }}
    [prev, from, s] = [hop, slot, hop.length];
  }}
}}
console.log(JSON.stringify({{ turns, worst }}));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_turns_dont_swing_out_the_wrong_way_first():
    """A left turn never drifts right before it goes left, nor a right turn left: not across a divided street,
    whose middle links start on the halves' centerlines, nor into a street that starts barely off to the side."""
    out = subprocess.run(["node", "--input-type=module", "-e", SWING_SCRIPT], capture_output=True, text=True, check=True)
    result = json.loads(out.stdout)
    assert result["turns"] > 50
    assert result["worst"] < 1.0, f"a turn swings {result['worst']:.1f} m the wrong way"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_cars_dont_u_turn_through_big_intersections():
    """Turning right onto a divided street's middle link and right again off it is a U-turn, however ordinary
    each hop looks on its own, so cars pick their way through a big intersection by where it ends up."""
    out = subprocess.run(["node", "--input-type=module", "-e", UTURN_SCRIPT], capture_output=True, text=True, check=True)
    result = json.loads(out.stdout)
    assert result["trips"] > 0
    assert result["uturns"] / result["trips"] < 0.02
