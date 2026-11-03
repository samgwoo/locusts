// The flow around a minigame that every game page shares: the overlay, practice runs checked against the
// server's replay, and scored job fair runs. A page adds "?fair" to play the pet's scored runs.
// `game` below is { id, name, controls }, where id is the server's game id and controls says how to play.

export const FAIR = new URLSearchParams(location.search).has("fair");
// Every API request carries this header; the server refuses unsafe ones without it (see app.py).
const HEADERS = { "Content-Type": "application/json", "X-Locusts": "1" };
// The signed-in player's pet, if any (scored runs need one).
const me = await fetch("/api/me").then((r) => (r.ok ? r.json() : null)).catch(() => null);
export const petId = me?.pet_id ?? null;
const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
export const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ESCAPES[c]);
export const BACK = `<a href="/">Back to campus</a>`;
// Practice runs with the OCaml Trauma perk's extra 10%.
const OCAML_SECONDS = 66;

const overlay = document.getElementById("overlay");
const overlayTitle = document.getElementById("overlay-title");
export const overlayBody = document.getElementById("overlay-body");
export const startButton = document.getElementById("start");
const ocaml = document.getElementById("ocaml");
const startNote = document.getElementById("start-note");

export function showOverlay(title, body, button) {
  overlayTitle.textContent = title;
  overlayBody.innerHTML = body;
  startButton.textContent = button ?? "";
  for (const el of [startButton, startNote]) el.hidden = !button;
  ocaml.parentElement.hidden = !button || FAIR;
  overlay.hidden = false;
}

export function hideOverlay() {
  overlay.hidden = true;
}

export async function post(path, body = {}) {
  const res = await fetch(path, { method: "POST", headers: HEADERS, body: JSON.stringify(body) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail ?? `The server said ${res.status}.`);
  return data;
}

// What a scored run will be like, or why the pet can't play one. Sends the player to the other game's
// page if this week's fair plays it.
export async function introFair(game) {
  document.getElementById("mode").textContent = "job fair";
  document.title = `${game.name} · job fair`;
  showOverlay(game.name, "Checking the fair…");
  if (!me) return showOverlay("Not signed in", `Sign in on campus first.<br>${BACK}`);
  if (!petId) return showOverlay("No pet", `Hatch a pet on campus first.<br>${BACK}`);
  const res = await fetch(`/api/pets/${petId}/fair`);
  const fair = await res.json();
  if (!res.ok || !fair.fair) return showOverlay("No fair", `${esc(fair.detail ?? "Your pet needs a career first.")}<br>${BACK}`);
  if (fair.game.id !== game.id) return location.replace(`${fair.game.page}?fair`);
  const { fair: f, runs, rules, applications } = fair;
  const left = runs.allowed - runs.used;
  const why = f.state !== "open" ? "The job fair isn't open right now."
    : !applications.used ? "Apply to a role before playing." : left <= 0 ? "You've used your scored runs at this fair." : null;
  if (why) return showOverlay(`Job fair #${f.id}`, `${why}${runs.best ? `<br>Best score: ${runs.best.score}` : ""}<br>${BACK}`);
  const boosts = rules.boosts.map((b) => `${esc(b.label)} +${Math.round(b.value * 100)}%`).join(", ");
  showOverlay(`Job fair #${f.id}`, `Scored run ${runs.used + 1} of ${runs.allowed}: ${rules.seconds} seconds and
    ${rules.max_hits} hits${boosts ? `, score ${boosts}` : ""}.<br>Starting locks your applications, and leaving
    mid-run uses up the run.<br>${game.controls}`, "Start scored run");
}

// A course to play: a scored run's in fair mode, otherwise a practice one. `fairRun` is the scored run's
// id and score multiplier.
export async function fetchRun(game) {
  if (FAIR) {
    const started = await post(`/api/pets/${petId}/fair/runs`, { game: game.id });
    return { course: started.course, config: started.config, fairRun: { id: started.run, multiplier: started.multiplier } };
  }
  const practice = await post(`/api/minigames/${game.id}/practice`, ocaml.checked ? { seconds: OCAML_SECONDS } : {});
  return { ...practice, fairRun: null };
}

export async function countdown() {
  for (const n of ["3", "2", "1"]) {
    showOverlay("", `<div class="big">${n}</div>`);
    await new Promise((r) => setTimeout(r, 600));
  }
  hideOverlay();
}

// Show how a run went: submitted to the fair in fair mode, otherwise checked against the server's replay.
// `mine` is the browser's result, which the server should match.
export async function finishRun(game, { title, summary, mine, inputs, course, config, fairRun }) {
  if (FAIR) return submitFairRun(title, summary, mine, inputs, fairRun);
  showOverlay(title, `${summary}<br><span class="note">Checking with the server…</span>`, "Run again");
  const res = await fetch(`/api/minigames/${game.id}/replay`, {
    method: "POST", headers: HEADERS,
    body: JSON.stringify({ seed: course.seed, seconds: course.ticks / config.ticks_per_second, inputs }),
  });
  let check;
  if (!res.ok) check = `<span class="bad">Server rejected the run (${res.status}).</span>`;
  else {
    const server = await res.json();
    check = server.score === mine.score && server.hits === mine.hits
      ? `<span class="ok">Server replay matches.</span>`
      : `<span class="bad">Server replay scored ${server.score} with ${server.hits} hits.</span>`;
  }
  overlayBody.innerHTML = `${summary}<br>${check}`;
}

async function submitFairRun(title, summary, mine, inputs, fairRun) {
  showOverlay(title, `${summary}<br><span class="note">Submitting to the fair…</span>`);
  let done;
  try {
    done = await post(`/api/pets/${petId}/fair/runs/${fairRun.id}`, { inputs });
  } catch (e) {
    return showOverlay(title, `${summary}<br><span class="bad">${esc(e.message)}</span><br>${BACK}`);
  }
  const scored = done.run, fair = done.fair;
  const check = scored.raw_score === mine.score ? "" : `<br><span class="bad">The server scored it ${scored.raw_score}.</span>`;
  const boosted = fairRun.multiplier !== 1 ? `<br>× ${fairRun.multiplier.toFixed(2)} = <b>${scored.score}</b>` : "";
  const left = fair.runs.allowed - fair.runs.used;
  const results = new Intl.DateTimeFormat("en-US", { weekday: "short", hour: "numeric", minute: "2-digit" })
    .format(new Date(fair.fair.results_at * 1000));
  showOverlay(title, `${summary}${boosted}${check}<br><span class="ok">Counted. Your best is ${fair.runs.best.score}.</span>
    <br>Results come out ${results}.<br>${BACK}`, left > 0 && fair.fair.state === "open" ? `Use your retry (${left} left)` : null);
}

// Scale the canvas by whole pixels to fill the window.
export function fit(canvas, w, h) {
  const resize = () => {
    const scale = Math.max(1, Math.floor(Math.min((innerWidth - 16) / w, (innerHeight - 90) / h)));
    canvas.style.width = `${w * scale}px`;
    canvas.style.height = `${h * scale}px`;
  };
  addEventListener("resize", resize);
  resize();
}
