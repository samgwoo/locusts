// Pixel-art helpers shared by the minigames: a sparse pixel grid drawn in the campus palette.

export const COLORS = { c: "#ece2c8", b: "#b0483a", k: "#281c22", g: "#68864e" };

export class Grid {
  constructor(w, h) { this.w = w; this.h = h; this.px = new Map(); }
  set(x, y, c) { if (x >= 0 && y >= 0 && x < this.w && y < this.h) this.px.set(y * this.w + x, c); }
  rect(x, y, w, h, c) { for (let j = 0; j < h; j++) for (let i = 0; i < w; i++) this.set(x + i, y + j, c); }
  ellipse(cx, cy, rx, ry, fill, outline) {
    for (let y = -ry; y <= ry; y++) for (let x = -rx; x <= rx; x++) {
      const d = (x * x) / (rx * rx) + (y * y) / (ry * ry);
      if (d <= 1) this.set(cx + x, cy + y, outline && d > 0.72 ? outline : fill);
    }
  }
  line(x0, y0, x1, y1, c) {
    const dx = Math.abs(x1 - x0), dy = -Math.abs(y1 - y0), sx = x0 < x1 ? 1 : -1, sy = y0 < y1 ? 1 : -1;
    let err = dx + dy;
    for (;;) {
      this.set(x0, y0, c);
      if (x0 === x1 && y0 === y1) return;
      const e2 = 2 * err;
      if (e2 >= dy) { err += dy; x0 += sx; }
      if (e2 <= dx) { err += dx; y0 += sy; }
    }
  }
  map(rows, ox = 0, oy = 0, scale = 1, color = null) {
    rows.forEach((row, y) => [...row].forEach((ch, x) => {
      if (ch !== ".") this.rect(ox + x * scale, oy + y * scale, scale, scale, color ?? ch);
    }));
  }
  canvas(flip = false) {
    const out = new OffscreenCanvas(this.w, this.h);
    const g = out.getContext("2d");
    for (const [i, c] of this.px) {
      const x = i % this.w, y = Math.floor(i / this.w);
      g.fillStyle = COLORS[c];
      g.fillRect(flip ? this.w - 1 - x : x, y, 1, 1);
    }
    return out;
  }
}

export function fromMap(rows) {
  const g = new Grid(rows[0].length, rows.length);
  g.map(rows);
  return g.canvas();
}

export const PET_BODY = [
  "..k......k..",
  "...k....k...",
  "....kkkk....",
  "...kggggk...",
  "...kkggkk...",
  "...kggggk...",
  "..kkggggkk..",
  ".kckggggkck.",
  ".kckggggkck.",
  ".kckggggkck.",
  ".kckggggkck.",
  ".kckggggkck.",
  "..kkggggkk..",
];

// The pet seen from above, in two walking frames.
export const PET = [
  fromMap([...PET_BODY, "..k.kggk.k..", ".k...kk...k.", "k..........k"]),
  fromMap([...PET_BODY, "..k.kggk.k..", "..k..kk..k..", "..k......k.."]),
];
