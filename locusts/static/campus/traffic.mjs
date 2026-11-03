// Cars driving the campus streets, drawn into the map canvas. tools/render_campus.py writes the street graph
// (traffic.json, in map pixels, with each street's lane layout), the car sprites (cars.png: a row per heading,
// a column per paint), and occluders.png, the pixels of whatever stands in front of a street, which go back
// over each car.
//
// Distances and speeds are in ground meters, so a car crossing the foreshortened north-south streets keeps its
// pace. Cars keep to their lane and never overlap: each keeps back from the car ahead, including one turning
// into or out of its lane; turns only start when there's room past the intersection; intersections take one
// approach at a time, longest wait first, like a four-way stop; and parked cars wait for a gap to pull out.

const ACCEL = 2.5;      // m/s²
const BRAKE = 6;
const TURN_SPEED = 4.5; // m/s around a corner
const CROSS_SPEED = 7;  // m/s straight across an intersection
const CAR_M = 4.5;
const GAP_M = 2;        // bumper gap kept behind the car ahead
const LOOKAHEAD_M = 18; // how far before a corner a car picks where it's going
const CLEAR_M = CAR_M * 1.5 + GAP_M + 0.5;   // room a car needs past a corner to get its back end out of it
const PLATOON = 3;          // cars an approach sends through once it has the intersection, if they're coming
const PLATOON_HOLD_S = 5;   // how long the intersection waits for the next car of a platoon before moving on
const CHOOSE_M = 45;        // how far before a corner a car picks its turn, so it has room to change lanes for it
const MIN_TURN_M = 4;       // the tightest a turn rounds its corner, roughly its radius
const RESIDENT_M = 30;      // meters of parking strip per car that stays parked there for good
const PARK_END_M = 9;       // how far from either end of its lane a car can park
const BALANCE_S = 2;        // how often a car going straight looks for a shorter line in the next lane over
const BALANCE_M = 80;       // how far ahead it counts the cars in each line

const STYLES = {
  // Wanders the grid, mostly going straight.
  cruiser: { speed: [8, 11], straight: 3, right: 1, left: 1, stops: 0.3 },
  // Circles its blocks, always turning the same way when it can.
  looper: { speed: [6, 8], loop: true, stops: 0.6 },
  // Barrels straight across campus and off the map, then a new one drives in.
  commuter: { speed: [11, 14], straight: 8, right: 1, left: 1, stops: 0.05 },
  // Drives a while, pulls into a parking strip, sits, and heads off again.
  parker: { speed: [6, 9], straight: 2, right: 1, left: 1, stops: 0.5, parks: true },
};
const MIX = ["cruiser", "cruiser", "cruiser", "looper", "looper", "commuter", "commuter", "parker", "parker", "parker"];

const rand = (lo, hi) => lo + Math.random() * (hi - lo);
const pick = (list) => list[Math.floor(Math.random() * list.length)];

function weighted(options) {
  const total = options.reduce((sum, o) => sum + o.weight, 0);
  let r = Math.random() * total;
  for (const o of options) if ((r -= o.weight) <= 0) return o;
  return options[options.length - 1];
}

export class Traffic {
  constructor(data, cars, occluders, { still = false } = {}) {
    this.cars = cars;
    this.occluders = occluders;
    this.sheet = data.cars;
    this.gy = data.ground_y;
    this.pxPerM = data.px_per_m;
    this.land = data.land;
    this.bridges = data.bridges ?? [];
    this.clock = 0;
    this.nodes = data.nodes.map(([x, y, inside, setback]) => ({ x, y, inside: inside === 1, setback, out: [], edges: 0 }));
    for (const e of data.edges) {
      this.nodes[e.a].edges++;
      this.nodes[e.b].edges++;
    }
    this.lanes = [];
    const trims = this.mergeTrims(data.edges);
    data.edges.forEach((e, i) => {
      const [ta, tb] = [trims.get(`${i}:a`) ?? 0, trims.get(`${i}:b`) ?? 0];
      this.addLane(e, e.pts, e.a, e.b, e.fwd, e.park_fwd, ta, tb);
      if (e.back.length) this.addLane(e, [...e.pts].reverse(), e.b, e.a, e.back, e.park_back, tb, ta);
    });
    // Placing stop lines can turn lanes into parts of intersections, which grows the intersections, which moves
    // the stop lines, so repeat until it settles.
    for (let pass = 0, internal = -1; pass < 4; pass++) {
      this.placeStopLines();
      const now = this.lanes.filter((l) => l.internal).length;
      if (now === internal) break;
      internal = now;
      this.groupJunctions();
    }
    this.entries = this.lanes.filter((l) => !this.nodes[l.from].inside);
    const meters = this.lanes.reduce((sum, l) => sum + l.length * l.slots.length, 0);
    this.agents = [];
    const count = Math.min(300, Math.round(meters / 60));
    for (let i = 0; i < count * 4 && this.agents.length < count; i++) this.place(still);
    // Residents: cars parked along the curbs for good. They hold their spots like any parked car, so parkers
    // pull in only between them.
    const curbs = this.lanes.filter((l) => l.park !== null && !l.internal);
    const residents = Math.round(curbs.reduce((sum, l) => sum + l.length, 0) / RESIDENT_M);
    for (let i = 0, placed = 0; i < residents * 4 && placed < residents; i++) placed += this.placeResident(curbs);
  }

  // A one-way lane group along `pts` from node `from` to `to`: `slots` are its lanes' offsets right of the
  // centerline (curb-side first) and `park` its parking strip's. It's trimmed back from each end by the
  // node's intersection setback, so turns arc across the intersection instead of through its middle.
  addLane(edge, pts, from, to, slots, park, trimFrom, trimTo) {
    const trimmed = this.trim(pts, Math.max(this.nodes[from].setback, trimFrom), Math.max(this.nodes[to].setback, trimTo));
    const cum = [0];
    for (let i = 1; i < trimmed.length; i++) cum.push(cum[i - 1] + this.ground(trimmed[i - 1], trimmed[i]));
    // Too short to trim back from both of its intersections: a link inside one big intersection, like between
    // the two halves of a divided street.
    const internal = trimmed === pts && this.nodes[from].edges >= 3 && this.nodes[to].edges >= 3;
    const lane = { id: this.lanes.length, edge, pts: trimmed, cum, length: cum[cum.length - 1], from, to, slots, park,
      internal };
    if (lane.length < 1 || !slots.length) return;
    this.lanes.push(lane);
    this.nodes[from].out.push(lane);
  }

