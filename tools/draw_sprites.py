"""Draw the locust sprite sheet and HUD icons in the campus map's four-color palette.

The locust is a student standing on its hind legs, about as tall as two cars are long (45 px at the map's
5 px per meter), facing right; the client mirrors it to face left. The body is a single green with black and
white detailing, and brick red is kept for the things it carries. Each frame is built from layers (far limbs,
abdomen, backpack, torso, near leg, head, props, near arm), and every filled layer gets a 1 px ink outline so
parts read as separate shapes, like the map.

Usage: uv run --group map tools/draw_sprites.py locusts/static/sprites [--preview preview.png]
"""

import argparse
import json
import math
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

# Same colors as tools/render_campus.py. Index 0 is transparent.
PALETTE = [(236, 226, 200), (176, 72, 58), (40, 28, 34), (104, 134, 78)]
CLEAR, CREAM, BRICK, INK, LEAF = range(5)
CHARS = {".": CLEAR, "c": CREAM, "b": BRICK, "k": INK, "g": LEAF}

FRAME_W, FRAME_H = 48, 48
# The map point the locust stands on: between its feet, one row under them.
ANCHOR = (24, 46)
GROUND = 45

Point = tuple[float, float]


class Layer:
    def __init__(self):
        self.a = np.zeros((FRAME_H, FRAME_W), np.uint8)

    def _mask(self, draw) -> np.ndarray:
        im = Image.new("1", (FRAME_W, FRAME_H), 0)
        draw(ImageDraw.Draw(im))
        return np.array(im, bool)

    def poly(self, pts, color):
        self.a[self._mask(lambda d: d.polygon([tuple(map(round, p)) for p in pts], fill=1))] = color

    def ellipse(self, box, color):
        self.a[self._mask(lambda d: d.ellipse(box, fill=1))] = color

    def line(self, pts, color, width=1):
        self.a[self._mask(lambda d: d.line([tuple(map(round, p)) for p in pts], fill=1, width=width))] = color

    def px(self, x, y, color):
        if 0 <= x < FRAME_W and 0 <= y < FRAME_H:
            self.a[y, x] = color

    def glyph(self, rows, x, y):
        for j, row in enumerate(rows):
            for i, ch in enumerate(row):
                if ch != ".":
                    self.px(x + i, y + j, CHARS[ch])


def stamp(frame: np.ndarray, layer: Layer, outline=True):
    filled = layer.a > 0
    if outline:
        grown = filled.copy()
        grown[1:] |= filled[:-1]
        grown[:-1] |= filled[1:]
        grown[:, 1:] |= filled[:, :-1]
        grown[:, :-1] |= filled[:, 1:]
        frame[grown & ~filled] = INK
    frame[filled] = layer.a[filled]


Leg = tuple[Point, Point, Point]                 # knee, ankle, toe, in frame coordinates
Arm = tuple[Point, Point]                        # elbow, hand, in body coordinates
Antenna = tuple[Point, Point]                    # bend, tip, in head coordinates


@dataclass(frozen=True)
class Pose:
    dy: int = 0                                  # body offset, positive is down; legs stay planted
    head: Point = (0, 0)                         # head offset from the body
    near_leg: Leg = ((28, 38), (25, GROUND), (29, GROUND))
    far_leg: Leg = ((24, 38), (20, GROUND), (24, GROUND))
    near_arm: Arm = ((28, 28), (31, 25))
    far_arm: Arm = ((25, 28), (28, 27))
    antennae: tuple[Antenna, Antenna] = (((22, 3), (16, 2)), ((27, 2), (22, 1)))
    eye: str = "open"                            # open, closed, tired
    wings: bool = False
    pack: str = "worn"                           # worn, or set down on the ground beside it
    props: tuple = ()                            # drawn after the head, under the near arm
    wear: tuple = ()                             # shop item ids it has on (see WEARABLES)


