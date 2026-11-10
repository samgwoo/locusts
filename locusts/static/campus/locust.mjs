// The locust sprite: animations from locusts/static/sprites/locust.json, plus flying between map spots.

// Trips come from the server, which sets the speed (travel_px_per_second in config.py) and the straight
// line. This flies that line in one go: a short climb, a cruise above the roofs, and a landing. The
// shadow stays on the line. Altitude is in map pixels. Roofs are drawn about 4.25 px per meter up, so
// the cruise clears the ordinary blocks (Franklin, the tall one, is about 190 px) while staying low
// enough that the pet and the roofs both fit on screen. The three college-house towers rise about
// twice that, and a pet crosses those in front of the facade.
const MIN_ALT = 96;
const MAX_ALT = 260;
const CLIMB_MS = 1400;

const smooth = (t) => t * t * (3 - 2 * t);

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
    this.lift = 0;
    this.placed = true;
  }

  // Fly a server trip ({from, to, start, arrive}, in seconds) that began `elapsed` ms ago, so a
  // reloaded page picks the flight up partway. Faces the direction of travel. Position along the line
  // stays linear in time, matching the server; only the altitude eases.
  walk(trip, elapsed) {
    const [x0, y0] = trip.from, [x, y] = trip.to;
    const dx = x - x0, dy = y - y0;
    const distance = Math.hypot(dx, dy);
    const total = (trip.arrive - trip.start) * 1000;
    if (distance < 1 || elapsed >= total) return this.place(x, y);
    if (Math.abs(dx) > 2) this.facing = Math.sign(dx);
    this.placed = true;
    this.hop = {
      trip, from: trip.from, to: trip.to, start: performance.now() - elapsed, total,
      cruise: Math.min(MAX_ALT, Math.max(MIN_ALT, distance * 0.14)), fromLift: this.lift,
    };
    this.stepHop(performance.now());
  }

  // The current animation and frame, so the HUD portrait can mirror the pet.
  frame(now) {
    if (this.hop?.flying) return { name: "fly", col: frameAt(this.sheet, "fly", now - this.hop.start) };
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
    // A smaller, lighter shadow is the ground under a pet that has climbed.
    const air = this.hop ? Math.min(1, this.lift / this.hop.cruise) : 0;
    this.shadow.style.opacity = String(1 - 0.45 * air);
    this.shadow.style.transform = `translate(${left}px, ${top}px) scale(${z * (1 - 0.4 * air)})`;
    this.el.style.transform = `translate(${left}px, ${top - Math.round(this.lift) * z}px) scale(${z * this.facing}, ${z})`;
    const row = this.sheet.animations[name].row;
    for (const [id, el] of this.worn) {
      showFrame(el, this.sheet, row, col, id);
      el.style.transform = this.el.style.transform;
    }
  }

  stepHop(now) {
    const { from, to, start, total, cruise, fromLift } = this.hop;
    const p = (now - start) / total;
    if (p >= 1) {
      this.hop = null;
      this.x = to[0];
      this.y = to[1];
      this.lift = 0;
      this.started = now;
      return;
    }
    const climb = Math.min(0.45, CLIMB_MS / total);
    let alt;
    if (p < climb) alt = fromLift + (cruise - fromLift) * smooth(p / climb);
    else if (p > 1 - climb) alt = cruise * smooth((1 - p) / climb);
    else alt = cruise + Math.sin((now - start) / 700 * Math.PI * 2) * 5;
    // Crouch, launch, flap, land. The flap is its own loop (the fly row); a redirect stays in it.
    this.hop.flying = false;
    if (p > 1 - climb * 0.4) this.hopFrame = 3;
    else if (fromLift > 20 || p >= climb) this.hop.flying = true;
    else this.hopFrame = p < climb * 0.35 ? 0 : 1;
    this.x = from[0] + (to[0] - from[0]) * p;
    this.y = from[1] + (to[1] - from[1]) * p;
    this.lift = alt;
  }
}