  // Where each lane's cars wait for their turn: as close to the corner as possible while staying clear of every
  // turn other approaches make through that intersection. A lane too short to wait in clear of them (say, a
  // stub between a bend and the far side of a wide street) becomes part of the intersection. Run before and
  // after groupJunctions, since a big intersection's turns all count against each of its stop lines.
  placeStopLines() {
    const paths = new Map();
    this.nodes.forEach((node, n) => {
      const list = paths.get(node.group) ?? (node.group ? paths.set(node.group, []).get(node.group) : []);
      if (!node.group) paths.set(node, list);
      for (const lane of this.lanes.filter((l) => l.to === n)) {
        for (const out of node.out) {
          if (out.edge === lane.edge) continue;
          lane.slots.forEach((_, k) => {
            const { at } = this.makePath(lane, lane.length, k, out, this.alongside(lane, k, out));
            list.push({ from: lane.id, pts: Array.from({ length: 11 }, (_, i) => this.meters(at(i / 10))) });
          });
        }
        // Turns that arc across a big intersection straight onto a street past it (see driven).
        for (const way of this.ways(lane)) {
          const [far, ...rest] = this.driven(lane, way);
          if (way.length === 1 || rest.length) continue;
          const left = this.turnKind(lane, far) === "left";
          const { at } = this.makePath(lane, lane.length, left ? lane.slots.length - 1 : 0, far, left ? far.slots.length - 1 : 0);
          list.push({ from: lane.id, pts: Array.from({ length: 11 }, (_, i) => this.meters(at(i / 10))) });
        }
      }
      // And the first stretch of every lane leaving, where cars that just came through stand in line.
      for (const out of node.out) {
        out.slots.forEach((off) => {
          const pts = [];
          for (let d = 0; d <= Math.min(CLEAR_M, out.length); d += 1.5) pts.push(this.meters(this.shifted(this.locate(out, d), off)));
          list.push({ from: -1, start: out.id, pts });
        });
      }
    });
    for (const lane of this.lanes) {
      const node = this.nodes[lane.to];
      const others = paths.get(node.group ?? node).filter((p) => p.from !== lane.id);
      const blocked = (k, s) => [s - CAR_M / 2, s, s + CAR_M / 2].some((d) => {
        const spot = this.meters(this.shifted(this.locate(lane, d), lane.slots[k]));
        return others.some((p) => p.pts.some((q) => Math.hypot(q.x - spot.x, q.y - spot.y) < 2.4));
      });
      lane.holds = lane.slots.map((_, k) => {
        let hold = Math.max(0, lane.length - CAR_M / 2 - 0.5);
        while (hold > CAR_M / 2 && blocked(k, hold)) hold -= 1;
        if (hold <= CAR_M / 2 && blocked(k, hold)) lane.internal = true;
        return hold;
      });
    }
  }

  // Junctions joined by internal links act as one intersection: one lock and one line of waiting cars.
  groupJunctions() {
    const root = this.nodes.map((_, i) => i);
    const find = (i) => (root[i] === i ? i : (root[i] = find(root[i])));
    for (const lane of this.lanes) if (lane.internal) root[find(lane.from)] = find(lane.to);
    const groups = new Map();
    this.nodes.forEach((node, i) => {
      const r = find(i);
      node.group = groups.get(r) ?? groups.set(r, { busy: new Set(), waiting: new Map(), size: 0 }).get(r);
      node.group.size++;
    });
  }

  // Where two streets leave a node at a sharp angle (a fork, a merge, a slip road), their lanes overlap for a
  // while. Trim both back, in map pixels keyed "edge:end", until their outer edges have pulled apart.
  mergeTrims(edges) {
    const ends = new Map();
    const length = (pts) => pts.slice(1).reduce((sum, p, i) => sum + Math.hypot(p[0] - pts[i][0], p[1] - pts[i][1]), 0);
    edges.forEach((e, i) => {
      const half = Math.max(0, ...[...e.fwd, ...e.back, e.park_fwd ?? 0, e.park_back ?? 0].map(Math.abs)) + this.pxPerM;
      for (const [end, node, p0, p1] of [["a", e.a, e.pts[0], e.pts[1]], ["b", e.b, e.pts.at(-1), e.pts.at(-2)]]) {
        const n = Math.hypot(p1[0] - p0[0], p1[1] - p0[1]) || 1;
        const list = ends.get(node) ?? ends.set(node, []).get(node);
        list.push({ key: `${i}:${end}`, ux: (p1[0] - p0[0]) / n, uy: (p1[1] - p0[1]) / n, half, length: length(e.pts) });
      }
    });
    const trims = new Map();
    for (const list of ends.values()) {
      for (let i = 0; i < list.length; i++) {
        for (let j = i + 1; j < list.length; j++) {
          const a = list[i], b = list[j];
          const angle = Math.acos(Math.max(-1, Math.min(1, a.ux * b.ux + a.uy * b.uy)));
          if (angle > 1.3) continue;
          const need = Math.min((a.half + b.half) / Math.sin(Math.max(angle, 0.1)), 0.45 * Math.min(a.length, b.length));
          for (const k of [a.key, b.key]) trims.set(k, Math.max(trims.get(k) ?? 0, need));
        }
      }
    }
    return trims;
  }

  ground([x0, y0], [x1, y1]) {
    return Math.hypot(x1 - x0, (y1 - y0) / this.gy) / this.pxPerM;
  }