def draw_frame(p: Pose) -> np.ndarray:
    frame = np.zeros((FRAME_H, FRAME_W), np.uint8)
    dy = p.dy
    hx, hy = p.head[0], p.head[1] + dy

    def b(x, y):  # a point on the body
        return (x, y + dy)

    def h(x, y):  # a point on the head
        return (x + hx, y + hy)

    # Far limbs are solid ink silhouettes, so the near ones read in front.
    far = Layer()
    knee, ankle, toe = p.far_leg
    far.line([b(21, 32), knee], INK, 3)
    far.line([knee, ankle], INK, 2)
    far.line([ankle, toe], INK)
    far.line([b(23, 23), b(*p.far_arm[0]), b(*p.far_arm[1])], INK, 2)
    stamp(frame, far, outline=False)

    if p.pack == "ground":
        _backpack(frame, [(6, 35), (14, 34), (15, GROUND), (7, GROUND)])

    if p.wings:
        wing = Layer()
        root = b(20, 22)
        tips = [b(13, 9), b(6, 11), b(3, 18), b(5, 25)]
        wing.poly([root, *tips], CREAM)
        for t in tips[:-1]:
            wing.line([root, t], INK)
        stamp(frame, wing)

    abdomen = Layer()
    abdomen.poly([b(22, 27), b(16, 30), b(13, 36), b(15, 38), b(20, 35), b(23, 32)], LEAF)
    for i in range(3):
        abdomen.line([b(15 + i * 2, 36 - i * 2), b(17 + i * 2, 37 - i * 2)], CREAM)
    stamp(frame, abdomen)

    if p.pack == "worn":
        _backpack(frame, [b(12, 18), b(20, 17), b(21, 31), b(13, 32)])

    torso = Layer()
    torso.poly([b(20, 19), b(28, 19), b(30, 24), b(29, 31), b(21, 31), b(19, 25)], LEAF)
    for y in (23, 26, 29):
        torso.line([b(25, y), b(28, y)], INK)
    torso.line([b(21, 20), b(27, 20)], CREAM)
    stamp(frame, torso)
    for item in p.wear:
        if WEARABLES[item][0] in ("shirt", "outer"):
            WEARABLES[item][1](frame, b)

    # Near hind leg: a thick thigh with black chevrons, then the shin.
    knee, ankle, toe = p.near_leg
    hip = b(24, 32)
    shin = Layer()
    shin.line([knee, ankle], LEAF, 2)
    stamp(frame, shin)
    thigh = Layer()
    thigh.poly(_drumstick(hip, knee), LEAF)
    for t in (0.35, 0.65):
        cx, cy = _lerp(hip, knee, t)
        thigh.line([(cx - 2, cy), (cx, cy - 1), (cx + 2, cy)], INK)
    stamp(frame, thigh)
    foot = Layer()
    foot.line([ankle, toe], INK)
    stamp(frame, foot, outline=False)

    antennae = Layer()
    tips = Layer()
    for base, (bend, tip) in zip(((25, 8), (28, 7)), p.antennae):
        antennae.line([h(*base), h(*bend), h(*tip)], INK)
        tips.px(*map(round, h(*tip)), CREAM)
    stamp(frame, antennae, outline=False)
    stamp(frame, tips)

    head = Layer()
    head.ellipse((*h(21, 6), *h(34, 19)), LEAF)
    head.px(*h(33, 15), INK)
    head.px(*h(33, 16), INK)
    head.line([h(23, 10), h(24, 8), h(26, 7)], CREAM)
    head.line([h(23, 17), h(27, 18)], INK)
    stamp(frame, head)

    eye = Layer()
    if p.eye == "closed":
        eye.line([h(27, 11), h(31, 11)], INK)
        eye.line([h(28, 12), h(30, 12)], INK)
    else:
        eye.glyph([".kk.", "kckk", "kkkk", "kkkk", ".kk."], *h(27, 9))
        if p.eye == "tired":
            eye.glyph(["gggg", "kkkk"], *h(27, 9))
    stamp(frame, eye, outline=False)
    for item in p.wear:
        if WEARABLES[item][0] in ("head", "ears", "eyes"):
            WEARABLES[item][1](frame, h)

    for prop in p.props:
        prop(frame, p)

    # Sleeves: the near arm takes the color of whatever's worn over it.
    sleeve = next((WEARABLES[i][2] for i in reversed(p.wear) if WEARABLES[i][2] is not None), LEAF)
    arm = Layer()
    arm.line([b(27, 22), b(*p.near_arm[0]), b(*p.near_arm[1])], sleeve, 2)
    stamp(frame, arm)
    return frame


def _lerp(a: Point, b: Point, t: float) -> Point:
    return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t


def _drumstick(hip: Point, knee: Point) -> list[Point]:
    dx, dy = knee[0] - hip[0], knee[1] - hip[1]
    n = math.hypot(dx, dy)
    px, py = -dy / n, dx / n
    pts = [(0.0, 3.0), (0.3, 3.2), (0.75, 1.8), (1.0, 1.0)]
    side = [(hip[0] + dx * t + px * w, hip[1] + dy * t + py * w) for t, w in pts]
    other = [(hip[0] + dx * t - px * w, hip[1] + dy * t - py * w) for t, w in reversed(pts)]
    return side + other


def _backpack(frame, corners):
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = corners
    pack = Layer()
    pack.poly(corners, BRICK)
    pack.line([(x0 + 1, y0 + 3), (x1, y1 + 3)], CREAM)
    pack.poly([(x0 + 2, y0 + 7), (x1 - 1, y1 + 8), (x2 - 2, y2 - 3), (x3 + 1, y3 - 3)], CREAM)
    pack.line([(x0 + 3, y0 + 9), (x1 - 2, y1 + 10)], BRICK)
    stamp(frame, pack)


# --- Props: drawn with the pose so they can follow the hands. ---

def coffee(frame, p: Pose):
    x, y = p.near_arm[1]
    x, y = round(x), round(y + p.dy - 6)
    cup = Layer()
    cup.poly([(x, y), (x + 4, y), (x + 3, y + 7), (x + 1, y + 7)], CREAM)
    cup.line([(x, y + 3), (x + 4, y + 3)], BRICK)
    cup.line([(x, y + 4), (x + 4, y + 4)], BRICK)
    stamp(frame, cup)
    lid = Layer()
    lid.line([(x - 1, y - 1), (x + 5, y - 1)], INK)
    stamp(frame, lid, outline=False)


