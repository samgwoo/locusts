"""The day/night light in sky.mjs: untinted by day, dark with the lights on at night, and never jumping."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

SKY = Path(__file__).parent.parent / "locusts" / "static" / "campus" / "sky.mjs"
SCRIPT = f"""
import {{ Sky }} from {json.dumps(SKY.as_uri())};
const sky = Object.create(Sky.prototype);
sky.clock = {{ night_start: "23:00", night_end: "09:00", timezone: "America/New_York" }};
sky.keys = sky.keyframes();
const at = (m) => sky.light(m);
let jump = 0;
for (let m = 0; m < 1440; m++) {{
  const a = at(m), b = at((m + 1) % 1440);
  jump = Math.max(jump, ...a.tint.map((v, k) => Math.abs(v - b.tint[k])), Math.abs(a.lights - b.lights) * 255);
}}
const share = (h, m = 0) => sky.windowShare(h * 60 + m);
const windows = {{ evening: share(22, 30), midnight: share(0), two: share(2), four: share(4), seven: share(7),
  daybreak: share(9, 20), shares: Array.from({{ length: 1440 }}, (_, m) => sky.windowShare(m)) }};

const philly = Object.create(Sky.prototype);
philly.clock = sky.clock;
const sunJumpOf = (rise, set) => {{
  philly.keys = philly.keyframes(rise, set);
  let jump = 0;
  for (let m = 0; m < 1440; m++) {{
    const a = philly.light(m), b = philly.light((m + 1) % 1440);
    jump = Math.max(jump, ...a.tint.map((v, k) => Math.abs(v - b.tint[k])), Math.abs(a.lights - b.lights) * 255);
  }}
  return jump;
}};
philly.keys = philly.keyframes(6 * 60 + 30, 16 * 60 + 57);
const sun = {{
  beforeDawn: philly.light(5 * 60 + 20),
  morning: philly.light(7 * 60 + 15),
  day: philly.light(12 * 60),
  afterDusk: philly.light(17 * 60 + 40),
  jump: sunJumpOf(6 * 60 + 30, 16 * 60 + 57),
  winter: sunJumpOf(7 * 60 + 19, 16 * 60 + 38),
  summer: sunJumpOf(5 * 60 + 32, 20 * 60 + 32),
}};
philly.keys = philly.keyframes(100, 50);
const bogus = philly.light(12 * 60);

const rain = Sky.stepDrop({{ x: 10, y: 100, vx: 20, vy: 50, len: 8, sway: 0, phase: 0, snow: false }}, 0.5, 200, 300, 2);
const wrapped = Sky.stepDrop({{ x: 180, y: 290, vx: 0, vy: 40, len: 6, sway: 0, phase: 0, snow: true }}, 0.5, 200, 300, 1);
const flake = {{ sway: 10, phase: 0 }};
const precip = {{
  rain, wrapped,
  drift: Sky.drift(flake, 900 * Math.PI / 2),
  noDrift: Sky.drift({{ sway: 0, phase: 0 }}, 0),
  clear: Sky.dropCount("clear", 1, 1280, 800),
  none: Sky.dropCount("rain", 0, 1280, 800),
  fog: Sky.dropCount("fog", 0.5, 1280, 800),
  rainCount: Sky.dropCount("rain", 1, 1280, 800),
  snowCount: Sky.dropCount("snow", 1, 1280, 800),
  drizzle: Sky.dropCount("rain", 0.18, 1280, 800),
  wide: Sky.dropCount("rain", 1, 2560, 1600),
  alphaZero: Sky.cloudAlpha(0.5, 0),
  alphaFull: Sky.cloudAlpha(0.2, 1),
  alphaMid: Sky.cloudAlpha(0.05, 0.1),
  tintSame: Sky.weatherTint([255, 255, 255], "clear", 1),
  tintRain: Sky.weatherTint([255, 255, 255], "rain", 1),
  tintSnow: Sky.weatherTint([255, 255, 255], "snow", 1),
}};
console.log(JSON.stringify({{ noon: at(12 * 60), night: at(60), dusk: at(22 * 60 + 40), dawn: at(8 * 60 + 30), jump, windows, sun, bogus, precip }}));
"""

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def test_light_follows_the_campus_night():
    out = subprocess.run(["node", "--input-type=module", "-e", SCRIPT], capture_output=True, text=True, check=True)
    light = json.loads(out.stdout)
    assert light["noon"]["tint"] == [255, 255, 255] and light["noon"]["lights"] == 0
    assert max(light["night"]["tint"]) < 160 and light["night"]["lights"] == 1
    for twilight in (light["dusk"], light["dawn"]):
        assert 0 < twilight["lights"] < 1
        assert max(light["night"]["tint"]) < max(twilight["tint"]) < 255
    # Minute to minute the light changes by a few levels at most, so nobody sees it snap.
    assert light["jump"] < 6


def test_windows_go_dark_after_midnight_and_light_up_again_by_morning():
    out = subprocess.run(["node", "--input-type=module", "-e", SCRIPT], capture_output=True, text=True, check=True)
    w = json.loads(out.stdout)["windows"]
    assert w["evening"] > 0.9 and w["midnight"] > 0.9
    assert w["midnight"] > w["two"] > w["four"] < w["seven"] < w["daybreak"]
    assert w["four"] < 0.15
    assert 0.45 < w["daybreak"] <= 0.5
    shares = w["shares"]
    assert all(b <= a + 1e-9 for a, b in zip(shares[0:240], shares[1:241]))     # midnight to 4 AM: only off
    assert all(b >= a - 1e-9 for a, b in zip(shares[240:570], shares[241:571]))  # 4 AM to 9:30: only on


def test_real_sunrise_and_sunset_move_the_light_without_a_jump():
    """November in Philadelphia: dark well before the game's 11 PM night, bright by the time campus wakes."""
    sun = _light()["sun"]
    assert sun["day"]["tint"] == [255, 255, 255] and sun["day"]["lights"] == 0
    assert sun["morning"]["tint"] == [255, 255, 255] and sun["morning"]["lights"] == 0
    assert sun["beforeDawn"]["lights"] >= 0.75
    assert max(sun["afterDusk"]["tint"]) < 160 and sun["afterDusk"]["lights"] == 1
    assert sun["jump"] < 6 and sun["winter"] < 6 and sun["summer"] < 6
    # A sunrise after sunset isn't a day; the light stays on the game's night, so noon is still noon.
    assert _light()["bogus"]["tint"] == [255, 255, 255]


def test_rain_and_snow_fall_and_wrap_and_clear_skies_do_not():
    sky = _light()["precip"]
    assert sky["rain"]["x"] == 30 and sky["rain"]["y"] == 125
    assert sky["wrapped"]["y"] == -6 and sky["wrapped"]["x"] == 54
    assert sky["drift"] == pytest.approx(10) and sky["noDrift"] == 0
    assert sky["clear"] == sky["none"] == sky["fog"] == 0
    assert 0 < sky["drizzle"] < sky["rainCount"] and sky["snowCount"] < sky["rainCount"]
    assert sky["rainCount"] == 280 and sky["wide"] == 672
    assert sky["alphaZero"] == 0 and sky["alphaFull"] == 1 and sky["alphaMid"] == 0.5
    assert sky["tintSame"] == [255, 255, 255]
    assert sky["tintRain"][0] < sky["tintRain"][2] < 255
    assert sky["tintSnow"][2] < 255 and sky["tintSnow"][0] > sky["tintRain"][0]


def _light():
    out = subprocess.run(["node", "--input-type=module", "-e", SCRIPT], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)