  trim(pts, a, b) {
    const length = pts.slice(1).reduce((sum, p, i) => sum + Math.hypot(p[0] - pts[i][0], p[1] - pts[i][1]), 0);
    if (length < a + b + 6) return pts;
    const at = (d) => {
      for (let i = 1; i < pts.length; i++) {
        const [x0, y0] = pts[i - 1], [x1, y1] = pts[i];
        const seg = Math.hypot(x1 - x0, y1 - y0);
        if (d <= seg || i === pts.length - 1) return [[x0 + (x1 - x0) * (d / seg), y0 + (y1 - y0) * (d / seg)], i];
        d -= seg;
      }
    };
    const [start, i0] = at(a), [end, i1] = at(length - b);
    return [start, ...pts.slice(i0, i1), end];
  }

  // Where along `lane`'s centerline a point `s` meters in sits, and which way the lane runs there.
  locate(lane, s) {
    const { pts, cum } = lane;
    let i = 1;
    while (i < cum.length - 1 && cum[i] < s) i++;
    const [x0, y0] = pts[i - 1], [x1, y1] = pts[i];
    const f = cum[i] > cum[i - 1] ? Math.min(1, Math.max(0, (s - cum[i - 1]) / (cum[i] - cum[i - 1]))) : 0;
    const len = Math.hypot(x1 - x0, y1 - y0) || 1;
    let dx = (x1 - x0) / len, dy = (y1 - y0) / len;
    // Ease the facing across a bend in the street, over the last and first few meters of its segments.
    const dir = (j) => { const [ax, ay] = pts[j - 1], [bx, by] = pts[j], n = Math.hypot(bx - ax, by - ay) || 1; return [(bx - ax) / n, (by - ay) / n]; };
    const blend = (j, w) => { const [ex, ey] = dir(j); dx += (ex - dx) * w; dy += (ey - dy) * w; };
    if (i + 1 < pts.length && cum[i] - s < 3) blend(i + 1, 0.5 * (1 - (cum[i] - s) / 3));
    else if (i > 1 && s - cum[i - 1] < 3) blend(i - 1, 0.5 * (1 - (s - cum[i - 1]) / 3));
    const n = Math.hypot(dx, dy) || 1;
    return { x: x0 + (x1 - x0) * f, y: y0 + (y1 - y0) * f, dx: dx / n, dy: dy / n };
  }

  // The same point moved `off` pixels to the right of the centerline.
  shifted(p, off) {
    return [p.x - p.dy * off, p.y + p.dx * off];
  }

  fresh(car, lane, slot, s) {
    const style = pick(MIX);
    return Object.assign(car, {
      lane, slot, s, style, rule: STYLES[style], top: rand(...STYLES[style].speed), turn: null, next: null, plan: null,
      approach: null, clearing: null, route: null, shift: null, lateral: 0, parked: null, parkAt: null, parkS: null,
      pending: false,
      side: Math.random() < 0.5 ? 1 : -1, paint: Math.floor(Math.random() * this.sheet.paints), parkIn: rand(15, 60),
    });
  }

  // Seed a car somewhere clear: driving in a lane, or (some parkers, and everyone when motion is reduced)
  // already parked.
  place(still) {
    const lane = pick(this.lanes);
    if (lane.internal) return;
    const car = this.fresh({}, lane, Math.floor(Math.random() * lane.slots.length), rand(0, lane.length));
    if ((still || (car.rule.parks && Math.random() < 0.4)) && lane.park !== null) {
      if (!this.parkingFree(lane, car.s, null)) return;
      Object.assign(car, { slot: 0, lateral: 1, parked: still ? Infinity : rand(5, 60), parkS: car.s, speed: 0 });
    } else {
      if (still || this.agents.some((a) => a.lane === lane && a.slot === car.slot && a.parked === null
        && Math.abs(a.s - car.s) < CAR_M + GAP_M * 2)) return;
      car.speed = car.top * 0.6;
    }
    car.uid = this.agents.length;
    this.agents.push(car);
  }

  // Park a resident at a free spot somewhere along `curbs`, if the spot picked is free.
  placeResident(curbs) {
    const lane = pick(curbs), s = rand(0, lane.length);
    if (!this.parkingFree(lane, s, null)) return false;
    const car = this.fresh({}, lane, 0, s);
    Object.assign(car, { style: "resident", slot: 0, lateral: 1, parked: Infinity, parkS: s, speed: 0, uid: this.agents.length });
    this.agents.push(car);
    return true;
  }

  parkingFree(lane, s, self) {
    // Clear of both ends of the strip: turns swing over the curb at corners, and where a street bends its strip
    // runs into the next one's.
    if (s < PARK_END_M || s > Math.min(lane.length, ...lane.holds) - PARK_END_M) return false;
    return !this.agents.some((a) => a !== self && a.lane === lane
      && Math.abs((a.parkS ?? a.parkAt ?? -1e9) - s) < CAR_M + 1.2);
  }

  // Every way out of the intersection at the end of `lane`: each a list of the lanes driven, ending on a street
  // past it. Through a big intersection's internal links that's several, like across the middle of a divided
  // street and then onto its far half.
  ways(lane, depth = 0) {
    const node = this.nodes[lane.to];
    if (!node.inside) return [];
    let options = node.out.filter((l) => l.edge !== lane.edge);
    if (!options.length) options = node.out;
    return options.flatMap((l) => {
      const rest = l.internal && depth < 3 ? this.ways(l, depth + 1) : [];
      return rest.length ? rest.map((way) => [l, ...way]) : [[l]];
    });
  }

  // The lanes a car actually drives on `way` from the end of `lane`. Turning, it arcs from its lane straight onto
  // the street past the intersection, as a driver would: the links between a divided street's halves run between
  // their centerlines, so a car turning from the inside lane would swing out the wrong way to reach one first.
  // Going straight across, it drives the links.
  driven(lane, way) {
    return way.length > 1 && this.turnKind(lane, way.at(-1)) !== "straight" ? [way.at(-1)] : way;
  }

  // The lane a car takes on each lane of `way`, starting from lane `k` of `lane`: turning right from the curb
  // into the curb lane, left from the inside into the inside lane, straight on into whichever lines up.
  waySlots(lane, k, way, kind) {
    const slots = [];
    way.forEach((next, i) => {
      const n = next.slots.length;
      slots.push(kind === "right" || kind === "back" ? 0 : kind === "left" ? n - 1
        : this.alongside(i ? way[i - 1] : lane, i ? slots[i - 1] : k, next));
    });
    return slots;
  }

