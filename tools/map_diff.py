"""Render the campus map and show exactly what changed against the shipped one.

A full render takes about 8 seconds, so every check is a full render: this renders the current
tools/render_campus.py into a scratch dir, diffs it pixel-for-pixel against locusts/static/map,
and saves a before|after crop at 2x for every changed region, named after the buildings it touches.
Changed regions that touch no building are listed as "ground" so stray changes can't hide.

Usage: uv run --group map tools/map_diff.py [--install]
  --install   copy the new campus.png, buildings.json and every client overlay into locusts/static/map once the diff looks right
"""
import json, os, shutil, subprocess, sys
import numpy as np
from PIL import Image, ImageDraw
from scipy.ndimage import binary_dilation, find_objects, label

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHIPPED = os.path.join(ROOT, "locusts", "static", "map")
WORK = os.path.join(ROOT, "tools", ".map_diff")
NEW, CROPS = os.path.join(WORK, "new"), os.path.join(WORK, "crops")
MARGIN = 40     # px of context around each changed region
GAP = 12        # changes closer than this many px are reported as one region
RISE = 120      # px above a roof still counted as that building (towers, bulkheads, units)
HPX_PER_M = 0.85 * 5.0   # render_campus HEIGHT_K * PX_PER_M


def polys(b):
    """A building as drawn: roof, footprint, the wall between each pair of edges, and the air above
    the roof up to RISE px, where bulkheads, towers and rooftop units stand."""
    roof, foot = b["roof_px"][:-1], b["footprint_px"][:-1]
    drawn = sum(fy - ry for (_, fy), (_, ry) in zip(foot, roof)) / len(roof)    # roof_px is the base roof;
    rise = RISE + max(0, b["height_m"] * HPX_PER_M - drawn)                      # OSM parts can tower over it
    sky = [(x, y - dy) for x, y in roof for dy in (0, rise)]
    walls = [[foot[i], foot[(i + 1) % len(foot)], roof[(i + 1) % len(roof)], roof[i]] for i in range(len(foot))]
    return [roof, foot] + walls + [[roof[i], roof[(i + 1) % len(roof)], sky[2 * ((i + 1) % len(roof)) + 1], sky[2 * i + 1]]
                                   for i in range(len(roof))]


def touched(metas, here, x0, y0):
    """Names of the buildings whose drawn area overlaps the changed pixels `here` (a crop at x0, y0)."""
    h, w = here.shape
    hit = set()
    for meta in metas:
        for b in meta["buildings"]:
            ps = polys(b)
            xs = [p[0] for q in ps for p in q]; ys = [p[1] for q in ps for p in q]
            if max(xs) < x0 or min(xs) > x0 + w or max(ys) < y0 or min(ys) > y0 + h: continue
            im = Image.new("1", (w, h), 0); d = ImageDraw.Draw(im)
            for q in ps: d.polygon([(x - x0, y - y0) for x, y in q], fill=1)
            if (np.array(im) & here).any(): hit.add(b["name"] or b["id"])
    return sorted(hit)


def main(install):
    subprocess.run([sys.executable, os.path.join(ROOT, "tools", "render_campus.py"),
                    os.path.join(ROOT, "data", "map.osm"), NEW], check=True)
    before = np.array(Image.open(os.path.join(SHIPPED, "campus.png")).convert("RGB"))
    after = np.array(Image.open(os.path.join(NEW, "campus.png")).convert("RGB"))
    dy = after.shape[0] - before.shape[0]
    if before.shape[1] != after.shape[1] or dy < 0:
        sys.exit(f"map size changed: {before.shape[1]}x{before.shape[0]} -> {after.shape[1]}x{after.shape[0]}")
    if dy:   # the map grew: at the top (the crop reached further north) or at the bottom (the base hangs lower)
        top_n = (before != after[dy:]).any(axis=2).sum(); bot_n = (before != after[:before.shape[0]]).any(axis=2).sum()
        if top_n < bot_n:
            print(f"map grew {dy}px at the top; comparing the old map against the new one shifted up {dy}px")
            after = after[dy:]
        else:
            print(f"map grew {dy}px at the bottom; comparing the old map against the new one's top {before.shape[0]}px")
            after = after[:before.shape[0]]
            dy = 0
    changed = (before != after).any(axis=2)
    shutil.rmtree(CROPS, ignore_errors=True); os.makedirs(CROPS)
    print(f"{changed.sum()} pixels changed")
    if changed.any():
        old_meta = json.load(open(os.path.join(SHIPPED, "buildings.json")))
        new_meta = json.load(open(os.path.join(NEW, "buildings.json")))
        for bl in new_meta["buildings"]:
            for k in ("roof_px", "footprint_px"): bl[k] = [[x, y - dy] for x, y in bl[k]]
        regions, n = label(binary_dilation(changed, iterations=GAP))
        H, W = changed.shape
        for i, sl in enumerate(find_objects(regions), start=1):
            here = (regions[sl] == i) & changed[sl]
            hit = touched((old_meta, new_meta), here, sl[1].start, sl[0].start)
            y0, y1 = max(0, sl[0].start - MARGIN), min(H, sl[0].stop + MARGIN)
            x0, x1 = max(0, sl[1].start - MARGIN), min(W, sl[1].stop + MARGIN)
            pair = np.concatenate([before[y0:y1, x0:x1], np.full((y1 - y0, 4, 3), 255, np.uint8), after[y0:y1, x0:x1]], axis=1)
            tag = "_".join(h.replace(" ", "-").replace("/", "-") for h in hit)[:80] or "ground"
            fn = os.path.join(CROPS, f"{i:02d}_{tag}.png")
            Image.fromarray(pair).resize((pair.shape[1] * 2, pair.shape[0] * 2), Image.NEAREST).save(fn)
            print(f"  region {i}: x {x0}-{x1}, y {y0}-{y1}, {int(here.sum())} px -> {', '.join(hit) or 'ground'}")
        print(f"crops in {CROPS}")
    if install:
        for f in ("campus.png", "buildings.json", "breeze1.png", "breeze2.png", "breeze.json", "traffic.json", "cars.png",   # every overlay
                  "occluders.png", "lights.json", "windows.bin"):                                       # must match the map
            if os.path.exists(os.path.join(NEW, f)): shutil.copy(os.path.join(NEW, f), os.path.join(SHIPPED, f))
        print(f"installed into {SHIPPED}")


if __name__ == "__main__":
    main("--install" in sys.argv)