def steam(step: int):
    def draw(frame, p: Pose):
        x, y = p.near_arm[1]
        x, y = round(x), round(y + p.dy - 6)
        puff = Layer()
        puff.px(x + 1 + step % 2, y - 4, CREAM)
        puff.px(x + 2 - step % 2, y - 6, CREAM)
        stamp(frame, puff, outline=False)
    return draw


def laptop(frame_index: int):
    def draw(frame, p: Pose):
        lid = Layer()
        lid.poly([(35, 34), (38, 34), (41, 25), (38, 25)], CREAM)
        lid.line([(36, 33), (39, 26)], INK)
        lid.px(39, 30, BRICK)
        lid.px(39, 29, BRICK)
        stamp(frame, lid)
        base = Layer()
        base.poly([(27, 35), (38, 34), (38, 36), (27, 37)], CREAM)
        stamp(frame, base)
        keys = Layer()
        for i, x in enumerate(range(29, 38, 2)):
            if (i + frame_index) % 3:
                keys.px(x, 35, INK)
        stamp(frame, keys, outline=False)
    return draw


def broom(sweep: int, dust: bool):
    def draw(frame, p: Pose):
        x = 36 + sweep
        stick = Layer()
        stick.line([(27, 17 + p.dy), (x, 40)], BRICK)
        stamp(frame, stick)
        bristles = Layer()
        bristles.poly([(x - 2, 40), (x + 3, 40), (x + 5, GROUND), (x - 4, GROUND)], CREAM)
        bristles.line([(x - 1, 41), (x - 2, GROUND - 1)], INK)
        bristles.line([(x + 2, 41), (x + 3, GROUND - 1)], INK)
        stamp(frame, bristles)
        if dust:
            puff = Layer()
            for dx, dy in ((7, -2), (9, -4), (6, -6)):
                puff.px(x + dx, GROUND + dy, CREAM)
            stamp(frame, puff)
    return draw


Z_SMALL = ["ccc", ".c.", "ccc"]
Z_BIG = ["cccc", "..c.", ".c..", "cccc"]


def zzz(step: int):
    def draw(frame, p: Pose):
        z = Layer()
        z.glyph(Z_SMALL, 36 + step, 13 - step)
        z.glyph(Z_BIG, 40 + step, 6 - step)
        stamp(frame, z)
    return draw


def headband(frame, p: Pose):
    hx, hy = p.head[0], p.head[1] + p.dy
    band = Layer()
    band.line([(23 + hx, 8 + hy), (32 + hx, 7 + hy)], BRICK)
    stamp(frame, band, outline=False)


# A droplet flung off the back of a running locust's head, one per cycle: it leaves the head, flies back and up
# under the antennae, and falls away. Every frame shows it somewhere new, so it reads as moving, with a short
# trail back toward where it just was.
ARC = [(19, 12), (14, 9), (9, 10), (5, 15)]


def flung(step: int):
    def draw(frame, p: Pose):
        hx, hy = p.head[0], p.head[1] + p.dy
        x, y = ARC[step][0] + hx, ARC[step][1] + hy
        drop = Layer()
        drop.glyph([".c", "cc", "cc"], x, y)
        stamp(frame, drop)
        if step:
            px, py = ARC[step - 1][0] + hx, ARC[step - 1][1] + hy
            trail = Layer()
            for t in (0.45, 0.7):
                trail.px(round(px + (x - px) * t) + 1, round(py + (y - py) * t) + 1, INK)
            stamp(frame, trail, outline=False)
    return draw


def sweat(step: int):
    def draw(frame, p: Pose):
        drop = Layer()
        drop.glyph([".c.", "ccc", "ccc", ".c."], 36, 6 + p.dy + step)
        stamp(frame, drop)
    return draw


# --- Wearables: shop items drawn on the pet, each `fn(frame, at)` with `at` the body or head point function. ---

def hoodie(frame, b):
    hood = Layer()
    hood.ellipse((*b(15, 14), *b(23, 22)), BRICK)            # bunched behind the head
    stamp(frame, hood)
    body = Layer()
    body.poly([b(19, 19), b(29, 19), b(31, 24), b(30, 32), b(20, 32), b(18, 25)], BRICK)
    body.line([b(21, 28), b(28, 28)], INK)                   # the front pocket
    for x in (24, 26):                                       # drawstrings
        body.line([b(x, 20), b(x, 22)], CREAM)
    stamp(frame, body)


def vest(frame, b):
    body = Layer()
    body.poly([b(19, 19), b(29, 19), b(31, 24), b(30, 31), b(20, 31), b(18, 25)], INK)
    body.line([b(25, 20), b(25, 30)], CREAM)                 # zip
    for y in (23, 27):                                       # quilting
        body.line([b(20, y), b(29, y)], LEAF)
    body.px(*b(27, 21), CREAM)                               # the logo
    body.px(*b(28, 21), BRICK)
    stamp(frame, body)