  // The lane of `to` that lane `k` of `from` runs on into going straight: the nearest one where `to` starts.
  // Lanes are counted from the curb, but a street that gains or loses a lane can do it on either side (Spruce
  // gains a curb lane before 38th), so matching them by number would jump cars sideways a whole lane.
  alongside(from, k, to) {
    const [px, py] = this.shifted(this.locate(from, from.length), from.slots[k]), q = this.locate(to, 0);
    let best = 0, bestD = Infinity;
    to.slots.forEach((off, j) => {
      const [x, y] = this.shifted(q, off), d = Math.hypot(x - px, y - py);
      if (d < bestD) [best, bestD] = [j, d];
    });
    return best;
  }

  // Which way a car turns going from the end of lane `a` to the start of lane `b`.
  turnKind(a, b) {
    const p = this.locate(a, a.length), q = this.locate(b, 0);
    const ax = p.dx, ay = p.dy / this.gy, bx = q.dx, by = q.dy / this.gy;
    const angle = Math.atan2(ax * by - ay * bx, ax * bx + ay * by);   // positive turns right
    return Math.abs(angle) < 0.6 ? "straight" : Math.abs(angle) > 2.6 ? "back" : angle > 0 ? "right" : "left";
  }

  // Where a car goes from the end of its lane, by its style. A way through a big intersection is picked whole and
  // weighed by the turn it makes from end to end, so going right onto a divided street's middle and right again
  // counts as the U-turn it is. Right turns go from the curb-side lane and left turns from the innermost, so
  // `need` is the lane the car has to be in (null for straight on, from any lane); it changes lanes on the way if
  // it can (see step). With `legalOnly`, only turns it can make from its lane now. Each hop is
  // { lane, slot, kind, need, plan }, `plan` being the hop after it through an internal link. Null drives off
  // the map.
  chooseNext(car, legalOnly = false) {
    const last = car.lane.slots.length - 1;
    const rule = car.rule;
    const ways = new Map(this.ways(car.lane).map((all) => {
      const way = this.driven(car.lane, all);
      return [way.map((l) => l.id).join(">"), way];
    }));
    const scored = [...ways.values()].map((way) => {
      const kind = this.turnKind(car.lane, way.at(-1));
      const need = kind === "right" || kind === "back" ? 0 : kind === "left" ? last : null;
      const slots = this.waySlots(car.lane, car.slot, way, kind);
      let plan = null;
      for (let i = way.length - 1; i >= 0; i--) {
        plan = { lane: way[i], slot: slots[i], kind: this.turnKind(i ? way[i - 1] : car.lane, way[i]), need: i ? null : need, plan };
      }
      plan.weight = kind === "back" ? 0.02
        : rule.loop ? (kind === (car.side > 0 ? "right" : "left") ? 10 : kind === "straight" ? 1 : 0.2) : rule[kind];
      return plan;
    });
    if (!scored.length) return null;
    const legal = scored.filter((o) => o.need === null || o.need === car.slot);
    return weighted(legalOnly && legal.length ? legal : scored);
  }

  // Cars sharing one lane, in order: those in it, those turning out of its end (counted past its end), and
  // those turning into its start (counted before it), so nobody drives into a car crossing a lane boundary.
  queues() {
    const queues = new Map();
    const add = (lane, slot, car, s) => {
      const key = `${lane.id}:${slot}`;
      (queues.get(key) ?? queues.set(key, []).get(key)).push({ car, s });
    };
    for (const car of this.agents) {
      car.ahead = null;
      // Parked cars are out of the lane, but one that has started pulling out is already in the way.
      const leaving = car.parked !== null && car.parked <= 0 && car.lateral < 1;
      if (car.pending || (car.lateral > 0.5 && !leaving)) continue;
      if (car.parked !== null) {
        add(car.lane, car.slot, car, car.parkS);
        continue;
      }
      if (car.turn) {
        add(car.lane, car.slot, car, car.turn.s0 + car.turn.s);
        add(car.turn.next, car.turn.slot, car, car.turn.entry + car.turn.s - car.turn.length);
      } else {
        add(car.lane, car.slot, car, car.s);
        // Halfway through a lane change it's in the way in both lanes.
        if (car.shift && car.shift.t < 0.7) add(car.lane, car.shift.old, car, car.s);
      }
    }
    for (const [key, queue] of queues) {
      queue.sort((p, q) => p.s - q.s);
      queue.forEach((entry, i) => {
        const { car } = entry;
        const own = car.turn ? `${car.turn.next.id}:${car.turn.slot}` : `${car.lane.id}:${car.slot}`;
        if (key !== own || i + 1 >= queue.length) return;
        car.ahead = { car: queue[i + 1].car, gap: queue[i + 1].s - entry.s - CAR_M - GAP_M };
      });
    }
    return queues;
  }

