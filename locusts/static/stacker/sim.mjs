// Hey Day Stacker simulation. Mirrors locusts/minigames/hey_day_stacker.py tick for tick, so the server's
// replay of the logged drops scores a run exactly as the browser did.

export function newRun(course, config) {
  const field = config.field_px * config.units_per_px, block = config.block_px * config.units_per_px;
  return {
    course, config, field, block,
    topX: Math.floor((field - block) / 2), topW: block,
    tick: 0, floors: 0, perfects: 0, streak: 0, hits: 0, spawned: 0,
    x: null, direction: 0, nextSpawn: 0, over: false,
  };
}

export const canDrop = (run) => run.x !== null && !run.over;

export const speed = (run) =>
  Math.min(run.config.speed_max, run.config.speed_start + run.floors * run.config.speed_step);

// Advance one tick. Returns the drop as { kind, x, width } when one lands, otherwise null.
export function step(run, drop) {
  const c = run.config;
  let placed = null;
  if (run.x === null) {
    if (run.tick >= run.nextSpawn) {
      const side = run.course.sides[run.spawned++];
      run.x = side === 0 ? 0 : run.field - run.topW;
      run.direction = side === 0 ? 1 : -1;
    }
  } else if (drop) {
    placed = place(run);
    run.nextSpawn = run.tick + c.spawn_delay_ticks;
  } else {
    slide(run);
  }
  run.tick += 1;
  if (run.hits >= c.max_hits || run.tick >= run.course.ticks) run.over = true;
  return placed;
}

function slide(run) {
  const room = run.field - run.topW;
  let x = run.x + run.direction * speed(run);
  if (x < 0) { x = -x; run.direction = 1; }
  else if (x > room) { x = 2 * room - x; run.direction = -1; }
  run.x = x;
}

function place(run) {
  const c = run.config, u = c.units_per_px;
  const x = run.x, w = run.topW;
  const left = Math.max(x, run.topX), right = Math.min(x + w, run.topX + w);
  run.x = null;
  if (right - left < c.min_px * u) {
    run.hits += 1;
    run.streak = 0;
    return { kind: "miss", x, width: w };
  }
  run.floors += 1;
  if (Math.abs(x - run.topX) <= c.perfect_px * u) {
    run.perfects += 1;
    run.streak += 1;
    if (run.streak % c.grow_every === 0) {
      const grow = Math.min(c.grow_px * u, run.block - w);
      run.topW = w + grow;
      run.topX = Math.min(Math.max(0, run.topX - Math.floor(grow / 2)), run.field - run.topW);
    }
    return { kind: "perfect", x, width: w };
  }
  run.streak = 0;
  run.topX = left;
  run.topW = right - left;
  return { kind: "trim", x, width: w };
}

export function result(run) {
  const c = run.config;
  return {
    score: run.floors * c.floor_points + run.perfects * c.perfect_points,
    floors: run.floors, perfects: run.perfects, hits: run.hits, ticks_played: run.tick,
  };
}

export function replay(course, inputs, config) {
  const drops = new Set(inputs);
  const run = newRun(course, config);
  while (!run.over) step(run, drops.has(run.tick));
  return result(run);
}