def parka(frame, b):
    trim = Layer()
    trim.ellipse((*b(14, 13), *b(23, 21)), CREAM)            # the fur-trimmed hood, down
    stamp(frame, trim)
    body = Layer()
    body.poly([b(18, 18), b(30, 18), b(32, 24), b(31, 33), b(19, 33), b(17, 25)], CREAM)
    for y in (22, 26, 30):                                   # puffy baffles
        body.line([b(18, y), b(31, y)], INK)
    body.ellipse((*b(20, 19), *b(23, 21)), BRICK)            # the arm patch
    stamp(frame, body)


def cap(frame, h):
    crown = Layer()
    crown.poly([h(21, 8), h(22, 5), h(25, 3), h(29, 3), h(32, 5), h(33, 8)], BRICK)
    crown.poly([h(31, 7), h(37, 7), h(37, 8), h(31, 9)], BRICK)          # the brim, out front
    crown.px(*h(27, 3), CREAM)
    stamp(frame, crown)


def headphones(frame, h):
    band = Layer()
    band.line([h(22, 10), h(23, 5), h(27, 3), h(31, 5), h(32, 7)], INK, 2)
    stamp(frame, band, outline=False)
    cup = Layer()
    cup.ellipse((*h(21, 9), *h(25, 15)), BRICK)
    stamp(frame, cup)


def sunglasses(frame, h):
    shades = Layer()
    shades.line([h(22, 10), h(26, 10)], INK)                 # the arm, back to the ear
    shades.poly([h(26, 9), h(32, 9), h(31, 12), h(27, 12)], INK)
    stamp(frame, shades, outline=False)
    glint = Layer()
    glint.px(*h(30, 10), CREAM)
    stamp(frame, glint, outline=False)


# id: (slot, draw, sleeve color or None), for the ids in locusts/items.toml with `wear`. Slots draw in
# locusts/shop.py's WEAR_SLOTS order.
WEARABLES = {
    "hoodie": ("shirt", hoodie, BRICK),
    "vest": ("outer", vest, None),
    "parka": ("outer", parka, CREAM),
    "cap": ("head", cap, None),
    "headphones": ("ears", headphones, None),
    "sunglasses": ("eyes", sunglasses, None),
}


# --- Animations ---

STAND = Pose(props=(coffee,))
SIP = replace(STAND, near_arm=((30, 24), (32, 17)), head=(0, -1))
CROUCH = replace(STAND, dy=3, near_leg=((30, 40), (25, GROUND), (29, GROUND)),
                 far_leg=((26, 40), (20, GROUND), (24, GROUND)), near_arm=((27, 29), (30, 29)),
                 antennae=(((22, 4), (16, 4)), ((27, 3), (21, 3))))
SIT = Pose(dy=7, head=(1, 1), near_leg=((31, 36), (32, 44), (35, GROUND)),
           far_leg=((29, 37), (29, 44), (32, GROUND)), far_arm=((26, 28), (30, 28)), pack="ground")
SWEEP = Pose(head=(1, 1), near_arm=((28, 27), (31, 28)), far_arm=((25, 26), (29, 24)))
DROOP = (((21, 6), (16, 10)), ((26, 5), (21, 9)))
ASLEEP = replace(SIT, head=(1, 3), eye="closed", antennae=DROOP,
                 near_arm=((27, 29), (31, 31)), far_arm=((25, 29), (29, 31)))
TIRED = replace(STAND, dy=2, head=(1, 2), eye="tired", antennae=DROOP, near_arm=((27, 29), (29, 32)))
# Running in place at the gym: high knees and pumping arms, bag set down beside it.
JOG = Pose(pack="ground", antennae=(((21, 4), (15, 5)), ((26, 3), (20, 4))))
NEAR_UP = ((33, 32), (32, 39), (35, 39))
FAR_UP = ((30, 32), (28, 39), (31, 39))
NEAR_DOWN = ((27, 38), (25, GROUND), (29, GROUND))
FAR_DOWN = ((24, 38), (20, GROUND), (24, GROUND))
ARM_FWD, ARM_BACK = ((31, 25), (34, 20)), ((24, 26), (21, 29))