  // Whether a car at the end of its lane may start its turn now: there's room for it in its next lane, no
  // other approach holds the intersection or has waited longer, and nobody is standing in its path (which
  // catches what the graph can't, like a divided street's other half or two lanes merging at a sharp angle).
  canEnter(car, queues, near) {
    const room = (lane, slot, need, self) => !(queues.get(`${lane.id}:${slot}`) ?? []).some((e) => e.car !== self && e.s < need);
    const { lane: next, slot } = car.next;
    const node = this.nodes[car.lane.to], group = node.group;
    const path = this.turnPath(car);
    const clear = () => !near || this.pathClear(car, path);
    // Room to get all the way through, so a car never stops halfway and blocks the intersection. Through an
    // internal link that means room on the link and on the street past it, so plan that far now.
    if (next.internal) {
      car.next.plan ??= this.chooseNext({ ...car, lane: next, slot }) ?? "exit";
      const { plan } = car.next;
      if (!room(next, slot, CLEAR_M, car)) return false;
      if (plan !== "exit" && !room(plan.lane, plan.slot, CLEAR_M, car)) return false;
    } else if (!room(next, slot, CLEAR_M + path.entry, car)) {
      return false;
    }
    // A car on an internal link already holds its intersection.
    if (car.lane.internal || (node.edges < 3 && group.size === 1)) return clear();
    const route = this.route(car);
    // Cars already crossing go first if their routes cross this one's; ones that don't, like opposite
    // through-traffic, share the intersection.
    for (const other of group.busy) {
      // A car ahead in the same lane is the queue's business: following it through is how platoons move.
      if (other.approach === car.lane.id && other.approachSlot === car.slot) continue;
      if (other.route && this.crosses(route, this.remaining(other))) return false;
    }
    // The approach that has the intersection keeps it for a few cars, so traffic moves in platoons instead of
    // one car per approach at a time.
    if (this.inPlatoon(car)) return clear();
    const phase = group.phase;
    if (phase && this.clock - phase.at < PLATOON_HOLD_S && phase.count < PLATOON) {
      const coming = this.platoonNext(phase.approach, queues);
      if (coming && this.crosses(route, this.route(coming))) return false;
    }
    // Otherwise a car that has waited longer on another approach goes first, if their routes would cross and it
    // could actually go (yielding to one stuck behind a full lane would just stall both).
    const mine = group.waiting.get(car) ?? this.clock;
    for (const [other, since] of group.waiting) {
      if (other === car || other.lane.id === car.lane.id || since > mine - 0.3 || !other.next || other.next === "exit") continue;
      if (!room(other.next.lane, other.next.slot, CLEAR_M, other)) continue;
      if (this.crosses(route, this.route(other))) return false;
    }
    return clear();
  }

  // Whether the car's approach holds its intersection and hasn't yet sent its platoon through.
  inPlatoon(car) {
    const phase = this.nodes[car.lane.to].group.phase;
    return phase && phase.approach === car.lane.id && phase.count < PLATOON && this.clock - phase.at < PLATOON_HOLD_S;
  }

  // The next car coming up to the line on lane `id` (any of its lanes), if one is close enough to join a platoon.
  platoonNext(id, queues) {
    const lane = this.lanes[id];
    let best = null;
    lane.slots.forEach((_, k) => {
      for (const { car, s } of queues.get(`${id}:${k}`) ?? []) {
        if (car.turn || !car.next || car.next === "exit" || s < lane.holds[k] - 25) continue;
        if (!best || s > best.s) best = { car, s };
      }
    });
    return best?.car ?? null;
  }

  // Ground points along everything a car is about to drive through its intersection: the turn from where it is,
  // and through an internal link, the link and the turn after it.
  route(car) {
    const sample = ({ at }) => Array.from({ length: 11 }, (_, i) => this.meters(at(i / 10)));
    const { lane: next, slot, plan } = car.next;
    const pts = sample(this.turnPath(car));
    if (next.internal && plan && plan !== "exit") {
      for (let i = 1; i < 6; i++) pts.push(this.meters(this.shifted(this.locate(next, (next.length * i) / 6), next.slots[slot])));
      pts.push(...sample(this.makePath(next, next.length, slot, plan.lane, plan.slot)));
    }
    return pts;
  }

  // What's left of a crossing car's route: from the point nearest where it is now.
  remaining(car) {
    const spot = this.spots.get(car);
    if (!spot) return car.route;
    let best = 0, bestD = Infinity;
    car.route.forEach((p, i) => {
      const d = Math.hypot(p.x - spot.x, p.y - spot.y);
      if (d < bestD) [best, bestD] = [i, d];
    });
    return car.route.slice(best);
  }

  // Whether two routes come within a car's width (plus a little) of each other.
  crosses(a, b) {
    return a.some((p) => b.some((q) => Math.hypot(p.x - q.x, p.y - q.y) < 2.2));
  }

  pathClear(car, { at }) {
    const me = this.spots.get(car);
    for (const [other, spot] of this.spots) {
      if (other === car || (other.lateral > 0.5 && other.parked !== null)) continue;
      if ((spot.x - me.x) * me.hx + (spot.y - me.y) * me.hy < -2) continue;   // well behind this car
      for (let i = 2; i <= 12; i++) {
        const p = this.meters(at(i / 12));
        if (Math.hypot(spot.x - p.x, spot.y - p.y) < 2.4) return false;
      }
    }
    return true;
  }

  // Map pixels to ground meters, so distances on the foreshortened map are real ones.
  meters([x, y]) {
    return { x: x / this.pxPerM, y: y / (this.pxPerM * this.gy) };
  }

  turnPath(car) {
    return this.makePath(car.lane, car.s, car.slot, car.next.lane, car.next.slot);
  }

  // The way from `s` meters along `lane` in lane `from`, across the junction, into `next` in `slot`, joining it
  // `entry` meters in. Rounding a corner (the two lanes' lines cross ahead of the car), it drives it the way a
  // road is built: see cornerPath. Otherwise (nearly straight on, or turning back), a cubic whose handles run
  // along each lane, so the car leaves its lane and enters the next one facing the way they go (a single control
  // point can land past the far lane and flip the car around).
  makePath(lane, s, from, next, slot) {
    const a = this.locate(lane, s);
    let b = this.locate(next, 0);
    const p0 = this.shifted(a, lane.slots[from]);
    let p3 = this.shifted(b, next.slots[slot]);
    // How far ahead of the car the lanes' lines cross, and how far back from the far end; null if they don't.
    const legs = () => {
      const det = a.dx * b.dy - a.dy * b.dx, dx = p3[0] - p0[0], dy = p3[1] - p0[1];
      const u = (dx * b.dy - dy * b.dx) / det, v = (a.dx * dy - a.dy * dx) / det;
      return Math.abs(det) > 0.2 && u > 0 && v > 0 ? [u, v] : null;
    };
    let corner = legs(), entry = 0;
    // A street starting right at the corner leaves no room to round it, so the car joins farther in.
    const short = corner ? MIN_TURN_M * this.pxPerM - corner[1] : 0;
    if (short > 0) {
      entry = Math.min(this.ground([0, 0], [b.dx * short, b.dy * short]), Math.max(0, next.length - CAR_M));
      b = this.locate(next, entry);
      p3 = this.shifted(b, next.slots[slot]);
      corner = legs();
    }
    if (corner) return this.cornerPath(p0, a, p3, b, ...corner, entry);
    const dx = p3[0] - p0[0], dy = p3[1] - p0[1];
    const k = Math.hypot(dx, dy) * 0.4;
    const p1 = [p0[0] + a.dx * k, p0[1] + a.dy * k], p2 = [p3[0] - b.dx * k, p3[1] - b.dy * k];
    const at = (t) => {
      const u = 1 - t, w0 = u * u * u, w1 = 3 * u * u * t, w2 = 3 * u * t * t, w3 = t * t * t;
      return [w0 * p0[0] + w1 * p1[0] + w2 * p2[0] + w3 * p3[0], w0 * p0[1] + w1 * p1[1] + w2 * p2[1] + w3 * p3[1]];
    };
    // Which way the car faces along the arc, falling back to the lanes' own directions at a degenerate end.
    const facing = (t) => {
      const u = 1 - t;
      const dx = 3 * u * u * (p1[0] - p0[0]) + 6 * u * t * (p2[0] - p1[0]) + 3 * t * t * (p3[0] - p2[0]);
      const dy = 3 * u * u * (p1[1] - p0[1]) + 6 * u * t * (p2[1] - p1[1]) + 3 * t * t * (p3[1] - p2[1]);
      const n = Math.hypot(dx, dy);
      return n > 1e-6 ? [dx / n, dy / n] : t < 0.5 ? [a.dx, a.dy] : [b.dx, b.dy];
    };
    let length = 0;
    for (let i = 1; i <= 10; i++) length += this.ground(at((i - 1) / 10), at(i / 10));
    return { at, facing, length: Math.max(length, 0.5), entry };
  }

