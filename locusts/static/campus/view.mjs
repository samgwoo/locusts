import { SparseImage } from "./sparse.mjs";
import { Traffic } from "./traffic.mjs";

// The full-window campus map: panning, zooming, and finding the building under the pointer.
//
// The map is drawn into a window-sized canvas rather than scaling the 7000 px image with CSS, which browsers
// stop repainting reliably at large zooms.

const DRAG_PX = 5;
const COLORS = { cream: "#ece2c8", brick: "#b0483a", ink: "#281c22" };
// The wind: two slow sways crossing campus at different speeds and angles, so their sum never quite repeats, with
// gusts sweeping east on top, each its own strength and width. Trees near each other lean together, by the
// breeze overlays' whole pixel levels, and the lean steps every `stepMs` like the sprites do.
const WIND = {
  sways: [
    { ms: 5600, px: 1800, dx: 1, dy: 0.3, weight: 0.65 },
    { ms: 8300, px: 1300, dx: 0.6, dy: -1, weight: 0.35 },
  ],
  gustMs: 16000, gustPx: 4200, gustWidth: [0.18, 0.38], gustStrength: [0.4, 1.5],
  lagMs: 900,          // trees catch the wind up to this much later than their neighbors
  give: [0.75, 1.2],   // how far each tree bends for the same wind: some stiff, some limp
  rustle: 0.12,        // chance a tree blown past its first level flicks back for a step
  stepMs: 200,
};

// A fixed pseudo-random fraction in [0, 1) for integer `n`.
function hash(n) {
  n = Math.imul(n ^ (n >>> 16), 0x45d9f3b);
  n = Math.imul(n ^ (n >>> 16), 0x45d9f3b);
  return ((n ^ (n >>> 16)) >>> 0) / 4294967296;
}

const between = ([lo, hi], f) => lo + (hi - lo) * f;

// A breeze.json tree box with the wind's per-tree character: where its center is, how late it catches the wind
// and how much it bends.
export function breezeTree([x, y, w, h], i) {
  return { x, y, w, h, i, cx: x + w / 2, cy: y + h / 2, lag: hash(i) * WIND.lagMs, give: between(WIND.give, hash(i + 7919)) };
}

// How far the wind leans `tree` at animation step `step`: 0 (upright) up to `levels`.
export function windLean(tree, step, levels) {
  const t = step * WIND.stepMs - tree.lag;
  let sway = 0;
  for (const s of WIND.sways) {
    const p = tree.cx * s.dx + tree.cy * s.dy;
    sway += s.weight * (0.5 + 0.5 * Math.sin(2 * Math.PI * (t / s.ms - p / s.px)));
  }
  const g = t / WIND.gustMs - (tree.cx + tree.cy * 0.3) / WIND.gustPx, k = Math.floor(g), f = g - k;
  const width = between(WIND.gustWidth, hash(k * 31 + 5));
  const gust = f < width ? Math.sin((Math.PI * f) / width) * between(WIND.gustStrength, hash(k * 17 + 3)) : 0;
  let v = (sway * 0.9 + gust * 1.3) * tree.give;
  if (v >= 1.5 && hash(tree.i * 977 + step * 131) < WIND.rustle) v -= 1;
  return Math.max(0, Math.min(levels, Math.round(v)));
}