ANIMATIONS: dict[str, list[tuple[Pose, int]]] = {
    "idle": [
        (replace(STAND, props=(coffee, steam(0))), 700),
        (replace(STAND, eye="closed", props=(coffee, steam(1))), 120),
        (replace(STAND, dy=1, props=(coffee, steam(0))), 600),
        (SIP, 600),
        (replace(STAND, antennae=(((22, 2), (17, 0)), ((27, 1), (23, 0))), props=(coffee, steam(1))), 400),
    ],
    "hop": [
        (CROUCH, 90),
        (replace(STAND, dy=-3, near_leg=((25, 37), (21, 42), (18, 44)), far_leg=((22, 37), (18, 42), (15, 43)),
                 near_arm=((30, 19), (32, 15)), far_arm=((26, 19), (28, 15)),
                 antennae=(((20, 4), (14, 6)), ((25, 3), (19, 4)))), 90),
        (replace(STAND, dy=-2, near_leg=((30, 34), (26, 38), (29, 39)), far_leg=((27, 35), (23, 39), (26, 40)),
                 near_arm=((30, 23), (33, 21)), far_arm=((26, 22), (29, 20)), wings=True,
                 antennae=(((20, 4), (14, 6)), ((25, 3), (19, 4)))), 120),
        (replace(CROUCH, dy=2), 120),
    ],
    "train": [
        (replace(SIT, near_arm=((29, 27), (33, 28)), props=(laptop(0),)), 160),
        (replace(SIT, dy=8, near_arm=((29, 26), (35, 27)), props=(laptop(1),)), 160),
        (replace(SIT, near_arm=((29, 27), (33, 28)), props=(laptop(2),)), 160),
        (replace(SIT, dy=8, near_arm=((29, 26), (35, 27)), props=(laptop(3),)), 160),
    ],
    "task": [
        (replace(SWEEP, props=(broom(0, False),)), 180),
        (replace(SWEEP, dy=1, props=(broom(2, False),)), 180),
        (replace(SWEEP, props=(broom(4, True),)), 180),
        (replace(SWEEP, dy=1, props=(broom(2, False),)), 180),
    ],
    "sleep": [
        (replace(ASLEEP, props=(zzz(0),)), 700),
        (replace(ASLEEP, dy=8, props=(zzz(1),)), 700),
        (replace(ASLEEP, dy=8, props=(zzz(2),)), 700),
        (replace(ASLEEP, props=(zzz(3),)), 700),
    ],
    "tired": [
        (replace(TIRED, props=(coffee, sweat(0))), 500),
        (replace(TIRED, dy=3, props=(coffee, sweat(2))), 500),
    ],
    "workout": [
        (replace(JOG, dy=-2, near_leg=NEAR_UP, far_leg=FAR_DOWN, near_arm=ARM_BACK, far_arm=((27, 25), (30, 21)),
                 props=(headband, flung(0))), 140),
        (replace(JOG, dy=0, near_leg=NEAR_DOWN, far_leg=FAR_DOWN, near_arm=((28, 28), (30, 26)),
                 props=(headband, flung(1))), 110),
        (replace(JOG, dy=-2, near_leg=NEAR_DOWN, far_leg=FAR_UP, near_arm=ARM_FWD, far_arm=((23, 27), (21, 30)),
                 props=(headband, flung(2))), 140),
        (replace(JOG, dy=0, near_leg=NEAR_DOWN, far_leg=FAR_DOWN, near_arm=((28, 28), (30, 26)),
                 props=(headband, flung(3))), 110),
    ],
}


def shadow() -> np.ndarray:
    frame = np.zeros((FRAME_H, FRAME_W), np.uint8)
    cx, cy = ANCHOR
    for y in range(-2, 3):
        for x in range(-11, 12):
            if (x / 11) ** 2 + (y / 2.4) ** 2 <= 1 and (x + y) % 2 == 0:
                frame[cy + y - 1, cx + x] = INK
    return frame


# --- HUD icons: fill maps that get an ink outline, centered in 12x12 cells. ---

ICON = 12
ICONS = {
    "food": ["....k.....", "....kgg...", "..gg.gg...", ".gggggggg.", ".gcgggggg.", ".gcgggggg.", ".gggggggg.",
             "..gggggg..", "...gggg..."],
    "energy": [".....bbb", "....bbb.", "...bbb..", "..bbbbbb", ".....bb.", "....bb..", "...bb...", "..b....."],
    "coin": ["...bbbb...", "..bbbbbb..", ".bbbccbbb.", ".bbcbbbbb.", ".bbbccbbb.", ".bbbbbcbb.", ".bbbccbbb.",
             "..bbbbbb..", "...bbbb..."],
    "coffee": ["..c..c...", "...c..c..", "..c..c...", "bbbbbbb..", "cccccccc.", "ccccccc.c", "ccccccc.c",
               "cccccccc.", ".ccccc..."],
    "moon": ["...ccc..", "..ccc...", ".ccc....", ".ccc....", ".ccc....", ".cccc...", "..ccccc.", "...cccc."],
    "sun": ["....b....", ".b.....b.", "...bbb...", "..bbbbb..", "b.bbbbb.b", "..bbbbb..", "...bbb...",
            ".b.....b.", "....b...."],
    "sleep": ["ccccc....", "...c.....", "..c......", ".c.......", "ccccc.ccc", ".......c.", "......ccc"],
    "train": [".gggggggg.", ".gccccccg.", ".gcbbcccg.", ".gcccbbcg.", ".gccccccg.", ".gggggggg.",
              "cccccccccc"],
    "task": [".......b", "......b.", ".....b..", "....b...", "...b....", "..ccc...", ".ccccc..", "cc.c.cc.",
             "c.c.c.c."],
    "home": ["....bb....", "...bbbb...", "..bbbbbb..", ".bbbbbbbb.", "..cccccc..", "..ccggcc..", "..ccggcc..",
             "..ccggcc.."],
    "upgrade": ["....b....", "....b....", "...bbb...", "bbbbbbbbb", ".bbbbbbb.", "..bbbbb..", "..bb.bb..",
                ".bb...bb."],
    "fair": ["...cccc...", "...c..c...", ".bbbbbbbb.", ".bbbbbbbb.", ".bbbccbbb.", ".bbbbbbbb.", ".bbbbbbbb."],
    "center": ["....c....", "..ccccc..", ".cc.c.cc.", ".c..c..c.", "ccccbcccc", ".c..c..c.", ".cc.c.cc.",
               "..ccccc..", "....c...."],
    "plus": ["...cc...", "...cc...", "cccccccc", "cccccccc", "...cc...", "...cc..."],
    "minus": ["cccccccc", "cccccccc"],
    "close": ["cc....cc", ".cc..cc.", "..cccc..", "...cc...", "..cccc..", ".cc..cc.", "cc....cc"],
    "warn": ["....b....", "...bbb...", "...bcb...", "..bbcbb..", "..bbcbb..", ".bbbbbbb.", ".bbbcbbb.",
             "bbbbbbbbb"],
    "fast": ["bb..bb...", ".bb..bb..", "..bb..bb.", "...bb..bb", "..bb..bb.", ".bb..bb..", "bb..bb..."],
    "swag": ["bbbbbbbbbb", "kckkbbkckk", "kkkk..kkkk", ".kk....kk."],
    "lock": ["..cccc..", ".cc..cc.", ".c....c.", ".c....c.", "bbbbbbbb", "bbbbbbbb", "bbbkkbbb", "bbbkkbbb",
             "bbbbbbbb"],
    "check": [".......gg", "......ggg", ".....ggg.", "gg..ggg..", "ggggggg..", ".ggggg...", "..ggg...."],
    "clock": ["..cccccc..", ".cccckccc.", "ccccckcccc", "ccccckcccc", "ccccckkkcc", "cccccccccc", "cccccccccc",
              ".cccccccc.", "..cccccc.."],
    "star": ["....b....", "...bbb...", "bbbbbbbbb", ".bbbbbbb.", "..bbbbb..", ".bbb.bbb.", ".bb...bb."],
    "people": ["..cc...cc.", ".cccc.cccc", ".cccc.cccc", "..cc...cc.", ".cccc.cccc", "cccccccccc", "cccccccccc"],
}