  // Around a corner the lanes' lines cross `u` px ahead of p0 (heading `a`) and `v` px back from p3 (heading
  // `b`): straight in, an even curve as wide as the shorter side allows, and straight out. So a lopsided corner,
  // like turning into a street that starts barely off to the side, never swings the car out the wrong way first
  // or pivots it on the spot.
  cornerPath(p0, a, p3, b, u, v, entry) {
    const r = Math.min(u, v);
    const c = [p0[0] + a.dx * u, p0[1] + a.dy * u];
    const A = [c[0] - a.dx * r, c[1] - a.dy * r], B = [c[0] + b.dx * r, c[1] + b.dy * r];
    const curve = (q) => {
      const w = 1 - q;
      return [w * w * A[0] + 2 * w * q * c[0] + q * q * B[0], w * w * A[1] + 2 * w * q * c[1] + q * q * B[1]];
    };
    const l1 = this.ground(p0, A), l3 = this.ground(B, p3);
    let l2 = 0;
    for (let i = 1; i <= 10; i++) l2 += this.ground(curve((i - 1) / 10), curve(i / 10));
    const length = l1 + l2 + l3;
    const lerp = (p, q, f) => [p[0] + (q[0] - p[0]) * f, p[1] + (q[1] - p[1]) * f];
    const at = (t) => {
      const d = t * length;
      if (d <= l1) return lerp(p0, A, l1 ? d / l1 : 0);
      if (d <= l1 + l2) return curve(l2 ? (d - l1) / l2 : 0);
      return lerp(B, p3, l3 ? (d - l1 - l2) / l3 : 1);
    };
    const facing = (t) => {
      const d = t * length;
      if (d <= l1 && l1) return [a.dx, a.dy];
      if (d >= l1 + l2 && l3) return [b.dx, b.dy];
      const q = l2 ? Math.min(1, Math.max(0, (d - l1) / l2)) : 0;
      const dx = 2 * (1 - q) * (c[0] - A[0]) + 2 * q * (B[0] - c[0]), dy = 2 * (1 - q) * (c[1] - A[1]) + 2 * q * (B[1] - c[1]);
      const n = Math.hypot(dx, dy);
      return n > 1e-6 ? [dx / n, dy / n] : [a.dx, a.dy];
    };
    return { at, facing, length: Math.max(length, 0.5), entry };
  }

  // Drive from where the car waits at the line, across the intersection, into its next lane. The car holds the
  // intersection until its back end is clear of it.
  startTurn(car) {
    const { lane: next, slot } = car.next;
    const { at, facing, length, entry } = this.turnPath(car);
    const node = this.nodes[car.lane.to], group = node.group;
    if (!car.lane.internal) {
      car.route = this.route(car);
      car.approach = car.lane.id;
      car.approachSlot = car.slot;
      if (node.edges >= 3 || group.size > 1) {
        const phase = group.phase;
        group.phase = phase && phase.approach === car.lane.id && this.clock - phase.at < PLATOON_HOLD_S
          ? { ...phase, count: phase.count + 1, at: this.clock } : { approach: car.lane.id, count: 1, at: this.clock };
      }
    }
    car.clearing?.busy.delete(car);
    car.clearing?.waiting.delete(car);
    car.turn = { at, facing, length, entry, s: 0, s0: car.s, next, slot,
      speed: car.next.kind === "straight" ? CROSS_SPEED : TURN_SPEED };
    car.clearing = group;
    car.parkAt = null;
    group.busy.add(car);
    group.waiting.delete(car);
    // Through an internal link the car keeps the plan it made to get past it.
    car.plan = next.internal ? car.next.plan : null;
    car.next = null;
  }

  update(dt) {
    dt = Math.min(dt, 0.1);
    this.clock += dt;
    const queues = this.queues();
    // Every car's ground position and heading, for checking turn paths.
    this.spots = new Map();
    for (const car of this.agents) {
      if (car.pending) continue;
      const p = this.pose(car), m = this.meters([p.x, p.y]);
      const hx = p.dx, hy = p.dy / this.gy, n = Math.hypot(hx, hy) || 1;
      this.spots.set(car, { x: m.x, y: m.y, hx: hx / n, hy: hy / n });
    }
    for (const car of this.agents) this.step(car, dt, queues);
  }

  follow(car, target) {
    if (!car.ahead) return target;
    // A car turning ahead swings its back end out, so leave it an extra meter.
    const gap = car.ahead.gap - (car.ahead.car.turn ? 1 : 0);
    return Math.min(target, Math.sqrt(2 * BRAKE * 0.5 * Math.max(0, gap)), gap < 3 ? car.ahead.car.speed : target);
  }

