import hashlib
import json
import struct

import pytest

from locusts import campus_map
from locusts.config import DEFAULT_CONFIG as C
from locusts.houses import houses
from locusts.upgrades import load_all


PLACES = [b.map_name for b in C.buildings.values()] + [h.map_name for h in houses().values()]


def test_every_game_building_and_house_is_on_the_map():
    campus = campus_map.load()
    missing = [name for name in PLACES if name not in campus.anchors]
    assert not missing, f"not in buildings.json: {missing}"
    assert C.default_house in houses()


def test_shapes_cover_their_anchors():
    campus = campus_map.load()
    for name in PLACES:
        roof, *_ = campus.shapes[name]
        xs, ys = zip(*roof)
        x, y = campus.anchors[name]
        assert min(xs) <= x <= max(xs) and min(ys) <= y <= max(ys), name


def test_building_shape_adds_a_wall_per_edge():
    foot = [(0, 10), (4, 10), (4, 14), (0, 14)]
    roof = [(x, y - 8) for x, y in foot]
    roof_, foot_, *walls = campus_map.building_shape(roof, foot)
    assert (roof_, foot_) == (roof, foot)
    assert len(walls) == 4
    assert walls[0] == [(0, 10), (4, 10), (4, 2), (0, 2)]
    with pytest.raises(ValueError):
        campus_map.building_shape(roof[:3], foot)


def test_every_overlay_was_rendered_with_the_installed_map():
    """render_campus.py writes campus.png and its overlays together. Copying a re-rendered campus.png without the
    rest leaves overlays drawing the old map back over the new one wherever they show, like old trees appearing
    over a new building whenever a car drives past."""
    map_dir = campus_map.MAP_DIR
    stamp = hashlib.sha1((map_dir / "campus.png").read_bytes()).hexdigest()[:12]
    for name in ("breeze.json", "traffic.json", "lights.json"):
        assert json.loads((map_dir / name).read_text()).get("map") == stamp, f"{name} is from another render"


def test_the_breeze_overlay_matches_the_map():
    """render_campus.py writes them together, so a map re-rendered without its breeze would sway the old trees."""
    map_dir = campus_map.MAP_DIR
    campus = campus_map.load()
    breeze = json.loads((map_dir / "breeze.json").read_text())
    assert breeze["levels"] >= 2
    for level in range(1, breeze["levels"] + 1):
        header = (map_dir / f"breeze{level}.png").read_bytes()[:24]
        assert struct.unpack(">II", header[16:24]) == (campus.width, campus.height)
    trees = breeze["trees"]
    assert trees
    for x, y, w, h in trees:
        assert 0 <= x and 0 <= y and x + w <= campus.width and y + h <= campus.height


def test_the_night_lights_match_the_map():
    map_dir = campus_map.MAP_DIR
    campus = campus_map.load()
    lamps = json.loads((map_dir / "lights.json").read_text())["lamps"]
    assert lamps and all(0 <= x < campus.width and 0 <= y < campus.height for x, y in lamps)
    # windows.bin: per window a box (4 little-endian uint16s) then its pixel mask, one bit per pixel.
    raw = (map_dir / "windows.bin").read_bytes()
    offset, windows = 0, 0
    while offset < len(raw):
        x, y, w, h = struct.unpack_from("<4H", raw, offset)
        assert w > 0 and h > 0 and x + w <= campus.width and y + h <= campus.height
        offset += 8 + (w * h + 7) // 8
        windows += 1
    assert offset == len(raw) and windows > 1000


def test_anchors_are_inside_the_image():
    campus = campus_map.load()
    for name, (x, y) in campus.anchors.items():
        assert 0 <= x < campus.width and 0 <= y < campus.height, name


def test_every_career_path_trains_somewhere_on_the_map():
    campus = campus_map.load()
    for career in load_all().values():
        for path in career.paths.values():
            for building in path.buildings:
                assert C.buildings[building].map_name in campus.anchors