# --- Shop items: bigger fill maps, keyed by the item ids in locusts/items.toml, in 20x20 cells. ---

ITEM = 20
ITEMS = {
    "thinkpad": [
        "..kkkkkkkkkkkk..",
        "..kggggggggggk..",
        "..kgccccgggggk..",
        "..kggggggggggk..",
        "..kgcccccggggk..",
        "..kggggggggggk..",
        "..kgcccggggggk..",
        "..kkkkkkkkkkkk..",
        ".kkkkkkkkkkkkkk.",
        ".kckckckckckckk.",
        "kkckckckbkckckck",
        "kkkkkkkcckkkkkkk",
        ".kkkkkkkkkkkkkk.",
    ],
    "macbook": [
        "..cccccccccccc..",
        "..cccccccccccc..",
        "..cccccccgcccc..",
        "..ccccccgccccc..",
        "..ccccggggcccc..",
        "..ccccgggccccc..",
        "..ccccggggcccc..",
        "..cccccggccccc..",
        "..cccccccccccc..",
        "..cccccccccccc..",
        ".kkkkkkkkkkkkkk.",
        "cccccccccccccccc",
        ".cccccccccccccc.",
    ],
    "headphones": [
        "....bbbbbb....",
        "..bb......bb..",
        ".b..........b.",
        ".b..........b.",
        "b............b",
        "b............b",
        "bbbc......cbbb",
        "bkbc......cbkb",
        "bkbc......cbkb",
        "bkbc......cbkb",
        "bbbc......cbbb",
        ".bb........bb.",
    ],
    "topper": [
        "..cccc............",
        ".cccccc...........",
        ".cccccc...........",
        "gggggggggggggggggg",
        "gggcggggcggggcgggg",
        "gggggggggggggggggg",
        "gcggggcggggcggggcg",
        "gggggggggggggggggg",
        "bbbbbbbbbbbbbbbbbb",
        "b................b",
        "b................b",
    ],
    "curtains": [
        "kkkkkkkkkkkkkkkk",
        "bbbbbccccccbbbbb",
        "bkbbbckkcccbbbkb",
        "bkbbbckcckcbbbkb",
        "bkbbbckkcccbbbkb",
        "bkbbbckkkkcbbbkb",
        "bkbbbccccccbbbkb",
        "bkbbbb....bbbbkb",
        "bkbbbbb..bbbbbkb",
        "bkbbbbb..bbbbbkb",
    ],
    "fridge": [
        "cccccccccc",
        "ccccccckcc",
        "ccccccckcc",
        "cccccccccc",
        "kkkkkkkkkk",
        "cccccccccc",
        "ccccccckcc",
        "cgcbccckcc",
        "cggbccckcc",
        "cccccccccc",
        "cccccccccc",
        "cccccccccc",
        ".k......k.",
    ],
    "meal_plan": [
        "bbbbbbbbbbbbbbbb",
        "bbbbbbbbbbbbbbbb",
        "cccccccccccccccc",
        "cggggcckkkkkkkcc",
        "cgccgccccccccccc",
        "cgccgcckkkkkcccc",
        "cggggccccccccccc",
        "cccccccccccccccc",
        "kkkkkkkkkkkkkkkk",
        "cccccccccccccccc",
    ],
    "ai": [
        "..gggggggggggg..",
        ".gggggggcgggggg.",
        "gggggggcgggggggg",
        "ggggggcccggggggg",
        "ggggcccccccggggg",
        "ggggggcccggggggg",
        "gggggggcgggggggg",
        ".gggggggcgggggg.",
        "..gggggggggggg..",
        "...ggg..........",
        "..gg............",
        ".g..............",
    ],
    "indego": [                  # side on: saddle, diamond frame, handlebars, the front basket; red and ink
        "...........kkkk...",      # only, so it reads on an owned item's green tile too
        ".....kkkk...k.....",
        ".......bbbbbk.....",
        "...kkkbb...bkkk...",
        ".kk...bb..bkkkkkk.",
        ".k...b.kb.b..k..k.",
        "k....b..bb...k...k",
        "k...kbbbkk...k...k",
        "k.......kk.......k",
        ".k.....k..k.....k.",
        ".kk...kk..kk...kk.",
        "...kkk......kkk...",
    ],
    "keyboard": [
        "bbbbbbbbbbbbbbbb",
        "bcbcbcbcbcbcbcbb",
        "bbbbbbbbbbbbbbbb",
        "bbcbcbcbcbcbcbcb",
        "bbbbbbbbbbbbbbbb",
        "bcbcbccccccbcbcb",
        "bbbbbbbbbbbbbbbb",
    ],
    "ipad": [
        "kkkkkkkkkkkk...",
        "kcccccccccck...",
        "kccccgggccck...",
        "kcccgcccgcck...",
        "kccccgggccck..c",
        "kcccccccccck.cc",
        "kcccccccccckcc.",
        "kcccccccccccc..",
        "kccccccccccck..",
        "kkkkkkkkkkkk...",
    ],
    "hoodie": [
        "....bbbbbb....",
        "...bkkkkkkb...",
        "..bbbkkkkbbb..",
        ".bbbbbcbcbbbb.",
        "bbbbbbcbcbbbbb",
        "bbb.bbbbbb.bbb",
        "bbb.bbbbbb.bbb",
        "bbb.bkkkkb.bbb",
        "bbb.bbbbbb.bbb",
        "cc..bbbbbb..cc",
        "....cccccc....",
    ],
    "cap": [
        "....bbbbbb......",
        "..bbbbbbbbbb....",
        ".bbbbbbbbbbbb...",
        ".bbbbbbcbbbbbb..",
        ".kkkkkkkkkkkbbbb",
    ],
    "vest": [
        "...kk....kk...",
        "..kkkk..kkkk..",
        ".kkkkkkckkkkk.",
        ".kkkkkkckkcbk.",
        ".ggggggcggggg.",
        ".kkkkkkckkkkk.",
        ".kkkkkkckkkkk.",
        ".ggggggcggggg.",
        ".kkkkkkckkkkk.",
        ".kkkkkkckkkkk.",
    ],
    "parka": [
        "...cccccccccc...",
        "..ccccckkccccc..",
        ".cbbccccccccccc.",
        ".cbbccccccccccc.",
        "kkkkkkkkkkkkkkkk",
        "cccccccccccccccc",
        "cccccccccccccccc",
        "kkkkkkkkkkkkkkkk",
        "cccccccccccccccc",
        "cccccccccccccccc",
        "kkkkkkkkkkkkkkkk",
        "ccc..cccccc..ccc",
    ],
    "sunglasses": [
        "kkkkkkkkkkkkkkkk",
        "kkkkkkk..kkkkkkk",
        "kkkckkk..kkkckkk",
        ".kkkkk....kkkkk.",
        "..kkk......kkk..",
    ],
    "espresso": [
        "bbbbbbbbbbbb",
        "bkkkkkkkkkkb",
        "bbbbbbbbbbbb",
        "bb..kkkk..bb",
        "bb...kk...bb",
        "bb...cc...bb",
        "bb..cccc..bb",
        "bb..cccc..bb",
        "bbbbbbbbbbbb",
        "bbbbbbbbbbbb",
    ],
    "blanket": [
        "gggggggggggggggg",
        "gcgcgcgcgcgcgcgc",
        "gggggggggggggggg",
        "gggggggggggggggg",
        "cccccccccccccccc",
        "gggggggggggggggg",
        "gggggggggggggggg",
        "gcgcgcgcgcgcgcgc",
        "gggggggggggggggg",
    ],
    "leds": [
        "..c...b...g...c.",
        ".ccc.bbb.ggg.ccc",
        "kkkkkkkkkkkkkkkk",
        ".ccc.bbb.ggg.ccc",
        "..c...b...g...c.",
    ],
    "scooter": [
        "..........kkk...",
        "...........k....",
        "...........k....",
        "..........k.....",
        "..........k.....",
        ".........k......",
        ".........k......",
        "bbbbbbbbbk......",
        ".kk.....kk......",
        "kckk...kckk.....",
        ".kk.....kk......",
    ],
}
# Thin line art that an outline would smear, drawn with its own ink instead.
UNOUTLINED_ITEMS = {"indego", "scooter"}


