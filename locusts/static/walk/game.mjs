import { COLORS, fromMap, Grid, PET } from "../minigame/pixels.mjs";
import {
  BACK, countdown, esc, FAIR, fetchRun, finishRun, fit, introFair, showOverlay, startButton,
} from "../minigame/shell.mjs";
import { canMove, lanesAt, newRun, result, step, stunned } from "./sim.mjs";

const W = 160, H = 240, LANE_W = 24, PATH_X = 20;
// Screen row of the pet's back edge. Everything else is placed relative to it.
const PET_REAR_Y = 212;
const TICK_MS = 1000 / 60;
const GAME = {
  id: "locust-walk",
  name: "Locust Walk",
  controls: "← → or A D to switch lanes. On a phone, tap either side.",
};

const canvas = document.getElementById("game");
const ctx = canvas.getContext("2d");
const hud = {
  time: document.getElementById("time"),
  distance: document.getElementById("distance"),
  hearts: document.getElementById("hearts"),
};

// --- Sprites, built once as small offscreen canvases. ---

const FLYER = fromMap([
  "....kkkk....",
  "...kkkkkk...",
  "...kkkkkk...",
  "....kkkk....",
  "..bbbbbbbb..",
  ".bbbbbbbbbb.",
  ".bbbbbbbbbbc",
  ".kbbbbbbbbcc",
  "..bbbbbbbb..",
  "...kk..kk...",
  "...kk..kk...",
]);

const SWAG = fromMap([
  ".kk....kk.",
  "kggk..kggk",
  "kggggggggk",
  ".kggggggk.",
  "..kgccgk..",
  "..kggggk..",
  "..kkkkkk..",
]);

function tourSprite() {
  const g = new Grid(44, 40);
  const person = (x, y, shirt) => { g.rect(x, y + 3, 8, 4, shirt); g.rect(x + 1, y + 3, 6, 1, "k"); g.rect(x + 2, y, 4, 4, "k"); };
  g.line(22, 0, 22, 9, "k");
  g.rect(23, 0, 6, 2, "b");
  g.rect(23, 2, 3, 2, "b");
  person(18, 6, "b");
  [[3, 14], [13, 15], [24, 14], [34, 15], [8, 23], [19, 24], [30, 23], [3, 32], [14, 32], [25, 33], [35, 32]]
    .forEach(([x, y], i) => person(x, y, i % 3 ? "g" : "b"));
  return g.canvas();
}

function bikeSprites() {
  const g = new Grid(20, 12);
  g.ellipse(4, 7, 4, 4, null, "k");
  g.ellipse(15, 7, 4, 4, null, "k");
  for (const [i, c] of [...g.px]) if (!c) g.px.delete(i);
  g.line(4, 7, 9, 7, "b"); g.line(9, 7, 13, 3, "b"); g.line(6, 3, 13, 3, "b");
  g.line(6, 3, 4, 7, "b"); g.line(13, 3, 15, 7, "b"); g.line(12, 1, 14, 1, "k"); g.line(13, 1, 13, 3, "b");
  g.rect(5, 2, 3, 1, "k");
  return { right: g.canvas(), left: g.canvas(true) };
}

function buttonSprite() {
  const g = new Grid(44, 32);
  g.ellipse(22, 16, 21, 15, "c", "k");
  g.ellipse(22, 16, 15, 10, "c", "k");
  for (const [x, y] of [[17, 12], [27, 12], [17, 20], [27, 20]]) g.ellipse(x, y, 2, 2, "k");
  g.line(30, 3, 34, 10, "k"); g.line(34, 10, 31, 15, "k");
  return g.canvas();
}

