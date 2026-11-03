"""The browser's Hey Day Stacker simulation (sim.mjs) must score runs exactly like the server's replay()."""

import json
import random
import shutil
import subprocess
from dataclasses import asdict
from pathlib import Path

import pytest
from test_hey_day_stacker import perfectly, play

from locusts.minigames.hey_day_stacker import DEFAULT_STACKER, StackerConfig, generate, replay

SIM = Path(__file__).parent.parent / "locusts" / "static" / "stacker" / "sim.mjs"
SCRIPT = f"""
import {{ replay }} from {json.dumps(SIM.as_uri())};
let data = "";
process.stdin.on("data", (chunk) => data += chunk);
process.stdin.on("end", () => {{
  const runs = JSON.parse(data);
  console.log(JSON.stringify(runs.map(({{ course, config, inputs }}) => replay(course, inputs, config))));
}});
"""
FAST = StackerConfig(speed_start=40, speed_step=3, perfect_px=1, grow_every=2, max_hits=5)

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def run_node(runs: list[dict]) -> list[dict]:
    out = subprocess.run(["node", "--input-type=module", "-e", SCRIPT], input=json.dumps(runs),
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_browser_simulation_matches_server_replay():
    rng = random.Random(0)
    runs, expected = [], []
    for seed in range(30):
        config = FAST if seed % 3 == 0 else DEFAULT_STACKER
        course = generate(seed, seconds=66 if seed % 5 == 0 else None, config=config)
        slack = rng.randint(2, 12)
        missed = set(rng.sample(range(1, 40), seed % 3))
        variants = (
            play(course, perfectly, config),
            play(course, lambda run, m=missed: abs(run.x - run.top_x) >= run.top_w if run.spawned in m
                 else perfectly(run), config),
            play(course, lambda run, s=slack: abs(run.x - run.top_x) <= run.top_w // s, config),
            play(course, lambda run: rng.random() < 0.04, config),
            sorted(rng.sample(range(course.ticks), rng.randint(0, 300))),
            [],
        )
        for inputs in variants:
            runs.append({"course": course.to_dict(), "config": config.to_dict(), "inputs": inputs})
            expected.append(asdict(replay(course, inputs, config)))
    assert run_node(runs) == expected
    assert {r["hits"] for r in expected} >= {0, 1, 2, 3}
    assert any(r["perfects"] for r in expected) and any(r["floors"] > r["perfects"] for r in expected)