def outlined(layer: np.ndarray) -> np.ndarray:
    out = np.zeros_like(layer)
    filled = layer > 0
    grown = filled.copy()
    grown[1:] |= filled[:-1]
    grown[:-1] |= filled[1:]
    grown[:, 1:] |= filled[:, :-1]
    grown[:, :-1] |= filled[:, 1:]
    out[grown & ~filled] = INK
    out[filled] = layer[filled]
    return out


def fill_map(rows: list[str], size: int) -> np.ndarray:
    """Center a fill map in a size x size cell, leaving room for the outline."""
    w = max(len(r) for r in rows)
    if w > size - 2 or len(rows) > size - 2:
        raise ValueError(f"a {w}x{len(rows)} map doesn't fit a {size} px cell with its outline")
    layer = np.zeros((size, size), np.uint8)
    ox, oy = (size - w) // 2, (size - len(rows)) // 2
    for j, row in enumerate(rows):
        for i, ch in enumerate(row):
            layer[oy + j, ox + i] = CHARS[ch]
    return layer


def icon(rows: list[str]) -> np.ndarray:
    return outlined(fill_map(rows, ICON))


def item(name: str) -> np.ndarray:
    layer = fill_map(ITEMS[name], ITEM)
    return layer if name in UNOUTLINED_ITEMS else outlined(layer)


