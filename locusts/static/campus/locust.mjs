// The locust sprite: animations from locusts/static/sprites/locust.json, plus hopping between map spots.

// Trips come from the server, which sets the walking speed (travel_px_per_second in config.py). This
// only picks how far apart the hops land; their timing is spread evenly so the last one lands on arrival.
const HOP_PX = 50;

// locust.json, with wear.json as its `wear`: the clothes sheet, a block like the locust sheet per wearable item.
export async function loadSheet() {
  const [sheet, wear] = await Promise.all(["locust", "wear"].map((n) => fetch(`/static/sprites/${n}.json`).then((r) => r.json())));
  return { ...sheet, wear };
}

// The frame of an animation that is showing `elapsed` ms after it started.
export function frameAt(sheet, name, elapsed) {
  const ms = sheet.animations[name].ms;
  const total = ms.reduce((a, b) => a + b, 0);
  let t = elapsed % total;
  for (let i = 0; i < ms.length; i++) {
    if (t < ms[i]) return i;
    t -= ms[i];
  }
  return 0;
}

// Shows a frame of the locust sheet in `el`, or with `item`, that frame of the item's block in the clothes sheet.
export function showFrame(el, sheet, row, col, item = null) {
  const [w, h] = sheet.frame;
  const block = item ? sheet.wear.names.indexOf(item) * sheet.wear.height : 0;
  el.style.width = `${w}px`;
  el.style.height = `${h}px`;
  el.style.backgroundPosition = `-${col * w}px -${block + row * h}px`;
}

// Keeps `els` (an item id -> element map) showing `items`, in order, each made by `make` and placed after `after`.
export function syncWorn(els, items, make, after) {
  for (const [id, el] of els) if (!items.includes(id)) { el.remove(); els.delete(id); }
  let prev = after;
  for (const id of items) {
    let el = els.get(id);
    if (!el) els.set(id, (el = make(id)));
    if (prev.nextSibling !== el) prev.after(el);
    prev = el;
  }
}

export class Locust {
  constructor(parent, sheet) {
    this.sheet = sheet;
    this.shadow = Object.assign(document.createElement("div"), { className: "sprite" });
    this.el = Object.assign(document.createElement("div"), { className: "sprite" });
    parent.append(this.shadow, this.el);
    showFrame(this.shadow, sheet, sheet.shadow.row, sheet.shadow.col);
    const [ax, ay] = sheet.anchor;
    this.el.style.transformOrigin = this.shadow.style.transformOrigin = `${ax}px ${ay}px`;
    this.x = 0;
    this.y = 0;
    this.lift = 0;
    this.facing = 1;
    this.animation = "idle";
    this.started = performance.now();
    this.hop = null;
    this.placed = false;
    this.worn = new Map();
  }

  get hopping() {
    return this.hop !== null;
  }

  remove() {
    this.el.remove();
    this.shadow.remove();
    for (const el of this.worn.values()) el.remove();
  }

  // Dress it in shop item ids `items` (in drawing order, as the server sends `wearing`).
  wear(items) {
    syncWorn(this.worn, items.filter((id) => this.sheet.wear.names.includes(id)), () => {
      const el = Object.assign(document.createElement("div"), { className: "sprite wear" });
      el.style.transformOrigin = this.el.style.transformOrigin;
      return el;
    }, this.el);
  }

  setAnimation(name) {
    if (name !== this.animation) {
      this.animation = name;
      this.started = performance.now();
    }
  }

  place(x, y) {
    this.hop = null;
    this.x = x;
    this.y = y;
    this.placed = true;
  }

  // Hop along a server trip ({from, to, start, arrive}, in seconds) that began `elapsed` ms ago, so a
  // reloaded page picks the walk up partway. Faces the direction of travel.
  walk(trip, elapsed) {
    const [x0, y0] = trip.from, [x, y] = trip.to;
    const dx = x - x0, dy = y - y0;
    const distance = Math.hypot(dx, dy);
    const total = (trip.arrive - trip.start) * 1000;
    if (distance < 1 || elapsed >= total) return this.place(x, y);
    const hops = Math.max(1, Math.round(distance / HOP_PX));
    if (Math.abs(dx) > 2) this.facing = Math.sign(dx);
    this.placed = true;
    this.hop = {
      trip, from: trip.from, to: trip.to, hops, ms: total / hops, start: performance.now() - elapsed,
      height: Math.min(56, 18 + (distance / hops) * 0.12),
    };
    this.stepHop(performance.now());
  }

  // The current animation and frame, so the HUD portrait can mirror the pet.
  frame(now) {
    if (this.hop) return { name: "hop", col: this.hopFrame };
    return { name: this.animation, col: frameAt(this.sheet, this.animation, now - this.started) };
  }

  // Draws the current frame where `view` shows the locust's map position, scaled to the view's zoom.
  update(now, view) {
    if (this.hop) this.stepHop(now);
    const { name, col } = this.frame(now);
    showFrame(this.el, this.sheet, this.sheet.animations[name].row, col);
    const [ax, ay] = this.sheet.anchor;
    const z = view.z;
    const [sx, sy] = view.toScreen(Math.round(this.x), Math.round(this.y));
    const left = view.snap(sx - ax), top = view.snap(sy - ay);
    this.shadow.style.transform = `translate(${left}px, ${top}px) scale(${z})`;
    this.el.style.transform = `translate(${left}px, ${top - Math.round(this.lift) * z}px) scale(${z * this.facing}, ${z})`;
    const row = this.sheet.animations[name].row;
    for (const [id, el] of this.worn) {
      showFrame(el, this.sheet, row, col, id);
      el.style.transform = this.el.style.transform;
    }
  }

  stepHop(now) {
    const { from, to, hops, start, ms, height } = this.hop;
    const t = (now - start) / ms;
    if (t >= hops) {
      this.hop = null;
      this.x = to[0];
      this.y = to[1];
      this.lift = 0;
      this.started = now;
      return;
    }
    const index = Math.floor(t), phase = t - index;
    // Crouch, launch, fly, land.
    this.hopFrame = phase < 0.14 ? 0 : phase < 0.26 ? 1 : phase < 0.86 ? 2 : 3;
    const move = Math.min(1, Math.max(0, (phase - 0.14) / 0.72));
    const progress = (index + move) / hops;
    this.x = from[0] + (to[0] - from[0]) * progress;
    this.y = from[1] + (to[1] - from[1]) * progress;
    this.lift = Math.sin(Math.PI * move) * height;
  }
}
