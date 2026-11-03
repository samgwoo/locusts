// Locust Walk simulation. Must match replay() in locusts/minigames/locust_walk.py tick for tick,
// which tests/test_walk_js.py checks.

export function speed(config, tick) {
  return Math.min(config.speed_max, config.speed_start + Math.floor(tick / config.speed_ramp_ticks));
}

// The lanes an obstacle covers at a tick, as [first, last], or null for a bike off the path.
export function lanesAt(config, ob, tick) {
  if (ob.kind === "bike") {
    const step = Math.floor((tick - ob.t0) / config.bike_ticks_per_lane);
    if (step < 0 || step >= config.lanes) return null;
    const lane = ob.lane + ob.direction * step;
    return [lane, lane];
  }
  return [ob.lane, ob.lane + ob.width - 1];
}

export function collides(config, ob, tick, rear, lane) {
  if (rear >= ob.y + ob.depth || rear + config.pet_depth <= ob.y) return false;
  const lanes = lanesAt(config, ob, tick);
  return lanes !== null && lane >= lanes[0] && lane <= lanes[1];
}

export function newRun(course, config) {
  return {
    course, config,
    tick: 0, lane: config.start_lane, rear: 0, hits: 0, swag: 0,
    nextMove: 0, stunnedUntil: 0, behind: 0, gone: new Set(),
    played: course.ticks, over: false,
  };
}

export function stunned(run) {
  return run.tick < run.stunnedUntil;
}

export function canMove(run, direction) {
  const lane = run.lane + direction;
  return !stunned(run) && run.tick >= run.nextMove && lane >= 0 && lane < run.config.lanes;
}

// Advances one tick, changing lanes first if `direction` (-1, 0, or 1) is allowed.
// Returns what happened so the client can log the move and play effects.
export function step(run, direction = 0) {
  const { config, course } = run;
  const events = { moved: false, hit: null, swag: null };
  const tick = run.tick;
  if (run.over) return events;
  if (!stunned(run)) {
    if (direction && canMove(run, direction)) {
      run.lane += direction;
      run.nextMove = tick + config.move_cooldown_ticks;
      events.moved = true;
    }
    run.rear += speed(config, tick);
    const obstacles = course.obstacles;
    while (run.behind < obstacles.length && obstacles[run.behind].y + obstacles[run.behind].depth <= run.rear) {
      run.behind++;
    }
    for (let i = run.behind; i < obstacles.length; i++) {
      const ob = obstacles[i];
      if (ob.y >= run.rear + config.pet_depth) break;
      if (run.gone.has(i) || !collides(config, ob, tick, run.rear, run.lane)) continue;
      run.gone.add(i);
      if (ob.kind === "swag") {
        run.swag++;
        events.swag = ob;
        continue;
      }
      run.hits++;
      run.stunnedUntil = tick + 1 + config.stun_ticks;
      events.hit = ob;
      break;
    }
    if (run.hits >= config.max_hits) {
      run.played = tick + 1;
      run.over = true;
    }
  }
  run.tick++;
  if (run.tick >= course.ticks) run.over = true;
  return events;
}

export function result(run) {
  const distance = Math.floor(run.rear / run.config.units_per_px);
  return {
    score: distance + run.swag * run.config.swag_px,
    distance_px: distance,
    hits: run.hits,
    swag: run.swag,
    ticks_played: run.played,
  };
}

export function replay(course, inputs, config) {
  const moves = new Map(inputs.map(([tick, direction]) => [tick, direction]));
  const run = newRun(course, config);
  while (!run.over) step(run, moves.get(run.tick) ?? 0);
  return result(run);
}
