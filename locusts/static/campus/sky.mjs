// Time of day and weather over the campus map. drawOnMap paints into the map canvas itself, as its last step:
// the light's color multiplied over everything (darkest at night), cloud shadows on the ground, and the night
// lights screen-blended on top (lit windows, fewer as the night goes on; street lamps; headlights). The pets are
// page elements over the map, tinted to match by a CSS filter. Over them goes one more canvas, the clouds
// themselves, drifting east. (Each extra full-window canvas costs Chrome about three window-sized buffers, and a
// blend mode more, so the sky keeps to one.)
//
// The light follows today's real sunrise and sunset in Philadelphia (from /api/weather) once that forecast
// has arrived, and the game's night (config.clock) until then. Clouds, rain, and snow come from the same
// forecast, so every player sees the same sky; with no forecast, clouds fall back to the campus date.
// Add ?time=HH:MM to hold the light at a campus minute, ?weather=rain|snow|storm|mix|drizzle|fog|clear to
// preview precipitation, and ?sun=HH:MM,HH:MM to preview a sunrise and sunset.

import { SparseCanvas } from "./sparse.mjs";

const DAY = { tint: [255, 255, 255], lights: 0, shadow: 1 };
const DAWN = { tint: [222, 180, 196], lights: 0.4, shadow: 0.5 };
const GOLDEN = { tint: [255, 214, 168], lights: 0.1, shadow: 0.8 };
const TWILIGHT = { tint: [150, 126, 182], lights: 0.75, shadow: 0.3 };
const NIGHT = { tint: [72, 84, 140], lights: 1, shadow: 0.12 };

// What share of windows are lit through the night, by minutes after night starts or ends (config.clock), and how
// the share eases from each keyframe to the next. With the default 11 PM to 9 AM night: nearly every window
// through the evening; from midnight people go to bed in a rush that tapers off ("log": about half the lights
// out by 1 AM and two thirds by 2), fewest lit around 4 AM; then waking up, about half lit by 9:30 AM when
// daylight drowns them out.
const WINDOWS_LIT = [["start", -180, 0.95], ["start", 60, 0.95, "log"], ["start", 300, 0.12], ["end", 30, 0.5]];
const EASE = {
  smooth: (f) => f * f * (3 - 2 * f),
  log: (f) => Math.log1p(9 * f) / Math.log(10),
};
const BLINK_MS = 1500;         // how often some window flips against the trend for a minute or so
// A lit window's warm color as one little-endian RGBA pixel.
const WINDOW_COLOR = ((255 << 24) | (140 << 16) | (214 << 8) | 255) >>> 0;

// Page-URL previews. Intensity is 0 to 1, the same scale /api/weather reports.
const WEATHER_PREVIEW = {
  clear: { kind: "clear", intensity: 0, storm: false },
  drizzle: { kind: "rain", intensity: 0.18, storm: false },
  rain: { kind: "rain", intensity: 0.62, storm: false },
  storm: { kind: "rain", intensity: 0.95, storm: true },
  snow: { kind: "snow", intensity: 0.7, storm: false },
  mix: { kind: "mix", intensity: 0.6, storm: false },
  fog: { kind: "fog", intensity: 0, storm: false },
};

const CLOUD_CELL = 4;          // map pixels per cloud pixel, so clouds are chunky like the rest of the art
const CLOUD_LIFT = [70, 150];  // how far above its shadow a cloud floats, in map pixels
const WIND = [5, 0.6];         // map pixels per second the clouds drift (and a little south)
const MARGIN = 500;            // clouds wrap around this far past the map's edges
const REDRAW_MS = 100;         // the sky only moves this often unless the map does

const toMinutes = (hhmm) => { const [h, m] = hhmm.split(":").map(Number); return h * 60 + m; };
const mix = (a, b, f) => a + (b - a) * f;

