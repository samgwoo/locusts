"""Redraw the digit 5 in Pixelify Sans, whose stock 5 has the S's rounded top and reads as an S.

The new 5 is built from the font's own S at both ends of the weight axis, so it keeps the S's bottom half
(bowl and lower-left hook) and swaps its top half for a flat, full-width bar and a straight left stem. The
regular outline goes in glyf and the bold one in gvar as deltas, so every weight stays a proper 5. Rebuilding
from the S each time makes rerunning the script harmless.

Usage: uv run --with fonttools tools/fix_pixelify_five.py locusts/static/fonts/pixelify-sans/PixelifySans.ttf
"""

import argparse

from fontTools.pens.recordingPen import RecordingPen
from fontTools.ttLib import TTFont
from fontTools.ttLib.tables.ttProgram import Program
from fontTools.ttLib.tables.TupleVariation import TupleVariation
from fontTools.varLib.instancer import instantiateVariableFont

# Each point of the 5, as (index of the S point to take x from, index of the S point to take y from).
# The S runs: bottom bar, lower-left hook, right bowl, middle bar, upper-left stem, top bar, upper-right hook.
FIVE_FROM_S = [
    (0, 0), (1, 1), (2, 2), (3, 3), (4, 4), (5, 5), (6, 6), (7, 7),  # bottom bar, hook, bowl's inner edge
    (10, 8), (10, 13),  # middle bar runs left into a stem that goes straight up the outer edge
    (16, 13), (16, 20), (21, 20),  # flat top bar across the whole width, with square corners
    (21, 21), (22, 22), (23, 23), (24, 24), (25, 25), (26, 26), (27, 27),  # middle bar to the bowl's outer edge
]


def s_points(font: TTFont) -> list[tuple[float, float]]:
    pen = RecordingPen()
    font.getGlyphSet()["S"].draw(pen)
    points = [args[0] for op, args in pen.value if op in ("moveTo", "lineTo")]
    if len(points) != 28 or any(op not in ("moveTo", "lineTo", "closePath") for op, _ in pen.value):
        raise SystemExit("this Pixelify Sans has a different S than the one this script was written for")
    return points


def fix(path: str) -> None:
    font = TTFont(path)
    regular = s_points(font)
    bold_font = instantiateVariableFont(TTFont(path), {"wght": font["fvar"].axes[0].maxValue})
    bold = s_points(bold_font)
    five = [(regular[x][0], regular[y][1]) for x, y in FIVE_FROM_S]
    five_bold = [(bold[x][0], bold[y][1]) for x, y in FIVE_FROM_S]

    glyph = font["glyf"]["five"]
    old_phantoms = font["gvar"].variations["five"][0].coordinates[-4:]
    glyph.coordinates = type(glyph.coordinates)([(round(x), round(y)) for x, y in five])
    glyph.endPtsOfContours = [len(five) - 1]
    glyph.flags = bytearray([1] * len(five))
    glyph.numberOfContours = 1
    glyph.program = Program()
    glyph.program.fromBytecode(b"")
    glyph.recalcBounds(font["glyf"])

    deltas = [(round(bx - x), round(by - y)) for (x, y), (bx, by) in zip(five, five_bold)]
    axis = font["fvar"].axes[0].axisTag
    font["gvar"].variations["five"] = [TupleVariation({axis: (0.0, 1.0, 1.0)}, deltas + list(old_phantoms))]
    advance, _ = font["hmtx"]["five"]
    font["hmtx"]["five"] = (advance, glyph.xMin)
    font.save(path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("font")
    fix(parser.parse_args().font)