def to_image(indices: np.ndarray) -> Image.Image:
    rgba = np.zeros((*indices.shape, 4), np.uint8)
    for i, color in enumerate(PALETTE, start=1):
        rgba[indices == i] = (*color, 255)
    return Image.fromarray(rgba, "RGBA")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--preview", type=Path, help="also write an 8x preview on a cream background")
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    cols = max(len(frames) for frames in ANIMATIONS.values())
    rows = len(ANIMATIONS) + 1
    sheet = np.zeros((rows * FRAME_H, cols * FRAME_W), np.uint8)
    meta = {"frame": [FRAME_W, FRAME_H], "anchor": list(ANCHOR), "animations": {}}
    for row, (name, frames) in enumerate(ANIMATIONS.items()):
        for col, (pose, _) in enumerate(frames):
            sheet[row * FRAME_H:(row + 1) * FRAME_H, col * FRAME_W:(col + 1) * FRAME_W] = draw_frame(pose)
        meta["animations"][name] = {"row": row, "ms": [ms for _, ms in frames]}
    sheet[-FRAME_H:, :FRAME_W] = shadow()
    meta["shadow"] = {"row": rows - 1, "col": 0}
    to_image(sheet).save(args.out_dir / "locust.png", optimize=True)
    (args.out_dir / "locust.json").write_text(json.dumps(meta, indent=1) + "\n")

    # wear.png: for each wearable, the locust sheet's every frame drawn again with it on, keeping only the pixels
    # that changed, so the client layers it over the plain sprite. One block per wearable, stacked down.
    wear = np.zeros((len(WEARABLES) * sheet.shape[0], sheet.shape[1]), np.uint8)
    for k, item_id in enumerate(WEARABLES):
        for row, frames in enumerate(ANIMATIONS.values()):
            for col, (pose, _) in enumerate(frames):
                y, x = k * sheet.shape[0] + row * FRAME_H, col * FRAME_W
                plain = sheet[row * FRAME_H:(row + 1) * FRAME_H, x:x + FRAME_W]
                dressed = draw_frame(replace(pose, wear=(item_id,)))
                wear[y:y + FRAME_H, x:x + FRAME_W] = np.where(dressed != plain, dressed, CLEAR)
    to_image(wear).save(args.out_dir / "wear.png", optimize=True)
    (args.out_dir / "wear.json").write_text(json.dumps({"names": list(WEARABLES), "height": sheet.shape[0]}) + "\n")

    icons = np.zeros((ICON, ICON * len(ICONS)), np.uint8)
    for i, rows_ in enumerate(ICONS.values()):
        icons[:, i * ICON:(i + 1) * ICON] = icon(rows_)
    to_image(icons).save(args.out_dir / "icons.png", optimize=True)
    (args.out_dir / "icons.json").write_text(json.dumps({"size": ICON, "names": list(ICONS)}) + "\n")

    names = list(ITEMS)
    items = np.zeros((ITEM, ITEM * len(names)), np.uint8)
    for i, name in enumerate(names):
        items[:, i * ITEM:(i + 1) * ITEM] = item(name)
    to_image(items).save(args.out_dir / "items.png", optimize=True)
    (args.out_dir / "items.json").write_text(json.dumps({"size": ITEM, "names": names}) + "\n")

    if args.preview:
        width = max(sheet.shape[1], icons.shape[1], items.shape[1])
        preview = Image.new("RGBA", (width, sheet.shape[0] + ICON + ITEM + 8), PALETTE[0] + (255,))
        preview.alpha_composite(to_image(sheet))
        preview.alpha_composite(to_image(icons), (0, sheet.shape[0] + 2))
        preview.alpha_composite(to_image(items), (0, sheet.shape[0] + ICON + 6))
        preview.resize((preview.width * 6, preview.height * 6), Image.NEAREST).save(args.preview)


if __name__ == "__main__":
    main()