function loveSprite() {
  const glyphs = {
    L: ["k....", "k....", "k....", "k....", "k....", "k....", "kkkkk"],
    O: [".kkk.", "k...k", "k...k", "k...k", "k...k", "k...k", ".kkk."],
    V: ["k...k", "k...k", "k...k", "k...k", ".k.k.", ".k.k.", "..k.."],
    E: ["kkkkk", "k....", "k....", "kkkk.", "k....", "k....", "kkkkk"],
  };
  const g = new Grid(44, 32);
  const letter = (ch, x, y) => { g.map(glyphs[ch], x + 1, y + 1, 2, "k"); g.map(glyphs[ch], x, y, 2, "b"); };
  letter("L", 10, 1); letter("O", 23, 0);
  letter("V", 10, 17); letter("E", 23, 17);
  return g.canvas();
}

const SPRITES = { flyer: FLYER, swag: SWAG, tour: tourSprite(), button: buttonSprite(), love: loveSprite() };
const BIKE = bikeSprites();

// --- Drawing ---

function worldToScreen(yPx, rearPx) {
  return PET_REAR_Y - (yPx - rearPx);
}

function drawBackground(rearPx, lanes) {
  const pathW = lanes * LANE_W;
  ctx.fillStyle = COLORS.g;
  ctx.fillRect(0, 0, W, H);
  ctx.fillStyle = COLORS.c;
  ctx.fillRect(PATH_X, 0, pathW, H);
  for (let sy = 0; sy < H; sy++) {
    const wy = rearPx + (PET_REAR_Y - sy);
    // Grass dither and paving joints, anchored to the world so they scroll.
    ctx.fillStyle = COLORS.k;
    for (let x = 0; x < W; x++) {
      if (x === PATH_X) x += pathW;
      if (((Math.imul(wy, 73856093) ^ Math.imul(x, 19349663)) >>> 0) % 13 === 0) ctx.fillRect(x, sy, 1, 1);
    }
    ctx.fillStyle = COLORS.b;
    if (((wy % 12) + 12) % 12 === 0) {
      for (let x = PATH_X; x < PATH_X + pathW; x += 2) ctx.fillRect(x, sy, 1, 1);
    } else {
      const course = Math.floor(wy / 12) & 1;
      for (let x = PATH_X + (course ? 12 : 0); x < PATH_X + pathW; x += 24) if (wy % 2 === 0) ctx.fillRect(x, sy, 1, 1);
    }
  }
  // Trees along both edges every 80 world pixels.
  for (let wy = Math.floor((rearPx - 40) / 80) * 80; wy < rearPx + PET_REAR_Y + 40; wy += 80) {
    const sy = worldToScreen(wy, rearPx);
    const left = (wy / 80) % 2 === 0;
    const cx = left ? 6 : W - 7;
    ctx.fillStyle = COLORS.k;
    ctx.beginPath(); ctx.arc(cx, sy, 11, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = COLORS.g;
    for (let y = -9; y <= 9; y++) for (let x = -9; x <= 9; x++) {
      if (x * x + y * y <= 81 && (x + y) % 2 === 0) ctx.fillRect(cx + x, sy + y, 1, 1);
    }
  }
}

function drawObstacle(run, ob, rearPx) {
  const { config } = run;
  const yPx = ob.y / config.units_per_px, depthPx = ob.depth / config.units_per_px;
  const bottom = worldToScreen(yPx, rearPx);
  const top = bottom - depthPx;
  if (bottom < 0 || top > H) return;
  if (ob.kind === "bike") {
    const lanes = lanesAt(config, ob, run.tick);
    if (!lanes) return;
    const sprite = ob.direction > 0 ? BIKE.right : BIKE.left;
    ctx.drawImage(sprite, PATH_X + lanes[0] * LANE_W + 2, top);
    return;
  }
  const sprite = SPRITES[ob.kind];
  const x = PATH_X + ob.lane * LANE_W + Math.floor((ob.width * LANE_W - sprite.width) / 2);
  ctx.drawImage(sprite, x, top + Math.floor((depthPx - sprite.height) / 2));
}

function draw(run) {
  const { config, course } = run;
  const rearPx = Math.floor(run.rear / config.units_per_px);
  drawBackground(rearPx, config.lanes);
  const obstacles = course.obstacles;
  for (let i = Math.max(0, run.behind - 8); i < obstacles.length; i++) {
    const ob = obstacles[i];
    if (worldToScreen(ob.y / config.units_per_px, rearPx) < -60) break;
    if (!run.gone.has(i)) drawObstacle(run, ob, rearPx);
  }
  const blink = stunned(run) && Math.floor(run.tick / 4) % 2 === 0;
  if (!blink) {
    const frame = stunned(run) ? 0 : Math.floor(run.tick / 6) % 2;
    ctx.drawImage(PET[frame], PATH_X + run.lane * LANE_W + 6, PET_REAR_Y - config.pet_depth_px);
  }
  const r = result(run);
  const left = Math.max(0, Math.ceil((course.ticks - run.tick) / config.ticks_per_second));
  hud.time.textContent = `${left}s`;
  hud.distance.textContent = `${r.score} pts${r.swag ? ` · ${r.swag} swag` : ""}`;
  hud.hearts.textContent = "♥".repeat(config.max_hits - run.hits) + "♡".repeat(run.hits);
}

// --- Game flow ---

let state = "intro";
let run = null, inputs = [], queue = [];
let acc = 0, last = 0;
// The scored run being played in fair mode: its id and score multiplier.
let fairRun = null;

async function start() {
  if (state === "loading" || state === "countdown" || state === "running" || state === "submitting") return;
  if (FAIR && startButton.hidden) return;
  state = "loading";
  showOverlay(GAME.name, "Getting a course…");
  let course, config;
  try {
    ({ course, config, fairRun } = await fetchRun(GAME));
  } catch (e) {
    state = "intro";
    showOverlay("Couldn't start", `${esc(e.message)}${FAIR ? `<br>${BACK}` : ""}`, FAIR ? null : "Try again");
    return;
  }
  run = newRun(course, config);
  inputs = [];
  queue = [];
  draw(run);
  state = "countdown";
  await countdown();
  state = "running";
  acc = 0;
  last = performance.now();
  requestAnimationFrame(frame);
}

function tick() {
  let direction = 0;
  if (queue.length && !stunned(run) && run.tick >= run.nextMove) {
    direction = queue.shift();
    if (!canMove(run, direction)) direction = 0;
  }
  const at = run.tick;
  const events = step(run, direction);
  if (events.moved) inputs.push([at, direction]);
  if (events.hit) queue = [];
}

function frame(now) {
  acc += Math.min(now - last, 250);
  last = now;
  while (acc >= TICK_MS && !run.over) {
    tick();
    acc -= TICK_MS;
  }
  draw(run);
  if (run.over) finish();
  else requestAnimationFrame(frame);
}

async function finish() {
  const mine = result(run);
  const summary = `<div class="big">${mine.score}</div>
    ${mine.distance_px} ft walked${mine.swag ? ` + ${mine.swag} swag` : ""}, ${mine.hits} hit${mine.hits === 1 ? "" : "s"}`;
  const title = mine.hits >= run.config.max_hits ? "Knocked out" : "Made it";
  state = FAIR ? "submitting" : "done";
  await finishRun(GAME, { title, summary, mine, inputs, course: run.course, config: run.config, fairRun });
  state = "done";
}

function press(direction) {
  if (state === "running" && queue.length < 2) queue.push(direction);
}

addEventListener("keydown", (e) => {
  if (e.repeat) return;
  if (e.key === "ArrowLeft" || e.key === "a" || e.key === "A") { press(-1); e.preventDefault(); }
  else if (e.key === "ArrowRight" || e.key === "d" || e.key === "D") { press(1); e.preventDefault(); }
  else if ((e.key === " " || e.key === "Enter") && state !== "running") { start(); e.preventDefault(); }
});

canvas.addEventListener("pointerdown", (e) => {
  const box = canvas.getBoundingClientRect();
  press(e.clientX - box.left < box.width / 2 ? -1 : 1);
});

startButton.addEventListener("click", start);

fit(canvas, W, H);
drawBackground(0, 5);
if (FAIR) introFair(GAME);