  accelerate(car, target, dt) {
    car.speed = target > car.speed ? Math.min(target, car.speed + ACCEL * dt) : Math.max(target, car.speed - BRAKE * dt);
  }

  step(car, dt, queues) {
    if (car.pending) return this.reenter(car, queues);
    if (car.parked !== null) return this.stepParked(car, dt, queues);
    if (car.turn) {
      this.accelerate(car, this.follow(car, car.turn.speed), dt);
      car.turn.s += car.speed * dt;
      if (car.turn.s >= car.turn.length) {
        Object.assign(car, { lane: car.turn.next, slot: car.turn.slot, s: car.turn.entry + car.turn.s - car.turn.length, turn: null });
      }
      return;
    }
    const lane = car.lane;
    if (car.clearing && !lane.internal && car.s > CAR_M / 2 + 0.5) {
      car.clearing.busy.delete(car);
      car.clearing = null;
    }
    if (car.next === null && (lane.length - car.s < CHOOSE_M || lane.internal)) {
      car.next = (lane.internal ? car.plan : null) ?? this.chooseNext(car) ?? "exit";
      car.plan = null;
      car.stopFor = !lane.internal && this.nodes[lane.to].edges >= 3 && Math.random() < car.rule.stops ? rand(0.4, 1.4) : 0;
    }
    // Change lanes toward the one the turn goes from, one lane at a time and only into a gap; too close to the
    // corner to make it, pick a turn this lane can make instead.
    const need = car.next && car.next !== "exit" ? car.next.need : null;
    if (need !== null && need !== car.slot && !car.shift && !lane.internal && car.parkAt === null) {
      const left = lane.holds[car.slot] - car.s;
      if (left > 20) this.changeLane(car, car.slot + Math.sign(need - car.slot), queues);
      else if (left < 10) car.next = this.chooseNext(car, true) ?? "exit";
    }
    // Going straight on (or not decided yet), any lane will do, so take the shorter line: every so often, count
    // the cars ahead in this lane and beside it, and move over if a neighbor's is clearly shorter.
    if (need === null && !car.shift && !lane.internal && car.parkAt === null && lane.slots.length > 1
        && lane.holds[car.slot] - car.s > 20 && this.clock >= (car.balanceAt ??= this.clock + rand(0, BALANCE_S))) {
      car.balanceAt = this.clock + BALANCE_S;
      const ahead = (k) => (queues.get(`${lane.id}:${k}`) ?? [])
        .filter((e) => e.car !== car && e.s > car.s && e.s < Math.min(lane.length, car.s + BALANCE_M)).length;
      const mine = ahead(car.slot);
      const best = [car.slot - 1, car.slot + 1].filter((k) => k >= 0 && k < lane.slots.length)
        .map((k) => ({ k, n: ahead(k) })).sort((a, b) => a.n - b.n)[0];
      if (best && mine - best.n >= 2) this.changeLane(car, best.k, queues);
    }
    if (car.shift && (car.shift.t += dt / 1.4) >= 1) car.shift = null;
    if (car.stopFor > 0 && this.inPlatoon(car)) car.stopFor = 0;
    let target = car.top;
    const turning = car.next && car.next !== "exit";
    // Where the car waits for its turn (see placeStopLines).
    const hold = turning ? lane.holds[car.slot] : lane.length;
    let go = false;
    if (turning) {
      // Slow for the corner, and plan to stop at the line unless it's clear to go straight through.
      go = car.stopFor <= 0 && this.canEnter(car, queues, hold - car.s < 6);
      const through = car.next.kind === "straight" ? CROSS_SPEED : TURN_SPEED;
      target = Math.min(target, Math.sqrt((go ? through ** 2 : 0) + 2 * BRAKE * 0.5 * Math.max(0, hold - car.s)));
      if (!go && hold - car.s > 0.5 && hold - car.s < 12 && this.holdBlocked(car, hold)) {
        target = Math.min(target, Math.sqrt(2 * BRAKE * 0.5 * Math.max(0, hold - 5.5 - car.s)));
      }
    }
    if (car.rule.parks && car.parkAt === null && car.slot === 0 && lane.park !== null) {
      car.parkIn -= dt;
      // Short of where it would stop for the corner, or it would turn first and park in the middle of the next street.
      const at = car.s + 12;
      if (car.parkIn <= 0 && at < Math.min(lane.length - 6, hold - 3) && this.parkingFree(lane, at, car)) car.parkAt = at;
    }
    if (car.parkAt !== null) {
      const to = car.parkAt - car.s;
      target = Math.min(target, Math.sqrt(2 * BRAKE * 0.4 * Math.max(0, to)));
      if (to <= 0.3) {
        Object.assign(car, { speed: 0, parkS: car.s, parkAt: null, parked: rand(20, 70) });
        return;
      }
    }
    this.accelerate(car, this.follow(car, target), dt);
    car.s = Math.min(hold, car.s + car.speed * dt);
    if (turning && go && car.s >= hold - 4) return this.startTurn(car);
    if (car.s < hold) return;
    if (!turning) {
      car.clearing?.busy.delete(car);
      Object.assign(car, { pending: true, speed: 0, clearing: null });
      return;
    }
    // At the line: wait out a stop, then for a clear turn.
    const { group } = this.nodes[lane.to];
    if (!lane.internal && !group.waiting.has(car)) group.waiting.set(car, this.clock);
    car.speed = 0;
    if (car.stopFor > 0) car.stopFor -= dt;
    else if (this.canEnter(car, queues, true)) this.startTurn(car);
  }

