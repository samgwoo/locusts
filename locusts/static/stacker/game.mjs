import { COLORS, fromMap } from "../minigame/pixels.mjs";
import {
  BACK, countdown, esc, FAIR, fetchRun, finishRun, fit, introFair, showOverlay, startButton,
} from "../minigame/shell.mjs";
import { canDrop, newRun, result, step } from "./sim.mjs";

const W = 160, H = 240, FIELD_X = 8, GROUND_Y = 222;
// The camera keeps the sliding block about this far above the bottom of the screen.
const CAMERA_LIFT = 120;
const TICK_MS = 1000 / 60;
const GAME = {
  id: "hey-day-stacker",
  name: "Hey Day Stacker",
  controls: "Space, a click, or a tap drops the block.",
};
// Before a run there's no config yet, so the opening scene uses the default sizes.
const PREVIEW = { field: 144, block: 40, height: 8 };

const canvas = document.getElementById("game");
const ctx = canvas.getContext("2d");
const hud = {
  time: document.getElementById("time"),
  score: document.getElementById("score"),
  hearts: document.getElementById("hearts"),
};

// The pet in profile, in its Hey Day straw hat.
const PET = fromMap([
  "....cccc....",
  "....bbbb....",
  "..cccccccc..",
  "....kkkk....",
  "...kggckk...",
  "..kggggggk..",
  ".kgggggggk..",
  "kggggggggkk.",
  ".k.k..k.k...",
  "k...k.k..k..",
]);

// College Hall, which the tower climbs past.
const HALL = fromMap([
  "..........k..........",
  ".........kkk.........",
  ".........kck.........",
  "........kkkkk........",
  "........kckck........",
  "........kkkkk........",
  "......kkkkkkkkk......",
  "......kckckckck......",
  "......kkkkkkkkk......",
  "kkkkkkkkkkkkkkkkkkkkk",
  "kckckkckckkkckckkckck",
  "kkkkkkkkkkkkkkkkkkkkk",
  "kckckkckckkkckckkckck",
  "kkkkkkkkkkkkkkkkkkkkk",
  "kckckkckckcckckckkckc",
  "kkkkkkkkkkcckkkkkkkkk",
]);

// --- Drawing ---

let state = "intro";
let run = null, inputs = [], pending = false;
let acc = 0, last = 0;
// The scored run being played in fair mode: its id and score multiplier.
let fairRun = null;
// What's drawn, in pixels: the tower's floors, trimmed pieces falling off, and floating labels.
let tower = [], pieces = [], popups = [];
let camera = 0, shake = 0;

const screenY = (worldY) => GROUND_Y - worldY + camera;

function drawBackground(height) {
  ctx.fillStyle = COLORS.c;
  ctx.fillRect(0, 0, W, H);
  // Bunting strung across the sky every 120 pixels of height.
  ctx.fillStyle = COLORS.b;
  for (let wy = 140; wy < camera + GROUND_Y + 40; wy += 120) {
    const sy = Math.round(screenY(wy));
    if (sy < -10 || sy > H) continue;
    for (let x = 0; x < W; x += 10) for (let j = 0; j < 5; j++) ctx.fillRect(x + j, sy + j, 10 - 2 * j, 1);
  }
  ctx.drawImage(HALL, W - HALL.width - 4, Math.round(screenY(0)) - HALL.height);
  ctx.fillStyle = COLORS.g;
  ctx.fillRect(0, Math.round(screenY(0)), W, H);
  ctx.fillStyle = COLORS.k;
  for (let sy = Math.round(screenY(0)) + 2; sy < H; sy += 3) {
    for (let x = (sy * 7) % 5; x < W; x += 5) ctx.fillRect(x, sy, 1, 1);
  }
  // Height marks every ten floors along the left edge.
  ctx.fillStyle = COLORS.k;
  for (let floor = 10; floor * height < camera + GROUND_Y; floor += 10) {
    const sy = Math.round(screenY(floor * height));
    if (sy >= 0 && sy < H) ctx.fillRect(0, sy, floor % 50 ? 3 : 6, 1);
  }
}

function drawBlock(x, worldY, w, h, kind) {
  const sx = Math.round(FIELD_X + x), sy = Math.round(screenY(worldY + h)), sw = Math.max(1, Math.round(w));
  if (sy > H || sy + h < 0) return;
  ctx.fillStyle = COLORS.k;
  ctx.fillRect(sx, sy, sw, h);
  ctx.fillStyle = kind === "base" ? COLORS.c : kind === "perfect" ? COLORS.g : COLORS.b;
  ctx.fillRect(sx + 1, sy + 1, sw - 2, h - 2);
  if (kind !== "base" && sw > 4) {
    ctx.fillStyle = COLORS.c;
    ctx.fillRect(sx + 1, sy + 1, sw - 2, 1);
  }
}