function mulberry32(seed) {
  return () => {
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function hash(text) {
  let h = 2166136261;
  for (const c of text) h = Math.imul(h ^ c.charCodeAt(0), 16777619);
  return h >>> 0;
}

export class Sky {
  constructor(view, clock, { lamps, windows }, { still = false } = {}) {
    this.view = view;
    this.clock = clock;
    this.lamps = lamps;
    this.windows = Sky.parseWindows(windows);
    this.lit = null;
    this.overrides = new Map();
    this.lastBlink = 0;
    this.still = still;
    this.frozenAt = Date.now() / 1000;
    const params = new URLSearchParams(location.search);
    const held = params.get("time");
    this.heldMinutes = held && /^\d{1,2}:\d{2}$/.test(held) ? toMinutes(held) : null;
    this.weatherOverride = WEATHER_PREVIEW[params.get("weather")] ?? null;
    const cover = params.get("cover");
    this.coverOverride = cover != null && cover !== "" && Number.isFinite(Number(cover))
      ? Math.min(1, Math.max(0, Number(cover))) : null;
    const sun = params.get("sun");
    this.sunOverride = sun && /^\d{1,2}:\d{2},\d{1,2}:\d{2}$/.test(sun) ? sun.split(",").map(toMinutes) : null;
    this.report = null;
    this.windScale = 1;
    this.drops = null;
    this.keys = this.sunOverride ? this.keyframes(...this.sunOverride) : this.keyframes();
    const clouds = Object.assign(document.createElement("canvas"), { className: "sky" });
    view.root.append(clouds);
    this.cloudLayer = clouds.getContext("2d");
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("width", "0");
    svg.setAttribute("height", "0");
    svg.style.position = "absolute";
    svg.innerHTML = `<filter id="pet-tint" color-interpolation-filters="sRGB"><feColorMatrix type="matrix"/></filter>`;
    document.body.append(svg);
    this.petFilter = svg.querySelector("feColorMatrix");
    view.overlay = (ctx, x0, y0, x1, y1) => this.drawOnMap(ctx, x0, y0, x1, y1);
    this.halo = this.makeHalo(9, [255, 196, 120]);
    this.beam = this.makeHalo(3, [255, 236, 190]);
    this.campusOffset = null;
    this.drawn = null;
    addEventListener("resize", () => this.resize());
    this.resize();
  }

  resize() {
    this.cloudLayer.canvas.width = innerWidth;
    this.cloudLayer.canvas.height = innerHeight;
    this.drawn = null;
  }

  // The light around the day, keyed by minute: [minute, look], sorted, wrapping at midnight. Without a
  // forecast this is the game's night. With one, sunrise and sunset are today's, in campus minutes. A day
  // shorter than four hours isn't a real one here, so that falls back rather than piling dawn onto dusk.
  keyframes(sunrise, sunset) {
    let end = sunrise ?? toMinutes(this.clock.night_end);
    let start = sunset ?? toMinutes(this.clock.night_start);
    if (sunrise != null && sunset != null && start < end + 240) {
      end = toMinutes(this.clock.night_end);
      start = toMinutes(this.clock.night_start);
    }
    return [[end - 90, NIGHT], [end - 30, DAWN], [end + 30, DAY], [start - 150, DAY], [start - 75, GOLDEN],
      [start - 20, TWILIGHT], [start + 30, NIGHT]]
      .map(([m, look]) => [((m % 1440) + 1440) % 1440, look])
      .sort((a, b) => a[0] - b[0]);
  }

  // A forecast from /api/weather. Sunrise and sunset retarget the light; everyone already shares the clock.
  applyForecast(report) {
    if (!report?.available) return;
    this.report = report;
    this.windScale = report.wind_kmh == null ? 1 : Math.min(1.8, 0.55 + report.wind_kmh / 28);
    if (!this.sunOverride && report.sunrise && report.sunset) {
      this.keys = this.keyframes(toMinutes(report.sunrise), toMinutes(report.sunset));
    }
    this.current = null;
    this.drawn = null;
  }

  // What is falling, and how much of the sky the clouds should cover. A page preview wins over the forecast;
  // with neither, cover stays null and the date's seeded clouds are used.
  conditions() {
    const over = this.weatherOverride;
    const report = this.report?.available ? this.report : null;
    const kind = over?.kind ?? report?.kind ?? "clear";
    const intensity = over ? over.intensity : (report?.intensity ?? 0);
    const storm = over ? over.storm : !!report?.storm;
    let cover = this.coverOverride;
    if (cover == null && report?.cloud_cover != null) cover = report.cloud_cover;
    if (cover == null && over && over.kind !== "clear" && over.kind !== "fog") cover = 0.92;
    if (cover == null && over?.kind === "fog") cover = 0.9;
    return { kind, intensity, storm, cover };
  }

  // Campus minutes since midnight, and the campus date, from the timezone's current UTC offset (refreshed every
  // ten minutes, which catches daylight saving).
  campusTime(now) {
    if (!this.campusOffset || now - this.campusOffset.at > 600_000) {
      const parts = Object.fromEntries(new Intl.DateTimeFormat("en-US", {
        timeZone: this.clock.timezone, year: "numeric", month: "2-digit", day: "2-digit", hour: "numeric",
        minute: "2-digit", hourCycle: "h23",
      }).formatToParts(new Date(now)).map((p) => [p.type, p.value]));
      const local = Number(parts.hour) * 60 + Number(parts.minute);
      const utc = new Date(now).getUTCHours() * 60 + new Date(now).getUTCMinutes();
      this.campusOffset = { at: now, minutes: ((local - utc) % 1440 + 1440) % 1440 };
    }
    const d = new Date(now);
    const utc = d.getUTCHours() * 60 + d.getUTCMinutes() + d.getUTCSeconds() / 60;
    const minutes = this.heldMinutes ?? (utc + this.campusOffset.minutes) % 1440;
    const date = new Date(now + this.campusOffset.minutes * 60_000).toISOString().slice(0, 10);
    return { minutes, date };
  }

  // The share of windows lit at a campus minute, eased between WINDOWS_LIT's keyframes.
  windowShare(minutes) {
    const anchor = { start: toMinutes(this.clock.night_start), end: toMinutes(this.clock.night_end) };
    const keys = WINDOWS_LIT.map(([from, offset, share, ease = "smooth"]) =>
      [((anchor[from] + offset) % 1440 + 1440) % 1440, share, ease]).sort((a, b) => a[0] - b[0]);
    let i = keys.findIndex(([m]) => m > minutes);
    if (i === -1) i = 0;
    const [m1, b] = keys[i], [m0, a, ease] = keys[(i - 1 + keys.length) % keys.length];
    const f = ((minutes - m0 + 1440) % 1440) / (((m1 - m0 + 1440) % 1440) || 1440);
    return mix(a, b, EASE[ease](f));
  }

  // windows.bin from tools/render_campus.py: per window, its box (x, y, w, h as little-endian uint16s) then its
  // pixels as a row-major bit mask, most significant bit first, in the random order windows switch in.
  static parseWindows(buffer) {
    const view = new DataView(buffer), out = [];
    for (let o = 0; o < buffer.byteLength;) {
      const [x, y, w, h] = [0, 2, 4, 6].map((k) => view.getUint16(o + k, true));
      const bytes = Math.ceil((w * h) / 8);
      out.push({ x, y, w, h, bits: new Uint8Array(buffer, o + 8, bytes) });
      o += 8 + bytes;
    }
    return out;
  }

  // Bring the lit windows up to `share`: they switch in their random rank order, one at a time, plus a few
  // flipping the other way for a while, so the campus looks lived in. Each window that changes is painted onto
  // the canvas the glow layer draws from (see paintWindow).
  syncWindows(share, wall) {
    const n = this.windows.length;
    if (!this.lit) {
      const { width, height } = this.view.map;
      this.lit = new SparseCanvas(width, height, this.windows.map(({ x, y, w, h }) => [x, y, w, h]));
      const w = Math.max(...this.windows.map((win) => win.w)), h = Math.max(...this.windows.map((win) => win.h));
      this.scratch = Object.assign(document.createElement("canvas"), { width: w, height: h }).getContext("2d");
      this.scratchData = this.scratch.createImageData(w, h);
      this.scratchPixels = new Uint32Array(this.scratchData.data.buffer);
      this.state = new Uint8Array(n);
      this.target = 0;
    }
    const changed = [];
    const prev = this.target;
    this.target = Math.round(share * n);
    for (let i = Math.min(prev, this.target); i < Math.max(prev, this.target); i++) this.setWindow(i, changed);
    for (const [i, o] of this.overrides) {
      if (o.until > wall) continue;
      this.overrides.delete(i);
      this.setWindow(i, changed);
    }
    if (wall - this.lastBlink > BLINK_MS) {
      this.lastBlink = wall;
      const i = Math.floor(Math.random() * n);
      this.overrides.set(i, { on: i >= this.target, until: wall + 20_000 + Math.random() * 70_000 });
      this.setWindow(i, changed);
    }
    for (const i of changed) this.paintWindow(i);
  }

  setWindow(i, changed) {
    const want = this.overrides.get(i)?.on ?? i < this.target;
    if (this.state[i] === +want) return;
    this.state[i] = +want;
    changed.push(i);
  }

  // Window `i`'s exact pixels, lit or put out. Its mask goes through a scratch canvas, then is drawn on to light
  // it or drawn "destination-out" to clear only those pixels, so a neighbor whose box overlaps it is untouched.
  paintWindow(i) {
    const win = this.windows[i], { w, h } = win, stride = this.scratchData.width, px = this.scratchPixels;
    for (let j = 0; j < h; j++) {
      for (let k = 0; k < w; k++) {
        const bit = j * w + k;
        px[j * stride + k] = win.bits[bit >> 3] & (0x80 >> (bit & 7)) ? WINDOW_COLOR : 0;
      }
    }
    this.scratch.putImageData(this.scratchData, 0, 0, 0, 0, w, h);
    this.lit.paint(this.scratch.canvas, 0, 0, w, h, win.x, win.y, this.state[i] ? "source-over" : "destination-out");
  }

  // The light at a campus minute, blended between its two nearest keyframes.
  light(minutes) {
    const keys = this.keys;
    let i = keys.findIndex(([m]) => m > minutes);
    if (i === -1) i = 0;
    const [m1, b] = keys[i], [m0, a] = keys[(i - 1 + keys.length) % keys.length];
    const span = (m1 - m0 + 1440) % 1440 || 1440;
    const f = ((minutes - m0 + 1440) % 1440) / span;
    return {
      tint: a.tint.map((v, k) => Math.round(mix(v, b.tint[k], f))),
      lights: mix(a.lights, b.lights, f),
      shadow: mix(a.shadow, b.shadow, f),
    };
  }

  // A soft pixel glow, `r` cells across at two map pixels per cell, in screen-blend colors.
  makeHalo(r, [cr, cg, cb]) {
    const size = r * 2 + 1;
    const canvas = Object.assign(document.createElement("canvas"), { width: size, height: size });
    const ctx = canvas.getContext("2d");
    const img = ctx.createImageData(size, size);
    for (let y = 0; y < size; y++) {
      for (let x = 0; x < size; x++) {
        const d = Math.hypot(x - r, y - r) / r;
        // Stepped falloff, dithered between steps, so it reads as pixel art rather than a smooth gradient.
        const level = Math.max(0, 1 - d);
        const stepped = Math.floor(level * 4 + ((x + y) % 2) * 0.5) / 4;
        const i = (y * size + x) * 4;
        img.data.set([cr, cg, cb, Math.round(stepped * 200)], i);
      }
    }
    ctx.putImageData(img, 0, 0);
    return canvas;
  }

  // How much of a full overcast to draw when no forecast has arrived: one number per campus date, so
  // everyone still shares a sky while Philadelphia is unreachable.
  seededCover(date) {
    if (this.seeded?.date === date) return this.seeded.cover;
    const cover = mulberry32(hash(`locusts-sky:${date}`))();
    this.seeded = { date, cover };
    return cover;
  }

  // One full set of clouds for the date, in a fixed order. How many show is the forecast's cloud cover
  // (see cloudAlpha), so a clearing sky fades clouds out instead of rebuilding them.
  weather(date) {
    if (this.cloudDay?.date === date) return this.cloudDay.clouds;
    const rng = mulberry32(hash(`locusts-sky:${date}:clouds`));
    const { width, height } = this.view.map;
    const count = Math.max(4, Math.round(18 * (width * height) / (7000 * 3000)));
    const clouds = Array.from({ length: count }, (_, i) => {
      const sprite = this.makeCloud(rng, rng());
      return {
        ...sprite, rank: (i + 0.5) / count, x0: rng() * (width + MARGIN * 2), y0: rng() * (height + MARGIN),
        speed: 0.7 + rng() * 0.6, lift: mix(...CLOUD_LIFT, rng()), bob: rng() * Math.PI * 2,
      };
    });
    this.cloudDay = { date, clouds };
    return clouds;
  }

  // One cloud as a mask of puffs, drawn twice: lit cream on top with a cooler underside, and a flat shadow.
  makeCloud(rng, cover) {
    const w = Math.round(mix(40, 110, rng()) * (0.8 + cover * 0.5)), h = Math.round(mix(20, 34, rng()));
    // A flat base of wide puffs, with rounder ones heaped on top.
    const base = Array.from({ length: 3 + Math.floor(rng() * 3) }, () => ({
      x: mix(0.2, 0.8, rng()) * w, y: mix(0.62, 0.72, rng()) * h, rx: mix(0.18, 0.32, rng()) * w, ry: mix(0.22, 0.3, rng()) * h,
    }));
    const tops = Array.from({ length: 2 + Math.floor(rng() * 4) }, () => {
      const r = mix(0.22, 0.4, rng()) * h;
      return { x: mix(0.25, 0.75, rng()) * w, y: mix(0.4, 0.55, rng()) * h, rx: r * 1.6, ry: r };
    });
    const puffs = [...base, ...tops];
    const inside = (x, y) => puffs.some((p) => ((x - p.x) / p.rx) ** 2 + ((y - p.y) / p.ry) ** 2 <= 1);
    const paint = (shadow) => {
      const canvas = Object.assign(document.createElement("canvas"), { width: w, height: h });
      const ctx = canvas.getContext("2d");
      const img = ctx.createImageData(w, h);
      for (let y = 0; y < h; y++) {
        for (let x = 0; x < w; x++) {
          if (!inside(x, y)) continue;
          const edge = !inside(x - 1, y) || !inside(x + 1, y) || !inside(x, y - 1) || !inside(x, y + 1);
          if (edge && (x + y) % 2) continue;                    // a dithered rim instead of a hard outline
          const under = !inside(x, y + 2) || y > h * 0.72;
          const color = shadow ? [0, 0, 0] : under ? [214, 210, 222] : [244, 240, 230];
          img.data.set([...color, 255], (y * w + x) * 4);
        }
      }
      ctx.putImageData(img, 0, 0);
      return canvas;
    };
    return { w, h, lit: paint(false), shadow: paint(true), tinted: null };
  }

  // A cloud sprite multiplied by the light's color, recolored only when the light has changed visibly.
  tinted(cloud, tint, shadowGray) {
    const key = `${tint}|${shadowGray}`;
    if (cloud.tinted?.key === key) return cloud.tinted;
    const make = (src, color) => {
      const canvas = Object.assign(document.createElement("canvas"), { width: cloud.w, height: cloud.h });
      const ctx = canvas.getContext("2d");
      ctx.drawImage(src, 0, 0);
      ctx.globalCompositeOperation = src === cloud.shadow ? "source-in" : "multiply";
      ctx.fillStyle = `rgb(${color})`;
      ctx.fillRect(0, 0, cloud.w, cloud.h);
      ctx.globalCompositeOperation = "destination-in";
      ctx.drawImage(src, 0, 0);
      return canvas;
    };
    cloud.tinted = { key, lit: make(cloud.lit, tint), shadow: make(cloud.shadow, [shadowGray, shadowGray, shadowGray + 8]) };
    return cloud.tinted;
  }

  // The sky right now: the light (cooled when something is falling), the shade cloud shadows cast, and
  // where every cloud is. Worked out once per frame, for both drawOnMap and the cloud layer.
  now() {
    const wall = Date.now();
    if (this.current?.wall === wall) return this.current;
    const { minutes, date } = this.campusTime(wall);
    const cond = this.conditions();
    const light = this.light(minutes);
    const tint = Sky.weatherTint(light.tint, cond.kind, cond.intensity);
    const lit = { ...light, tint };
    const t = this.still ? this.frozenAt : wall / 1000;
    const { width, height } = this.view.map;
    const span = width + MARGIN * 2, ySpan = height + MARGIN;
    const wind = this.windScale;
    const cover = cond.cover ?? this.seededCover(date);
    const clouds = this.weather(date).map((c) => {
      const x = (((c.x0 + WIND[0] * wind * c.speed * t) % span) + span) % span - MARGIN;
      const y = (((c.y0 + WIND[1] * wind * c.speed * t) % ySpan) + ySpan) % ySpan - MARGIN / 2
        + Math.sin(t / 900 + c.bob) * 12;
      return { c, x, y, w: c.w * CLOUD_CELL, h: c.h * CLOUD_CELL, alpha: Sky.cloudAlpha(c.rank, cover) };
    });
    const wet = cond.kind === "rain" || cond.kind === "mix" ? 1 - 0.18 * cond.intensity : 1;
    return (this.current = {
      wall, minutes, light: lit, clouds, cond, cover,
      shadowGray: Math.round(mix(255, 172, light.shadow) * wet),
    });
  }

  // Draws the light over the map, as the map view's last step (ctx is in map pixels): everything multiplied by
  // the sky's color and darkened under the clouds, then screen-blended night lights (lit windows, street lamps,
  // and a small headlight just ahead of every car on the island, unless something stands in front of it).
  // Pets are page elements over the map, so the lights never land on one; they get the tint from tintPets.
  drawOnMap(ctx, x0, y0, x1, y1) {
    const { minutes, light, clouds, shadowGray, wall } = this.now();
    const v = this.view, traffic = v.traffic;
    ctx.save();
    ctx.globalCompositeOperation = "multiply";
    if (light.tint.some((c) => c < 255)) {
      ctx.save();
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.fillStyle = `rgb(${light.tint})`;
      ctx.fillRect(0, 0, ctx.canvas.width, ctx.canvas.height);
      ctx.restore();
    }
    for (const { c, x, y, w, h, alpha } of clouds) {
      if (alpha <= 0 || x > x1 || y > y1 || x + w < x0 || y + h < y0) continue;
      ctx.globalAlpha = alpha;
      ctx.drawImage(this.tinted(c, light.tint, shadowGray).shadow, x, y, w, h);
    }
    ctx.globalAlpha = 1;
    if (light.lights > 0.02) {
      this.syncWindows(this.windowShare(minutes), wall);
      ctx.globalCompositeOperation = "screen";
      ctx.globalAlpha = light.lights;
      const sx = Math.max(0, Math.floor(x0)), sy = Math.max(0, Math.floor(y0));
      const sw = Math.min(v.map.width, Math.ceil(x1) + 1) - sx, sh = Math.min(v.map.height, Math.ceil(y1) + 1) - sy;
      if (sw > 0 && sh > 0) this.lit.draw(ctx, sx, sy, sw, sh);
      const r = this.halo.width;
      for (const [lx, ly] of this.lamps) {
        if (lx < x0 - r || ly < y0 - r || lx > x1 + r || ly > y1 + r) continue;
        ctx.drawImage(this.halo, lx - r, ly - r, r * 2, r * 2);
      }
      const b = this.beam.width;
      for (const car of traffic?.agents ?? []) {
        if (car.pending || car.parked !== null) continue;
        const p = traffic.pose(car);
        const hx = p.x + p.dx * 12, hy = p.y + p.dy * 12;
        if (hx < x0 - b || hy < y0 - b || hx > x1 + b || hy > y1 + b) continue;
        if (!traffic.onLand(hx, hy) || traffic.underBridge(car, p.x, p.y)) continue;
        if (traffic.occluded(hx, hy) || traffic.occluded(p.x, p.y)) continue;
        ctx.drawImage(this.beam, hx - b, hy - b, b * 2, b * 2);
      }
    }
    ctx.restore();
  }

  // The pets' sprites multiplied by the sky's color, like the map under them, through an SVG color filter.
  tintPets(tint) {
    const key = tint.join(",");
    if (key === this.petTint) return;
    this.petTint = key;
    const [r, g, b] = tint.map((c) => (c / 255).toFixed(3));
    this.petFilter.setAttribute("values", `${r} 0 0 0 0  0 ${g} 0 0 0  0 0 ${b} 0 0  0 0 0 1 0`);
    document.documentElement.style.setProperty("--pet-tint", key === "255,255,255" ? "none" : "url(#pet-tint)");
  }

  // Called every frame: keeps the map redrawing as the light and cloud shadows move, tints the pets, and draws
  // the clouds, which float above the pets (in CSS pixels: they're chunky enough, at a quarter the memory).
  // Rain and snow are redrawn with them, in screen space, but they don't ask the map to repaint.
  draw(now) {
    const v = this.view;
    const { light, clouds, shadowGray, cond } = this.now();
    this.tintPets(light.tint);
    const falling = cond.intensity > 0 && !this.still;
    const state = `${v.tx}|${v.ty}|${v.z}|${innerWidth}|${innerHeight}|${light.tint}|${cond.kind}|${cond.intensity}`;
    const move = !this.drawn || this.drawn.state !== state || now - this.drawn.at >= REDRAW_MS;
    if (!move && !falling) return;
    if (move) {
      this.drawn = { state, at: now };
      v.dirty = true;
    }
    const sky = this.cloudLayer;
    sky.setTransform(1, 0, 0, 1, 0, 0);
    sky.clearRect(0, 0, sky.canvas.width, sky.canvas.height);
    sky.setTransform(v.z, 0, 0, v.z, Math.round(v.tx), Math.round(v.ty));
    sky.imageSmoothingEnabled = false;
    const [x0, y0] = v.toMap(0, 0), [x1, y1] = v.toMap(innerWidth, innerHeight);
    const fade = 0.82 * Math.min(1, Math.max(0.18, (4.2 - v.z * v.dpr) / 3));
    for (const { c, x, y, w, h, alpha } of clouds) {
      if (alpha <= 0) continue;
      const cy = y - c.lift;
      if (x > x1 || cy > y1 || x + w < x0 || cy + h < y0) continue;
      sky.globalAlpha = fade * alpha;
      sky.drawImage(this.tinted(c, light.tint, shadowGray).lit, x - c.lift * 0.12, cy, w, h);
    }
    sky.globalAlpha = 1;
    sky.setTransform(1, 0, 0, 1, 0, 0);
    this.drawPrecip(sky, now, cond);
  }

  // 0 when `rank` is still above the cloud cover, 1 once the cover has passed it, fading across a tenth.
  static cloudAlpha(rank, cover) {
    return Math.min(1, Math.max(0, (cover - rank) / 0.1));
  }

  // Rain cools and darkens the daylight; snow pulls it toward the cloud color. Clear weather is unchanged,
  // which is what the day/night tests measure on light() itself.
  static weatherTint(tint, kind, intensity) {
    if (!intensity || kind === "clear" || kind === "fog") return tint;
    const k = Math.min(1, Math.max(0, intensity));
    if (kind === "snow") return tint.map((c, i) => Math.round(mix(c, [206, 216, 230][i], 0.22 * k)));
    return [
      Math.round(tint[0] * (1 - 0.14 * k)),
      Math.round(tint[1] * (1 - 0.09 * k)),
      Math.round(Math.min(255, tint[2] * (1 - 0.02 * k))),
    ];
  }

  // How many flakes fit the window. Area is relative to a laptop screen, and capped so a big monitor
  // doesn't get a blizzard of its own.
  static dropCount(kind, intensity, width, height) {
    if (intensity <= 0 || kind === "clear" || kind === "fog") return 0;
    const area = Math.max(0.55, Math.min(2.4, (width * height) / (1280 * 800)));
    const base = kind === "snow" ? 160 : kind === "mix" ? 260 : 280;
    return Math.round(base * (0.35 + 0.65 * intensity) * area);
  }

  // Advance one flake. `dt` is seconds, `wind` scales rain's sideways speed (snow already drifts). A flake
  // that leaves the bottom comes back at the top, shifted across, so the pattern doesn't repeat in columns.
  static stepDrop(drop, dt, width, height, wind = 1) {
    drop.x += (drop.snow ? drop.vx : drop.vx * wind) * dt;
    drop.y += drop.vy * dt;
    if (drop.y > height) {
      drop.y = -drop.len;
      drop.x += width * 0.37;
    }
    drop.x = ((drop.x % width) + width) % width;
    return drop;
  }

  // Sideways wobble for a snowflake, in CSS pixels, from the frame time in milliseconds.
  static drift(drop, time) {
    return drop.sway ? Math.sin(time / 900 + drop.phase) * drop.sway : 0;
  }

  spawnDrop(kind, width, height, rng, anywhere) {
    const snow = kind === "snow" || (kind === "mix" && rng() < 0.45);
    const len = snow ? 4 + Math.floor(rng() * 3) * 2 : 14 + Math.floor(rng() * 12);
    return {
      snow, len, x: rng() * width, y: anywhere ? rng() * height : -len,
      vy: snow ? 22 + rng() * 24 : 360 + rng() * 180,
      vx: snow ? -8 + rng() * 16 : 36 + rng() * 28,
      sway: snow ? 8 + rng() * 14 : 0,
      phase: rng() * Math.PI * 2,
      bright: rng() > 0.72,
    };
  }

  syncDrops(kind, intensity, width, height) {
    const count = Sky.dropCount(kind, intensity, width, height);
    if (!this.drops || this.dropKind !== kind || this.dropW !== width || this.dropH !== height) {
      const rng = mulberry32((Math.random() * 0x100000000) >>> 0);
      this.drops = Array.from({ length: count }, () => this.spawnDrop(kind, width, height, rng, true));
      this.dropKind = kind;
      this.dropW = width;
      this.dropH = height;
      return;
    }
    const rng = mulberry32(0x9e3779b9);
    while (this.drops.length < count) this.drops.push(this.spawnDrop(kind, width, height, rng, true));
    if (this.drops.length > count) this.drops.length = count;
  }

  // Precipitation in CSS pixels, over the clouds: rain slants with the wind, snow drifts. Fog is a veil.
  // Reduced motion leaves the flakes where they spawned.
  drawPrecip(ctx, now, cond) {
    const { kind, intensity, storm } = cond;
    const width = ctx.canvas.width, height = ctx.canvas.height;
    if (kind === "fog") {
      ctx.fillStyle = "rgba(210, 216, 222, 0.28)";
      ctx.fillRect(0, 0, width, height);
    }
    if (intensity > 0) {
      this.syncDrops(kind, intensity, width, height);
      if (!this.still) {
        const dt = Math.min(0.05, (now - (this.lastFall ?? now)) / 1000);
        this.lastFall = now;
        for (const drop of this.drops) Sky.stepDrop(drop, dt, width, height, this.windScale);
      }
      for (const drop of this.drops) this.drawDrop(ctx, drop, now);
    }
    if (storm) this.drawFlash(ctx, now);
  }

  drawDrop(ctx, drop, now) {
    const x = Math.round(drop.x + Sky.drift(drop, now));
    const y = Math.round(drop.y);
    if (drop.snow) {
      ctx.fillStyle = drop.bright ? "rgba(255, 255, 255, 0.96)" : "rgba(232, 238, 246, 0.9)";
      const s = drop.len;
      // A small block with two pixels sticking out, so a flake reads as one instead of a dot.
      ctx.fillRect(x, y, s, s);
      ctx.fillRect(x - 2, y + (s >> 1) - 1, 2, 2);
      ctx.fillRect(x + s, y + (s >> 1) - 1, 2, 2);
      ctx.fillRect(x + (s >> 1) - 1, y - 2, 2, 2);
      return;
    }
    for (let i = 0; i < drop.len; i += 2) {
      const px = x + (i >> 2);
      const py = y + i;
      ctx.fillStyle = "rgba(96, 122, 148, 0.72)";
      ctx.fillRect(px, py, 1, 2);
      ctx.fillStyle = drop.bright ? "rgba(244, 248, 252, 0.95)" : "rgba(186, 208, 224, 0.88)";
      ctx.fillRect(px + 1, py, 1, 2);
    }
  }

  drawFlash(ctx, now) {
    if (this.still) return;
    if (this.nextFlash == null) this.nextFlash = now + 4000 + Math.random() * 8000;
    if (now >= this.nextFlash) {
      this.flashUntil = now + 140;
      this.nextFlash = now + 7000 + Math.random() * 11000;
    }
    if (this.flashUntil != null && now < this.flashUntil) {
      ctx.fillStyle = `rgba(255, 252, 236, ${this.flashUntil - now > 80 ? 0.2 : 0.07})`;
      ctx.fillRect(0, 0, ctx.canvas.width, ctx.canvas.height);
    }
  }
}