  // Move over into lane `k`, only into a gap the cars around can keep: room for this car to brake for whatever
  // is ahead in the new lane, and for whatever is coming up behind to brake for it (at follow's half braking).
  changeLane(car, k, queues) {
    const braking = (fast, slow) => Math.max(0, fast - slow) ** 2 / BRAKE;
    const busy = (queues.get(`${car.lane.id}:${k}`) ?? []).some(({ car: o, s }) => {
      if (o === car) return false;
      return s >= car.s ? s - car.s < CAR_M + GAP_M + 1 + braking(car.speed, o.speed)
        : car.s - s < CAR_M + GAP_M + 3 + braking(o.speed, car.speed);
    });
    if (busy) return;
    car.shift = { from: car.lane.slots[car.slot], old: car.slot, t: 0 };
    car.slot = k;
    // Going straight through, it stays in the lane it moved to the whole way.
    if (car.next && car.next !== "exit" && car.next.need === null) {
      let prev = car.lane, slot = k;
      for (let hop = car.next; hop && hop !== "exit"; prev = hop.lane, slot = hop.slot, hop = hop.plan) {
        hop.slot = this.alongside(prev, slot, hop.lane);
      }
    }
    // Claim the gap now, so another car doesn't change into it this same frame.
    const key = `${car.lane.id}:${k}`;
    (queues.get(key) ?? queues.set(key, []).get(key)).push({ car, s: car.s });
  }

  stepParked(car, dt, queues) {
    if (car.parked > 0) {
      car.lateral = Math.min(1, car.lateral + dt / 1.5);
      car.parked -= dt;
      return;
    }
    // Pull out only into a gap, so nobody coming up the curb lane runs into it.
    if (car.lateral >= 1) {
      const key = `${car.lane.id}:0`;
      const lane = queues.get(key) ?? queues.set(key, []).get(key);
      if (lane.some((e) => e.car !== car && e.s > car.parkS - 15 && e.s < car.parkS + CAR_M + GAP_M)) return;
      lane.push({ car, s: car.parkS });   // claim the gap now, so nobody changes lanes into it this frame
    }
    car.lateral = Math.max(0, car.lateral - dt / 1.5);
    if (car.lateral === 0) Object.assign(car, { parked: null, parkS: null, parkIn: rand(30, 90), s: car.parkS ?? car.s });
  }

  // A car that drove off the map comes back as a new one driving in from an edge, once there's room.
  reenter(car, queues) {
    if (!this.entries.length) return;
    const lane = pick(this.entries), slot = Math.floor(Math.random() * lane.slots.length);
    const key = `${lane.id}:${slot}`;
    if ((queues.get(key) ?? []).some((e) => e.s < CAR_M + GAP_M * 3)) return;
    this.fresh(car, lane, slot, 0);
    car.speed = car.top * 0.7;
    (queues.get(key) ?? queues.set(key, []).get(key)).push({ car, s: 0 });
  }

  // Whether the spot where a car would wait at the line lies on a turn another approach is driving right now
  // (two halves of a divided street are separate nodes, so the intersection lock doesn't cover it).
  holdBlocked(car, hold) {
    const p = this.locate(car.lane, hold);
    const spot = this.meters(this.shifted(p, car.lane.slots[car.slot]));
    for (const other of this.agents) {
      if (!other.turn || (other.approach === car.lane.id && other.approachSlot === car.slot)) continue;
      const { at, s, length } = other.turn;
      for (let t = s / length; t <= 1; t += 0.1) {
        const q = this.meters(at(Math.min(1, t)));
        if (Math.hypot(q.x - spot.x, q.y - spot.y) < 2.6) return true;
      }
    }
    return false;
  }

  // The car's map position and facing, eased toward the parking strip while it parks.
  pose(car) {
    if (car.turn) {
      const { at, facing, s, length } = car.turn;
      const t = Math.min(1, s / length), [x, y] = at(t), [dx, dy] = facing(t);
      return { x, y, dx, dy };
    }
    const p = this.locate(car.lane, car.parkS ?? car.s);
    let drive = car.lane.slots[car.slot];
    if (car.shift) {
      const t = car.shift.t, ease = t * t * (3 - 2 * t);
      drive = car.shift.from + (drive - car.shift.from) * ease;
    }
    const [x, y] = this.shifted(p, drive + ((car.lane.park ?? drive) - drive) * car.lateral);
    return { x, y, dx: p.dx, dy: p.dy };
  }

  // Whether a car at map point (x, y) is passing under a bridge, where it can't be seen.
  underBridge(car, x, y) {
    const layer = (car.turn ? car.turn.next : car.lane).edge.layer ?? 0;
    return this.bridges.some(([bx, by, r, top]) => top > layer && Math.hypot(x - bx, y - by) < r);
  }

  // Whether map point (x, y) is behind something standing in front of the street (occluders.png, loaded as a
  // SparseImage with its mask).
  occluded(x, y) {
    return this.occluders.opaque(Math.round(x), Math.round(y));
  }

  // Whether map point (x, y) is on the island, where cars are drawn at all.
  onLand(x, y) {
    let inside = false;
    const L = this.land;
    for (let i = 0, j = L.length - 1; i < L.length; j = i++) {
      const [xi, yi] = L[i], [xj, yj] = L[j];
      if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
    }
    return inside;
  }

  // Draws the cars in map pixels: the caller has set view.mapTransform on ctx.
  draw(ctx, view, x0, y0, x1, y1) {
    const [cw, ch] = this.sheet.cell;
    const shown = [];
    for (const car of this.agents) {
      if (car.pending) continue;
      const p = this.pose(car);
      car.facing = [p.dx, p.dy];
      if (p.x < x0 - cw || p.y < y0 - ch || p.x > x1 + cw || p.y > y1 + ch || this.underBridge(car, p.x, p.y)) continue;
      const angle = Math.atan2(-p.dy / this.gy, p.dx);
      const n = this.sheet.headings;
      const heading = ((Math.round(angle / (2 * Math.PI / n)) % n) + n) % n;
      const [ax, ay] = this.sheet.anchors[heading];
      shown.push({ car, heading, x: Math.round(p.x) - ax, y: Math.round(p.y) - ay, foot: p.y });
    }
    if (!shown.length) return;
    shown.sort((a, b) => a.foot - b.foot);
    ctx.save();
    ctx.beginPath();
    for (const [x, y] of this.land) ctx.lineTo(x, y);
    ctx.closePath();
    ctx.clip();
    for (const { car, heading, x, y } of shown) ctx.drawImage(this.cars, car.paint * cw, heading * ch, cw, ch, x, y, cw, ch);
    for (const { x, y } of shown) this.occluders.draw(ctx, x, y, cw, ch);
    ctx.restore();
  }
}