function drawScene(height) {
  const jolt = shake ? (shake % 2 ? 1 : -1) : 0;
  ctx.save();
  ctx.translate(jolt, 0);
  drawBackground(height);
  tower.forEach((f, i) => drawBlock(f.x, i * height, f.w, height, i ? f.kind : "base"));
  const top = tower[tower.length - 1];
  if (top) {
    const bob = state === "running" && Math.floor(performance.now() / 300) % 2 ? 1 : 0;
    ctx.drawImage(PET, Math.round(FIELD_X + top.x + top.w / 2 - PET.width / 2),
      Math.round(screenY(tower.length * height)) - PET.height - bob);
  }
  for (const p of pieces) drawBlock(p.x, p.y, p.w, height, "trim");
  ctx.font = "8px ui-monospace, monospace";
  ctx.textAlign = "center";
  for (const p of popups) {
    ctx.fillStyle = p.color;
    ctx.fillText(p.text, p.x, Math.round(screenY(p.y)));
  }
  ctx.restore();
}

function draw() {
  const { config } = run;
  const u = config.units_per_px, height = config.block_height_px;
  camera += (Math.max(0, (run.floors + 2) * height - CAMERA_LIFT) - camera) * 0.12;
  drawScene(height);
  if (run.x !== null) drawBlock(run.x / u, (run.floors + 1) * height, run.topW / u, height, "trim");
  const r = result(run);
  const left = Math.max(0, Math.ceil((run.course.ticks - run.tick) / config.ticks_per_second));
  hud.time.textContent = `${left}s`;
  hud.score.textContent = `${r.score} pts · ${r.floors} fl`;
  hud.hearts.textContent = "♥".repeat(config.max_hits - run.hits) + "♡".repeat(run.hits);
}

function animate() {
  for (const p of pieces) { p.vy += 0.3; p.y -= p.vy; }
  pieces = pieces.filter((p) => screenY(p.y) < H + 20);
  for (const p of popups) { p.y += 0.4; p.life -= 1; }
  popups = popups.filter((p) => p.life > 0);
  if (shake) shake -= 1;
}

function preview() {
  const { field, block, height } = PREVIEW;
  tower = [{ x: (field - block) / 2, w: block, kind: "base" }];
  drawScene(height);
}

// --- Game flow ---

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
  pending = false;
  const u = config.units_per_px;
  tower = [{ x: run.topX / u, w: run.topW / u, kind: "base" }];
  pieces = [];
  popups = [];
  camera = 0;
  draw();
  state = "countdown";
  await countdown();
  state = "running";
  acc = 0;
  last = performance.now();
  requestAnimationFrame(frame);
}

function tick() {
  const drop = pending && canDrop(run);
  pending = false;
  const at = run.tick;
  const placed = step(run, drop);
  if (drop) inputs.push(at);
  if (placed) land(placed);
}

// Add a landed block to the drawing, with whatever fell off it.
function land(placed) {
  const { config } = run;
  const u = config.units_per_px, height = config.block_height_px;
  const x = placed.x / u, w = placed.width / u;
  const label = (text, color, y) => popups.push({ text, color, x: FIELD_X + x + w / 2, y, life: 40 });
  if (placed.kind === "miss") {
    const y = (run.floors + 1) * height;
    pieces.push({ x, w, y, vy: 0 });
    label("MISS", COLORS.b, y + height + 4);
    shake = 10;
    return;
  }
  const topX = run.topX / u, topW = run.topW / u, y = run.floors * height;
  tower.push({ x: topX, w: topW, kind: placed.kind });
  if (placed.kind === "perfect") {
    label(`PERFECT +${config.floor_points + config.perfect_points}`, COLORS.g, y + height + 4);
    return;
  }
  if (x < topX) pieces.push({ x, w: topX - x, y, vy: 0 });
  else pieces.push({ x: topX + topW, w: x + w - topX - topW, y, vy: 0 });
  label(`+${config.floor_points}`, COLORS.k, y + height + 4);
}

function frame(now) {
  acc += Math.min(now - last, 250);
  last = now;
  while (acc >= TICK_MS && !run.over) {
    tick();
    acc -= TICK_MS;
  }
  animate();
  draw();
  if (run.over) finish();
  else requestAnimationFrame(frame);
}

async function finish() {
  const mine = result(run);
  const summary = `<div class="big">${mine.score}</div>
    ${mine.floors} floor${mine.floors === 1 ? "" : "s"}${mine.perfects ? ` (${mine.perfects} perfect)` : ""},
    ${mine.hits} hit${mine.hits === 1 ? "" : "s"}`;
  const title = mine.hits >= run.config.max_hits ? "Toppled" : "Time's up";
  state = FAIR ? "submitting" : "done";
  await finishRun(GAME, { title, summary, mine, inputs, course: run.course, config: run.config, fairRun });
  state = "done";
}

function press() {
  if (state === "running" && canDrop(run)) pending = true;
}

addEventListener("keydown", (e) => {
  if (e.repeat) return;
  if (state === "running" && (e.key === " " || e.key === "ArrowDown" || e.key === "s" || e.key === "S")) {
    press();
    e.preventDefault();
  } else if ((e.key === " " || e.key === "Enter") && state !== "running") {
    start();
    e.preventDefault();
  }
});

canvas.addEventListener("pointerdown", (e) => {
  press();
  e.preventDefault();
});

startButton.addEventListener("click", start);

fit(canvas, W, H);
preview();
if (FAIR) introFair(GAME);
