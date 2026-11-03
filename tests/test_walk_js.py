"""The browser's Locust Walk simulation (sim.mjs) must score runs exactly like the server's replay()."""

import json
import random
import shutil
import subprocess
from dataclasses import asdict
from pathlib import Path

import pytest
from test_locust_walk import HARD, solve

from locusts.minigames.locust_walk import DEFAULT_WALK, generate, replay

SIM = Path(__file__).parent.parent / "locusts" / "static" / "walk" / "sim.mjs"
SCRIPT = f"""
import {{ replay }} from {json.dumps(SIM.as_uri())};
let data = "";
process.stdin.on("data", (chunk) => data += chunk);
process.stdin.on("end", () => {{
  const runs = JSON.parse(data);
  console.log(JSON.stringify(runs.map(({{ course, config, inputs }}) => replay(course, inputs, config))));
}});
"""

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def mashing(ticks: int, rng: random.Random) -> list[list[int]]:
    ticks_pressed = sorted(rng.sample(range(ticks), rng.randint(0, 400)))
    return [[t, rng.choice((-1, 1))] for t in ticks_pressed]


def sloppy(perfect: list[list[int]], rng: random.Random, misses: int, tail: int) -> list[list[int]]:
    """A perfect run with moves forgotten among the last `tail`, which ends with a hit or two."""
    start = max(0, len(perfect) - tail)
    dropped = set(rng.sample(range(start, len(perfect)), min(misses, len(perfect) - start)))
    return [move for i, move in enumerate(perfect) if i not in dropped]


def run_node(runs: list[dict]) -> list[dict]:
    out = subprocess.run(["node", "--input-type=module", "-e", SCRIPT], input=json.dumps(runs),
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_browser_simulation_matches_server_replay():
    rng = random.Random(0)
    runs, expected = [], []
    for seed in range(30):
        config = HARD if seed % 3 == 0 else DEFAULT_WALK
        course = generate(seed, seconds=66 if seed % 5 == 0 else None, config=config)
        perfect = solve(course, config)
        variants = (perfect, sloppy(perfect, rng, 1, 3), sloppy(perfect, rng, 2, 40), mashing(course.ticks, rng), [])
        for inputs in variants:
            runs.append({"course": course.to_dict(), "config": config.to_dict(), "inputs": inputs})
            expected.append(asdict(replay(course, inputs, config)))
    assert run_node(runs) == expected
    assert {r["hits"] for r in expected} >= {0, 1, 2, 3}