export function pointInPolygon(x, y, polygon) {
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const [xi, yi] = polygon[i], [xj, yj] = polygon[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

export class MapView {
  constructor(root, map, buildings, { onSelect, onMove }) {
    this.root = root;
    this.map = map;
    this.buildings = buildings;
    this.onSelect = onSelect;
    this.onMove = onMove;

    // Zoom is continuous, from half a device pixel per map pixel (an overview) to eight, and eases toward
    // wherever the wheel, pinch, or buttons point it (zoomTo), keeping the spot under the pointer in place.
    this.dpr = window.devicePixelRatio || 1;
    this.minZ = 0.5 / this.dpr;
    this.maxZ = 8 / this.dpr;
    this.z = 1 / this.dpr * Math.max(1, Math.round(this.dpr));
    this.zoomTo = null;
    this.outlines = new Map();
    this.tx = 0;
    this.ty = 0;
    this.target = null;
    this.follow = null;
    this.hover = null;
    this.selected = null;

    this.canvas = document.createElement("canvas");
    this.ctx = this.canvas.getContext("2d");
    this.image = Object.assign(new Image(), { src: map.image });
    this.image.onload = () => (this.dirty = true);
    // The breeze overlays: each tree's crown blown 1, then 2 pixels east, drawn over the map tree by tree.
    this.breeze = null;
    this.breezeStep = -1;
    const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
    // Cars, which stay parked if the browser asks for reduced motion.
    this.traffic = null;
    this.lastTick = performance.now();
    // The map-sized overlays are mostly empty, so each is kept as just its painted tiles (see sparse.mjs), and
    // they load one at a time so only one is ever decoded at full size.
    (async () => {
      if (map.traffic) {
        const cars = Object.assign(new Image(), { src: map.traffic.cars });
        const [data] = await Promise.all([fetch(map.traffic.data).then((r) => r.json()), cars.decode()]);
        const occluders = await SparseImage.load(map.traffic.occluders, { mask: true });
        this.traffic = new Traffic(data, cars, occluders, { still });
      }
      if (map.breeze && !still) {
        const { trees } = await fetch(map.breeze.trees).then((r) => r.json());
        const images = [];
        for (const src of map.breeze.images) images.push(await SparseImage.load(src));
        this.breeze = { images, trees: trees.map(breezeTree) };
      }
    })();
    // Sprites are positioned in screen space over the canvas.
    this.sprites = Object.assign(document.createElement("div"), { id: "sprites" });
    root.append(this.canvas, this.sprites);

    this.pointers = new Map();
    root.addEventListener("pointerdown", (e) => this.down(e));
    root.addEventListener("pointermove", (e) => this.move(e));
    root.addEventListener("pointerup", (e) => this.up(e));
    root.addEventListener("pointercancel", (e) => this.up(e, true));
    root.addEventListener("wheel", (e) => this.wheel(e), { passive: false });
    // A trackpad pinch arrives as a ctrl+wheel. Over the HUD or a panel the browser would zoom the whole page
    // instead, pushing the fixed HUD off-screen, and keep that zoom across reloads. So pinches anywhere zoom the map.
    addEventListener("wheel", (e) => { if (e.ctrlKey && !root.contains(e.target)) this.wheel(e); }, { passive: false });
    // Safari sends pinches as gesture events instead.
    for (const type of ["gesturestart", "gesturechange", "gestureend"]) {
      addEventListener(type, (e) => { if (!(window.visualViewport?.scale > 1)) e.preventDefault(); });
    }
    addEventListener("resize", () => this.resize());
    this.resize();
  }

  toMap(sx, sy) {
    return [(sx - this.tx) / this.z, (sy - this.ty) / this.z];
  }

  toScreen(x, y) {
    return [x * this.z + this.tx, y * this.z + this.ty];
  }

  // Rounds a screen coordinate to a whole device pixel.
  snap(v) {
    return Math.round(v * this.dpr) / this.dpr;
  }

  buildingAt(sx, sy) {
    const [x, y] = this.toMap(sx, sy);
    return Object.keys(this.buildings).find((id) => this.buildings[id].shape.some((p) => pointInPolygon(x, y, p))) ?? null;
  }

  highlight(kind, id) {
    if (this[kind] === id) return;
    this[kind] = id;
    this.dirty = true;
  }

  // Zoom in (positive) or out by `step` button presses' worth, easing there around screen point (sx, sy).
  zoom(step, sx = innerWidth / 2, sy = innerHeight / 2) {
    this.zoomBy(1.6 ** step, sx, sy);
  }

  zoomBy(factor, sx, sy) {
    const from = this.zoomTo?.z ?? this.z;
    this.zoomTo = { z: Math.min(this.maxZ, Math.max(this.minZ, from * factor)), sx, sy };
    if (!this.follow) this.target = null;
  }

  // Jump straight to zoom z around (sx, sy), for pinches, which already move as smoothly as the fingers do.
  setZoom(z, sx, sy) {
    const [mx, my] = this.toMap(sx, sy);
    this.z = Math.min(this.maxZ, Math.max(this.minZ, z));
    this.tx = sx - mx * this.z;
    this.ty = sy - my * this.z;
    this.apply();
  }

  // Glide so map point (x, y) sits at the center of the window.
  centerOn(x, y, { instant = false } = {}) {
    const tx = innerWidth / 2 - x * this.z, ty = innerHeight / 2 - y * this.z;
    if (instant) {
      this.tx = tx;
      this.ty = ty;
      this.target = null;
      this.apply();
    } else {
      this.target = [tx, ty];
    }
  }

  // Called every animation frame: glides toward a target, keeps a followed point centered, and redraws if needed.
  tick() {
    if (this.zoomTo) {
      // Ease in log space, so zooming in and out feel the same speed.
      const { z, sx, sy } = this.zoomTo;
      const next = Math.exp(Math.log(this.z) + (Math.log(z) - Math.log(this.z)) * 0.22);
      const done = Math.abs(Math.log(z / next)) < 0.002;
      this.setZoom(done ? z : next, sx, sy);
      if (done) this.zoomTo = null;
    }
    if (this.follow) {
      const [x, y] = this.follow();
      this.target = [innerWidth / 2 - x * this.z, innerHeight / 2 - y * this.z];
    }
    if (this.target) {
      const [tx, ty] = this.target;
      this.tx += (tx - this.tx) * 0.18;
      this.ty += (ty - this.ty) * 0.18;
      if (Math.abs(tx - this.tx) < 0.5 && Math.abs(ty - this.ty) < 0.5) {
        this.tx = tx;
        this.ty = ty;
        if (!this.follow) this.target = null;
      }
      this.apply();
    }
    const now = performance.now();
    if (this.traffic) {
      this.traffic.update((now - this.lastTick) / 1000);
      this.dirty = true;
    }
    this.lastTick = now;
    const step = Math.floor(now / WIND.stepMs);
    if (this.breeze && step !== this.breezeStep) {
      this.breezeStep = step;
      if (this.z * this.dpr >= 1) this.dirty = true;
    }
    if (this.dirty) this.draw();
  }

  // Draw in map pixels from here on: every layer shares one transform, so overlays land on exactly the same
  // device pixels as the map under them at any zoom.
  mapTransform(ctx) {
    const scale = this.z * this.dpr;
    ctx.setTransform(scale, 0, 0, scale, Math.round(this.tx * this.dpr), Math.round(this.ty * this.dpr));
    ctx.imageSmoothingEnabled = false;
  }

  drawBreeze(x0, y0, x1, y1) {
    // Below one device pixel per map pixel the sway would only shimmer.
    if (!this.breeze || this.z * this.dpr < 1) return;
    const { images, trees } = this.breeze;
    for (const tree of trees) {
      const { x, y, w, h } = tree;
      if (x > x1 || y > y1 || x + w < x0 || y + h < y0) continue;
      const level = windLean(tree, this.breezeStep, images.length);
      if (level) images[level - 1].draw(this.ctx, x, y, w, h);
    }
  }

  resize() {
    this.canvas.width = Math.round(innerWidth * this.dpr);
    this.canvas.height = Math.round(innerHeight * this.dpr);
    this.apply();
  }

  apply() {
    const w = this.map.width * this.z, h = this.map.height * this.z;
    this.tx = w <= innerWidth ? (innerWidth - w) / 2 : Math.min(0, Math.max(innerWidth - w, this.tx));
    this.ty = h <= innerHeight ? (innerHeight - h) / 2 : Math.min(0, Math.max(innerHeight - h, this.ty));
    this.tx = this.snap(this.tx);
    this.ty = this.snap(this.ty);
    this.dirty = true;
    this.onMove?.();
  }

  draw() {
    this.dirty = false;
    const { ctx } = this;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.fillStyle = COLORS.ink;
    ctx.fillRect(0, 0, this.canvas.width, this.canvas.height);
    if (!this.image.complete) return;
    // Only the visible part of the map, so huge zooms stay cheap.
    const [x0, y0] = this.toMap(0, 0), [x1, y1] = this.toMap(innerWidth, innerHeight);
    const sx = Math.max(0, Math.floor(x0)), sy = Math.max(0, Math.floor(y0));
    const sw = Math.min(this.map.width, Math.ceil(x1) + 1) - sx, sh = Math.min(this.map.height, Math.ceil(y1) + 1) - sy;
    this.mapTransform(ctx);
    if (sw > 0 && sh > 0) ctx.drawImage(this.image, sx, sy, sw, sh, sx, sy, sw, sh);
    this.traffic?.draw(ctx, this, x0, y0, x1, y1);
    this.drawBreeze(x0, y0, x1, y1);
    this.outline(this.hover !== this.selected && this.hover, COLORS.cream, [236, 226, 200, 46]);
    this.outline(this.selected, COLORS.brick, [176, 72, 58, 56]);
    // The time of day's light over all of it (sky.mjs).
    this.overlay?.(ctx, x0, y0, x1, y1);
    ctx.setTransform(1, 0, 0, 1, 0, 0);
  }

  // A building's highlight, drawn in map pixels so its edges land on the art's pixel grid: a light tint over the
  // building, a ring of `color` around it, and an ink line outside that. The ring's thickness in map pixels
  // keeps it a couple of device pixels wide at any zoom.
  outline(id, color, tint) {
    if (!id) return;
    const ring = Math.min(4, Math.max(1, Math.round(2 / (this.z * this.dpr))));
    const key = `${id}|${color}|${ring}`;
    let sprite = this.outlines.get(key);
    if (!sprite) this.outlines.set(key, (sprite = this.outlineSprite(this.buildings[id].shape, color, tint, ring)));
    this.ctx.drawImage(sprite.canvas, sprite.x, sprite.y);
  }

  outlineSprite(shape, color, tint, ring) {
    const pts = shape.flat();
    const pad = ring + 2;
    const x = Math.floor(Math.min(...pts.map((p) => p[0]))) - pad, y = Math.floor(Math.min(...pts.map((p) => p[1]))) - pad;
    const w = Math.ceil(Math.max(...pts.map((p) => p[0]))) - x + pad, h = Math.ceil(Math.max(...pts.map((p) => p[1]))) - y + pad;
    const canvas = Object.assign(document.createElement("canvas"), { width: w, height: h });
    const ctx = canvas.getContext("2d");
    // The building's footprint as hard pixels: canvas fills are antialiased, so threshold the coverage.
    for (const polygon of shape) {
      ctx.beginPath();
      for (const [px, py] of polygon) ctx.lineTo(px - x, py - y);
      ctx.closePath();
      ctx.fill();
    }
    const img = ctx.getImageData(0, 0, w, h);
    const inside = new Uint8Array(w * h);
    for (let i = 0; i < w * h; i++) inside[i] = img.data[i * 4 + 3] >= 128 ? 1 : 0;
    const grow = (mask) => {
      const out = mask.slice();
      for (let j = 1; j < h - 1; j++) {
        for (let i = 1; i < w - 1; i++) {
          const k = j * w + i;
          if (!mask[k] && (mask[k - 1] || mask[k + 1] || mask[k - w] || mask[k + w]
            || mask[k - w - 1] || mask[k - w + 1] || mask[k + w - 1] || mask[k + w + 1])) out[k] = 1;
        }
      }
      return out;
    };
    let band = inside;
    for (let r = 0; r < ring; r++) band = grow(band);
    const edge = grow(band);
    const hex = (c) => [1, 3, 5].map((i) => parseInt(c.slice(i, i + 2), 16));
    const [cr, cg, cb] = hex(color), [ir, ig, ib] = hex(COLORS.ink);
    for (let k = 0; k < w * h; k++) {
      const rgba = inside[k] ? tint : band[k] ? [cr, cg, cb, 255] : edge[k] ? [ir, ig, ib, 255] : [0, 0, 0, 0];
      img.data.set(rgba, k * 4);
    }
    ctx.putImageData(img, 0, 0);
    return { canvas, x, y };
  }

  down(e) {
    this.root.setPointerCapture(e.pointerId);
    this.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY, sx: e.clientX, sy: e.clientY });
    if (this.pointers.size === 2) this.pinch = this.spread();
    this.dragged = false;
    this.follow = null;
    this.target = null;
  }

  move(e) {
    const p = this.pointers.get(e.pointerId);
    if (!p) {
      const id = this.buildingAt(e.clientX, e.clientY);
      this.highlight("hover", id);
      this.root.classList.toggle("pointing", id !== null);
      return;
    }
    if (this.pointers.size === 2) {
      p.x = e.clientX;
      p.y = e.clientY;
      const spread = this.spread();
      const [a, b] = [...this.pointers.values()];
      this.zoomTo = null;
      this.setZoom(this.z * (spread / this.pinch), (a.x + b.x) / 2, (a.y + b.y) / 2);
      this.pinch = spread;
      this.dragged = true;
      return;
    }
    if (Math.hypot(e.clientX - p.sx, e.clientY - p.sy) > DRAG_PX) this.dragged = true;
    if (this.dragged) {
      this.root.classList.add("dragging");
      this.tx += e.clientX - p.x;
      this.ty += e.clientY - p.y;
      this.apply();
    }
    p.x = e.clientX;
    p.y = e.clientY;
  }

  up(e, cancelled = false) {
    if (!this.pointers.has(e.pointerId)) return;
    this.pointers.delete(e.pointerId);
    this.root.classList.remove("dragging");
    if (!cancelled && !this.dragged && this.pointers.size === 0) {
      this.onSelect(this.buildingAt(e.clientX, e.clientY));
    }
  }

  spread() {
    const [a, b] = [...this.pointers.values()];
    return Math.hypot(a.x - b.x, a.y - b.y) || 1;
  }

  wheel(e) {
    // If the page is already pinch-zoomed, let the browser pinch it back out.
    if (e.ctrlKey && window.visualViewport?.scale > 1) return;
    e.preventDefault();
    // Wheel notches and trackpad scrolls both scale the zoom by how far they moved; pinches (ctrl+wheel) send
    // smaller deltas, so they count for more. Line and page deltas are converted to pixels first.
    const px = e.deltaY * (e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? innerHeight : 1);
    this.zoomBy(Math.exp(-px * (e.ctrlKey ? 0.01 : 0.0015)), e.clientX, e.clientY);
  }
}
