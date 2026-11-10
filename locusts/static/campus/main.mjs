import { Locust, frameAt, loadSheet, showFrame, syncWorn } from "./locust.mjs";
import { Sky } from "./sky.mjs";
import { MapView } from "./view.mjs";

const $ = (id) => document.getElementById(id);
const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ESCAPES[c]);
// Polling re-renders every few seconds; rebuilding unchanged markup would eat clicks and reset hover and focus.
const setHTML = (el, html) => {
  if (el.lastHTML !== html) el.innerHTML = el.lastHTML = html;
};
const dollars = (v) => `${v < 0 ? "-" : ""}$${Math.abs(v).toFixed(2)}`;
// A stretch of seconds as "3d 4h" or "5h 12m".
const span = (s) => {
  const m = Math.max(0, Math.floor(s / 60)), d = Math.floor(m / 1440), h = Math.floor((m % 1440) / 60);
  return d ? `${d}d ${h}h` : `${h}h ${m % 60}m`;
};
const percent = (v, sign = false) => `${sign && v > 0 ? "+" : ""}${(v * 100).toFixed(1).replace(/\.0$/, "")}%`;
const ordinal = (n) => ["1st", "2nd", "3rd"][n - 1] ?? `${n}th`;
const capitalize = (s) => s.charAt(0).toUpperCase() + s.slice(1);
const WORKING = new Set(["training", "task"]);
// The path of a pet training at the gym.
const FITNESS = "fitness";
const POLL_MS = 5000;
const WEATHER_MS = 5 * 60 * 1000;

let config, sheet, view, locust, careerChoices, sky;
// Philadelphia right now, from /api/weather. Null until the first forecast arrives.
let weather = null;
let pet = null;
// The job fair from the pet's side, refetched on every render while its modal is open.
let fair = null;
// The signed-in account (/api/me: email, this semester's pet_id, and last semester's previous_pet_id) and its
// pet's id, or null when signed out.
let account = null;
let petId = null;
// The semester as /api/campus last reported it: number, starts_at, ends_at, next_starts_at, and over.
let term = null;
let selected = null;
// Set while the player's own action is in flight, so its effects aren't reported as surprises.
let expecting = false;
const tags = {};
// The career whose buildings the labels and Places list currently show.
let shownCareer;
// Refetches the pet when its walk ends, so the status and buttons switch over without waiting for a poll.
let arrivalTimer = null;
// Every pet on the server as /api/campus shows it, and the sprite and name tag of each one besides the
// player's, keyed by pet id. Their sprites sit in a layer under the player's own.
let campus = [];
const others = new Map();
// The HUD portrait's clothes, by item id.
const portraitWorn = new Map();
let othersLayer;
// Where other pets stand around a spot, so a crowd at one building doesn't stack into one locust. The
// player's own pet keeps the center.
const CROWD = [[-26, 6], [26, 6], [-14, 20], [14, 20], [-40, 18], [40, 18], [0, 30], [-28, 32], [28, 32]];

// Every API request carries X-Locusts: the server refuses unsafe ones without it (see app.py).
async function api(path, options = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json", "X-Locusts": "1" }, ...options });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    // Signed out somewhere else, or the session ran out: back to the sign-in screen.
    if (res.status === 401 && account && !path.startsWith("/api/auth/")) signedOut();
    throw Object.assign(new Error(body.detail || res.statusText), { status: res.status });
  }
  return body;
}

function toast(message, kind = "info") {
  const el = Object.assign(document.createElement("div"), { className: `panel toast ${kind}`, textContent: message });
  $("toasts").append(el);
  setTimeout(() => el.remove(), kind === "error" ? 5000 : 4000);
}

async function act(action, body, { follow = false } = {}) {
  expecting = true;
  try {
    const next = await api(`/api/pets/${petId}/${action}`, { method: "POST", body: body && JSON.stringify(body) });
    render(next, { follow });
    return true;
  } catch (e) {
    toast(e.message, "error");
    return false;
  } finally {
    expecting = false;
  }
}

// --- Names and places ---

function pathDef(path) {
  return pet.career_tree.paths[path];
}

// Paths of the pet's career that train at a building; empty for other careers' buildings.
function pathsAt(building) {
  const tree = pet?.career_tree;
  return tree ? Object.keys(tree.paths).filter((id) => tree.paths[id].buildings.includes(building)) : [];
}

function careerBuildings() {
  const tree = pet?.career_tree;
  return tree ? [...new Set(Object.values(tree.paths).flatMap((p) => p.buildings))] : [];
}

function placeName(id) {
  return config.buildings[id]?.name ?? id;
}

function itemDef(id) {
  return config.shop.items.find((i) => i.id === id);
}

// What the pet is off to do at its building, as [infinitive, gerund].
function workVerbs(p) {
  if (p.activity !== "training") return ["do a task", "doing a task"];
  if (p.path === FITNESS) return ["work out", "working out"];
  const name = pathDef(p.path).name;
  return [`train ${name}`, `training ${name}`];
}

// The college house a pet lives in, or the default one before there's a pet.
function homeOf(p) {
  return config.houses[p?.house] ?? config.houses[config.default_house];
}

function spotOf(building, house) {
  const b = building ? config.buildings[building] : config.houses[house] ?? homeOf(null);
  return [b.x, b.y];
}

function spotFor(p) {
  return spotOf(p.building, p.house);
}

function statusText(p) {
  if (p.trip) {
    if (!p.building) return `Walking home to ${homeOf(p).name}`;
    return `Walking to ${placeName(p.building)} to ${workVerbs(p)[0]}`;
  }
  switch (p.activity) {
    case "training": return `${capitalize(workVerbs(p)[1])} at ${placeName(p.building)}`;
    case "task": return `Doing a campus task at ${placeName(p.building)}`;
    case "sleeping": return `Asleep at ${homeOf(p).name}`;
    default: return `Hanging out at ${homeOf(p).name}`;
  }
}

// What anyone can tell about another pet from the map. Its career's paths aren't public.
function otherStatus(o) {
  const at = o.building ? placeName(o.building) : homeOf(o).name;
  if (o.trip) return o.building ? `Walking to ${at}` : `Walking home to ${at}`;
  if (o.activity === "training") return `${o.building === config.gym.building ? "Working out" : "Training"} at ${at}`;
  if (o.activity === "task") return `Doing a task at ${at}`;
  return o.activity === "sleeping" ? `Asleep at ${at}` : `Hanging out at ${at}`;
}

// Training at the gym is working out, not studying.
const trainingAnimation = (p) => (p.building === config.gym.building ? "workout" : "train");

function animationFor(p) {
  if (p.activity === "sleeping") return "sleep";
  if (p.activity === "training") return trainingAnimation(p);
  if (p.activity === "task") return "task";
  return p.penalized || p.fullness <= 0 || p.energy < config.min_energy_to_work ? "tired" : "idle";
}

// --- Rendering ---

function render(next, { follow = false } = {}) {
  const prev = pet;
  pet = next;
  locust.wear(pet.wearing);
  syncWorn(portraitWorn, pet.wearing.filter((id) => sheet.wear.names.includes(id)),
    () => Object.assign(document.createElement("div"), { className: "wear" }), $("portrait"));
  $("pet-card").hidden = false;
  $("actions").hidden = false;
  $("pet-name").textContent = pet.name;
  $("pet-status").textContent = statusText(pet);
  for (const [stat, top] of [["fullness", 100], ["energy", pet.max_energy]]) {
    $(stat).textContent = Math.round(pet[stat]);
    $(`${stat}-bar`).style.width = `${(pet[stat] / top) * 100}%`;
    $(`${stat}-bar`).parentElement.title = `${Math.round(pet[stat])} / ${top}`;
  }
  $("money").textContent = pet.invested > 0
    ? `${dollars(pet.money)} + ${dollars(pet.invested)} invested` : dollars(pet.money);
  $("feed-price").textContent = pet.swipes > 0 ? `${pet.swipes} swipe${pet.swipes === 1 ? "" : "s"}`
    : pet.perks.meal_price ? dollars(pet.perks.meal_price) : "free";
  const job = pet.internship;
  $("job-row").hidden = !job;
  if (job) $("job").textContent = `${job.title} at ${job.company} · ${dollars(job.pay_per_week)}/wk`;
  $("upgrades-button").disabled = $("fair-button").disabled = !pet.career_tree || pet.frozen;
  $("shop-button").disabled = pet.frozen;
  $("portfolio-button").hidden = !pet.perks.market && pet.invested <= 0;
  document.body.classList.toggle("frozen", pet.frozen);
  showHome();
  showCareer();
  renderBadges();
  renderActions();
  renderEvent(prev);
  renderTags();
  renderBuilding();
  if (!$("upgrades").hidden) renderUpgrades();
  if (!$("shop").hidden) renderShop();
  if (!$("portfolio").hidden) renderPortfolio();
  if (!$("fair").hidden) loadFair();
  if (prev && !expecting) reportSurprises(prev, pet);
  movePet({ follow });
  if (!pet.career && !pet.frozen) openHatch("declare");
  // The semester just ended (or ended while the player was away): the final ranking, once.
  if (pet.frozen && !localStorage.getItem(resultsKey(pet.semester))) openResults(pet.semester);
}

// --- Events while working ---

// How an effect reads on a chip: its icon and its text.
const EVENT_EFFECTS = {
  xp: ["train", (v) => `${v > 0 ? "+" : ""}${Math.round(v)} XP`],
  money: ["coin", (v) => `${v > 0 ? "+" : "-"}$${Math.abs(v)}`],
  energy: ["energy", (v) => `${v > 0 ? "+" : ""}${Math.round(v)} energy`],
  fullness: ["food", (v) => `${v > 0 ? "+" : ""}${Math.round(v)} food`],
  gpa: ["star", (v) => `${v > 0 ? "+" : ""}${v.toFixed(2)} GPA`],
};

const effectChips = (effects) => Object.entries(effects).map(([key, v]) =>
  `<span class="chip ${v > 0 ? "met" : "unmet"}"><i data-icon="${EVENT_EFFECTS[key][0]}"></i>${esc(EVENT_EFFECTS[key][1](v))}</span>`).join("");

// The waiting event, if any: what's going on, and a button per option. A new one is announced.
function renderEvent(prev) {
  const ev = pet.event;
  $("event-card").hidden = !ev;
  if (!ev) return;
  const left = Math.max(1, Math.round((ev.expires_at - Date.now() / 1000) / 60));
  const where = ev.building ? config.buildings[ev.building]?.name : null;
  setHTML($("event-card"), `<header><i data-icon="${ev.choice ? "fair" : "star"}"></i><h2>${esc(ev.title)}</h2>
      <span class="muted">${left} min</span></header>
    <p>${esc(ev.text)}${where ? ` <span class="muted">· ${esc(where)}</span>` : ""}</p>
    <div class="options">${ev.options.map((o, i) => `<button data-event-option="${i}">
      <span>${esc(o.label)}</span><span class="chips">${effectChips(o.effects)}</span></button>`).join("")}</div>
    <small class="muted">${ev.choice ? `Ignore it and ${esc(pet.name)} takes neither.` : "Or let it go: nothing lost."}</small>`);
  const before = prev?.event;
  if (prev && (!before || before.expires_at !== ev.expires_at)) toast(`${ev.title}: something came up for ${pet.name}.`);
}

async function answerEvent(option) {
  expecting = true;
  try {
    const next = await api(`/api/pets/${petId}/event`, { method: "POST", body: JSON.stringify({ option }) });
    render(next);
    const landed = Object.entries(next.landed).map(([k, v]) => EVENT_EFFECTS[k][1](v)).join(", ");
    toast(landed ? `${next.name}: ${landed}.` : "Done.");
  } catch (e) {
    toast(e.message, "error");
  } finally {
    expecting = false;
  }
}

function showHome() {
  if (tags.home?.house === pet.house) return;
  tags.home?.remove();
  const home = homeOf(pet);
  addTag("home", `${home.name} (home)`, [home.x, home.y - 30], "home");
  tags.home.house = pet.house;
  renderPlaces();
}

// Labels and the Places list only show the pet's own career buildings, plus home.
function showCareer() {
  if (shownCareer === pet.career) return;
  shownCareer = pet.career;
  for (const [id, tag] of Object.entries(tags)) {
    if (id === "home" || id === config.gym.building) continue;
    tag.remove();
    delete tags[id];
  }
  for (const id of careerBuildings()) {
    const b = config.buildings[id];
    const top = Math.min(...b.shape[0].map(([, y]) => y));
    addTag(id, b.name, [b.x, top]);
  }
  renderPlaces();
}

function renderBadges() {
  const badges = [];
  if (pet.frozen) badges.push(`<span class="badge bad" title="It stays as it was at the end">Semester over</span>`);
  if (pet.career_tree) badges.push(`<span class="badge good">${esc(pet.career_tree.name)}</span>`);
  badges.push(`<span class="badge" title="From last night's sleep">Rest ${Math.round(pet.rest_multiplier * 100)}%</span>`);
  badges.push(`<span class="badge" title="Events while working move it; some roles need it high">GPA ${pet.gpa.toFixed(2)}</span>`);
  if (WORKING.has(pet.activity) && !pet.trip) badges.push(`<span class="badge">Speed ${pet.work_speed.toFixed(2)}x</span>`);
  if (pet.penalized) badges.push(`<span class="badge bad" title="Burns energy twice as fast and works at half speed today">Exhausted</span>`);
  if (pet.coffee_active) badges.push(`<span class="badge good" title="Works ${percent(config.coffee_work_boost)} faster, and the exhaustion penalty is halved">Caffeinated</span>`);
  if (pet.swag > 0) badges.push(`<span class="badge" title="From the things your pet owns">Swag ${pet.swag}</span>`);
  if (pet.money < 0) badges.push(`<span class="badge bad" title="DoorDash put it below zero. Work it off">In debt</span>`);
  const running = runningExperiments();
  if (running.length) {
    const left = Math.ceil((Math.min(...running) - pet.now) / 3600);
    const bonus = percent(pet.perks.experiments.bonus * running.length, true);
    badges.push(`<span class="badge good" title="Training earns ${bonus} XP while experiments run">`
      + `${running.length === 1 ? "Experiment" : `${running.length} experiments`} · ${left}h</span>`);
  }
  const passive = pet.income_per_hour + (pet.activity === "sleeping" ? pet.perks.sleep_pay_per_hour
    : pet.activity === "home" && !pet.trip ? pet.perks.idle_pay_per_hour : 0);
  if (passive > 0) badges.push(`<span class="badge good">Earning ${dollars(passive)}/hr</span>`);
  setHTML($("badges"), badges.join(""));
}

function runningExperiments() {
  return pet.experiments.filter((end) => end > pet.now);
}

function renderActions() {
  const working = WORKING.has(pet.activity);
  const rules = {
    feed: pet.fullness >= 100 ? "Already full"
      : pet.swipes <= 0 && pet.money < pet.perks.meal_price ? "Not enough money" : null,
    coffee: pet.money < config.coffee_price ? "Not enough money" : null,
    sleep: pet.activity === "sleeping" ? "Already asleep" : working ? "Pull your pet out first"
      : pet.trip ? "Still walking home" : pet.energy >= pet.max_energy ? "Not tired" : null,
    wake: pet.activity !== "sleeping" ? "Not asleep" : pet.is_night ? "Pets sleep at night" : null,
    recall: working ? null : "Not at a building",
  };
  for (const button of document.querySelectorAll("#actions button")) {
    const why = pet.frozen ? "The semester is over" : rules[button.dataset.action];
    button.disabled = why !== null;
    button.title = why ?? "";
  }
}

function renderTags() {
  for (const [id, tag] of Object.entries(tags)) tag.classList.toggle("here", pet?.building === id);
  placeTags();
}

function placeTags() {
  for (const [id, tag] of Object.entries(tags)) {
    const [x, y] = tag.spot;
    const [sx, sy] = view.toScreen(x, y);
    tag.hidden = sx < -200 || sy < -50 || sx > innerWidth + 200 || sy > innerHeight + 50;
    tag.style.left = `${Math.round(sx)}px`;
    tag.style.top = `${Math.round(sy) - 10}px`;
  }
}

// While the pet is flying, sit it above the center of the window so the roofs underneath stay in view.
// The shift is capped, or a high cruise on a short window would push the pet off the top.
function flightCamera() {
  const above = Math.min(locust.lift * view.z, innerHeight * 0.28);
  return [locust.x, locust.y - locust.lift + above / view.z];
}

// Flies the locust along the pet's trip, picking it up partway after a reload, or stands it at its spot.
function movePet({ follow }) {
  locust.setAnimation(animationFor(pet));
  clearTimeout(arrivalTimer);
  const trip = pet.trip;
  if (trip) {
    arrivalTimer = setTimeout(refresh, (trip.arrive - pet.now) * 1000 + 250);
    const landed = !locust.hop && locust.x === trip.to[0] && locust.y === trip.to[1];
    if (locust.hop?.trip.start === trip.start || landed) return;
    const first = !locust.placed;
    locust.walk(trip, (pet.now - trip.start) * 1000);
    if (first) view.centerOn(locust.x, locust.y, { instant: true });
    if (first || follow) view.follow = flightCamera;
    return;
  }
  const [x, y] = spotFor(pet);
  if (!locust.placed) {
    locust.place(x, y);
    view.centerOn(x, y, { instant: true });
    return;
  }
  const dest = locust.hop ? locust.hop.to : [locust.x, locust.y];
  if (dest[0] !== x || dest[1] !== y) locust.place(x, y);
}

function reportSurprises(prev, now) {
  if (WORKING.has(prev.activity) && !WORKING.has(now.activity)) {
    toast(now.perks.burnout_immunity
      ? `${now.name} got too hungry to keep working and went home.`
      : `${now.name} ran out of energy and went home. Tomorrow will be rough.`, "error");
  }
  if (now.perks.market && prev.invested > 0 && Math.abs(now.invested - prev.invested) > 0.005) {
    toast(`The market moved ${percent(now.perks.market.last_move, true)} this morning.`);
  }
  const was = prev.internship, job = now.internship;
  if (job && (!was || was.until !== job.until)) {
    toast(job.kept && was ? `Fair results: ${now.name} kept its internship at ${job.company}!`
      : `Fair results: ${now.name} got ${job.title} at ${job.company}!`);
  } else if (was && !job) {
    toast(`${now.name}'s internship at ${was.company} ended.`, was.lost_on_exhaustion ? "error" : "info");
  }
  if (now.gpa > prev.gpa + 0.0001) toast(`${now.name}'s office hours paid off: GPA ${now.gpa.toFixed(2)}.`);
  const ordered = now.doordashes - prev.doordashes;
  if (ordered > 0) {
    toast(`${now.name} got hungry and ordered DoorDash${ordered > 1 ? ` ${ordered} times` : ""}: `
      + `-${dollars(ordered * config.doordash.price)}${now.money < 0 ? ". It's in debt!" : "."}`, "error");
  }
  for (const [id, sub] of Object.entries(prev.subscriptions)) {
    if (now.subscriptions[id]) continue;
    const name = itemDef(id)?.name ?? id;
    if (sub.renew) toast(`${now.name} couldn't pay for its ${name}, so it lapsed.`, "error");
    else toast(`${now.name}'s ${name} ended.`);
  }
  if (now.fitness.level > prev.fitness.level) toast(`${now.name} got fitter! Max energy is now ${now.max_energy}.`);
  if (!prev.is_night && now.is_night) toast("Night has fallen on campus.");
}

function renderBuilding() {
  const card = $("building-card");
  view.highlight("selected", selected);
  if (!selected || !pet) {
    card.hidden = true;
    return;
  }
  const b = config.buildings[selected];
  const working = WORKING.has(pet.activity);
  const here = pet.building === selected;
  const tooTired = pet.energy < config.min_energy_to_work;
  const busy = pet.frozen ? "The semester is over" : working ? "Pull your pet out first"
    : tooTired ? "Too tired to work" : null;
  const parts = [`<header><h2>${esc(b.name)}</h2>
    <button class="x" data-close title="Close"><i data-icon="close"></i></button></header>`];
  if (here) {
    const [to, ing] = workVerbs(pet);
    const doing = pet.trip ? `on the way to ${to}` : ing;
    parts.push(`<div class="here">${esc(pet.name)} is ${esc(doing)} here.
      <button class="quiet" data-recall ${pet.frozen ? "disabled" : ""}><i data-icon="home"></i>${pet.trip ? "Turn back" : "Pull out"}</button></div>`);
  }
  const visitors = campus.filter((o) => o.id !== pet.id && o.building === selected && !o.trip);
  if (visitors.length) {
    parts.push(`<div class="others"><small>Also here:</small> ${visitors.map((o) => `<span class="chip"
      title="${esc(otherStatus(o))}">${esc(o.name)}${o.career ? ` · ${esc(o.career)}` : ""}</span>`).join("")}</div>`);
  }
  const paths = pathsAt(selected);
  const lab = pet.perks.experiments;
  const xpRate = config.xp_per_hour * (1 + (lab ? lab.bonus * runningExperiments().length : 0));
  if (paths.length) parts.push(`<h3>Train</h3>`);
  if (lab && lab.buildings.includes(selected)) {
    const free = lab.slots - runningExperiments().length;
    parts.push(`<div>${free > 0
      ? `Going to work here starts ${free === 1 ? "an experiment" : `${free} experiments`}: `
        + `${percent(lab.bonus, true)} training XP each for ${lab.hours} hours, wherever you train.`
      : "Your experiments are already running."}</div>`);
  }
  for (const path of paths) {
    const def = pathDef(path);
    const s = pet.upgrades[path];
    const pips = "■".repeat(s.tier) + "□".repeat(def.tiers.length - s.tier);
    const now = here && !pet.trip && pet.activity === "training" && pet.path === path;
    const why = s.blocked ?? busy;
    parts.push(`<div class="path-row ${s.blocked ? "locked" : ""}">
      <div class="head"><b>${esc(def.name)}</b><span class="pips">${pips}</span></div>
      <small>${esc(def.focus)}</small>
      <div>${s.next ? `${Math.floor(s.xp)} / ${s.next.cost} XP toward <i>${esc(s.next.name)}</i>` : "Maxed out"}</div>
      ${now ? `<span class="badge good">Training now</span>`
        : `<button data-train="${path}" ${why ? "disabled" : ""} title="${esc(why ?? "")}">
             <i data-icon="train"></i>Train · ${+xpRate.toFixed(1)} XP/hr</button>`}
      ${s.blocked ? `<div class="why">${esc(s.blocked)}</div>` : ""}
    </div>`);
  }
  if (selected === config.gym.building) parts.push(gymSection(here, busy));
  const taskHere = here && !pet.trip && pet.activity === "task";
  const site = pet.perks.task_sites[selected];
  const taskText = site
    ? `<b>${esc(site.name)}</b>: earns ${dollars(site.pay_per_hour)}/hr${site.xp_per_hour
      ? ` and ${+site.xp_per_hour.toFixed(1)} XP/hr toward ${esc(pathDef(site.path).name)}` : ""}.`
    : `Earns ${dollars(pet.perks.task_pay_per_hour)}/hr, enough to eat. No XP.`;
  parts.push(`<h3>${site ? "Task" : "Campus task"}</h3>
    <div>${taskText}</div>
    ${taskHere ? `<span class="badge good">Working now</span>`
      : `<button data-task ${busy ? "disabled" : ""} title="${esc(busy ?? "")}"><i data-icon="task"></i>Do a task</button>`}
    ${busy && !here ? `<div class="why">${busy}</div>` : ""}`);
  setHTML(card, parts.join(""));
  card.hidden = false;
}

function gymSection(here, busy) {
  const gym = config.gym;
  const { level, hours } = pet.fitness;
  const maxed = level >= gym.max_levels;
  const now = here && !pet.trip && pet.activity === "training" && pet.path === FITNESS;
  const toward = Math.min(hours - level * gym.hours_per_level, gym.hours_per_level);
  return `<h3>Work out</h3>
    <div class="path-row">
      <div class="head"><b>Fitness</b><span class="pips">${"■".repeat(level)}${"□".repeat(gym.max_levels - level)}</span></div>
      <small>Any career can train here. Every ${gym.hours_per_level} hours adds ${gym.energy_per_level} max energy for good,
        and sleep refills the extra too.</small>
      <div>${maxed ? `Maxed out at ${pet.max_energy} max energy`
        : `${toward.toFixed(1)} / ${gym.hours_per_level} hours toward ${pet.max_energy + gym.energy_per_level} max energy`}</div>
      ${now ? `<span class="badge good">Working out now</span>` : maxed ? ""
        : `<button data-workout ${busy ? "disabled" : ""} title="${esc(busy ?? "")}"><i data-icon="energy"></i>Work out</button>`}
    </div>`;
}

// Icon and label for each item effect, shown as chips on shop tiles.
const EFFECT_CHIPS = {
  training_xp: ["train", (v) => `+${percent(v)} XP`],
  task_pay: ["coin", (v) => `+${percent(v)} pay`],
  work_energy_saving: ["energy", (v) => `-${percent(v)} drain`],
  sleep_regen: ["sleep", (v) => `+${percent(v)} sleep`],
  rest_bonus: ["moon", (v) => `+${percent(v)} rest`],
  travel_speed: ["fast", (v) => `+${percent(v)} speed`],
  home_meals: ["food", () => "eats at home"],
  meal_discount: ["food", (v) => `-${percent(v)} meals`],
};

const chip = (icon, label) => `<span class="chip"><i data-icon="${icon}"></i>${esc(label)}</span>`;

function itemChips(item) {
  const chips = Object.entries(item.effects).filter(([key]) => EFFECT_CHIPS[key])
    .map(([key, value]) => chip(EFFECT_CHIPS[key][0], EFFECT_CHIPS[key][1](value)));
  if (item.swipes) chips.push(chip("food", `${item.swipes} meals/wk`));
  if (item.swag) chips.push(chip("swag", `+${item.swag}`));
  if (item.wear) chips.push(chip("star", "Wearable"));
  return chips.join("");
}

function renderShop() {
  const { categories, items } = config.shop;
  const parts = [`<p class="shop-cash"><i data-icon="coin"></i><b>${dollars(pet.money)}</b>
    ${pet.swag ? `<i data-icon="swag"></i><b>${pet.swag}</b> swag` : ""}
    <span class="muted">Hover an item for details. Subscriptions bill weekly and lapse if your pet can't pay.</span></p>`];
  for (const [category, title] of Object.entries(categories)) {
    const rows = items.filter((i) => i.category === category).map(shopItem);
    if (rows.length) parts.push(`<h3>${esc(title)}</h3><div class="shop-items">${rows.join("")}</div>`);
  }
  setHTML($("shop-body"), parts.join(""));
}

function shopItem(item) {
  const sub = pet.subscriptions[item.id];
  const owned = pet.items.includes(item.id);
  const upgrade = config.shop.items.find((i) => i.replaces === item.id);
  const price = `${dollars(item.price)}${item.weekly ? "/wk" : ""}`;
  const tradedIn = upgrade && pet.items.includes(upgrade.id);
  let action, ribbon = "";
  if (owned) {
    ribbon = pet.wearing.includes(item.id) ? "Wearing" : "Owned";
  } else if (tradedIn) {
    action = `<small class="muted">Traded in for the ${esc(upgrade.name)}</small>`;
  } else if (sub) {
    ribbon = item.swipes ? `${pet.swipes} left` : "Active";
    action = sub.renew
      ? `<small>Renews ${campusWhen(sub.until)}</small><button class="quiet" data-cancel="${item.id}">Cancel</button>`
      : `<small class="why">Ends ${campusWhen(sub.until)}</small><button data-resume="${item.id}">Keep it</button>`;
  } else {
    const why = item.replaces && !pet.items.includes(item.replaces) ? `Trade in a ${itemDef(item.replaces).name}`
      : pet.money < item.price ? "Not enough money" : null;
    action = `<button data-item="${item.id}" ${why ? "disabled" : ""} title="${esc(why ?? "")}">
      <i data-icon="coin"></i>${price}</button>${why ? `<small class="why">${esc(why)}</small>` : ""}`;
  }
  return `<div class="item ${owned || sub ? "mine" : ""} ${tradedIn ? "retired" : ""}" title="${esc(item.blurb)}">
    <div class="art"><i data-item-art="${item.id}"></i>${ribbon ? `<span class="ribbon">${ribbon}</span>` : ""}</div>
    <b>${esc(item.name)}</b>
    <div class="chips">${itemChips(item)}</div>
    ${action ? `<div class="buttons">${action}</div>` : ""}
  </div>`;
}

// The tier each path's card spotlights, when the player has clicked one; otherwise its next tier.
const tierShown = {};

function renderUpgrades() {
  const tree = pet.career_tree;
  if (!tree) return;
  $("upgrades-title").textContent = `${tree.name} upgrades`;
  setHTML($("upgrade-grid"), Object.entries(tree.paths).map(([id, def]) => upgradeCard(id, def, pet.upgrades[id])).join(""));
}

// One path: its XP toward the next tier, a row of tier squares, and one tier's perks at a time.
function upgradeCard(pathId, def, s) {
  const n = def.tiers.length, maxed = s.tier >= n;
  const shown = Math.min(tierShown[pathId] ?? s.tier, n - 1);
  const nodes = def.tiers.map((t, i) => {
    const state = i < s.tier ? "owned" : i === s.tier ? "next" : "";
    const tip = [`${i + 1}. ${t.name} · ${t.cost} XP`, ...t.perks, ...t.later.map((l) => `Not built yet: ${l}`)].join("\n");
    return `<button class="node ${state} ${i === shown ? "shown" : ""} ${t.perks.length ? "" : "soon"}"
      data-tier="${pathId}:${i}" title="${esc(tip)}">${i < s.tier ? `<i data-icon="check"></i>` : i + 1}</button>`;
  }).join(`<span class="link"></span>`);
  const t = def.tiers[shown];
  const state = shown < s.tier ? `<span class="badge good">Owned</span>` : shown === s.tier ? `<span class="badge">Next</span>` : "";
  const goal = s.next?.cost;
  const meter = maxed ? `<div class="xp"><b>${Math.floor(s.xp)}</b> XP <span class="badge good">Maxed</span></div>`
    : `<div class="xp"><b>${Math.floor(s.xp)}</b><span class="muted"> / ${goal} XP</span></div>
       <div class="bar leaf"><div style="width: ${Math.min(100, (s.xp / goal) * 100)}%"></div></div>`;
  const locked = s.blocked && !maxed;
  const action = maxed ? "" : locked
    ? `<span class="badge bad" title="${esc(capitalize(s.blocked))}"><i data-icon="lock"></i>Locked</span>`
    : `<button data-buy="${pathId}" ${s.can_buy ? "" : "disabled"}
         title="${s.can_buy ? "" : `Needs ${goal - Math.floor(s.xp)} more XP`}"><i data-icon="upgrade"></i>Buy tier ${s.tier + 1}</button>`;
  return `<div class="upgrade ${locked ? "locked" : ""}">
    <h3>${esc(def.name)}</h3>
    <div class="chips">${chip("star", def.focus)}${def.buildings.map((b) => chip("train", placeName(b))).join("")}</div>
    ${meter}
    <div class="track">${nodes}</div>
    <div class="spotlight">
      <div class="head"><b>${shown + 1}. ${esc(t.name)}</b>${state}<span class="muted">${t.cost} XP</span></div>
      ${t.perks.map((p) => `<p>${esc(p)}</p>`).join("")}
      ${t.later.map((l) => `<p class="soon" title="Needs a system that isn't built yet">Coming later: ${esc(l)}</p>`).join("")}
    </div>
    <div class="buy">${action}</div>
  </div>`;
}

function renderPortfolio() {
  const m = pet.perks.market;
  const rules = m ? [
    `The market moves every morning: about ${percent(m.drift, true)} a day, swinging up to ${percent(m.swing)}`
      + ` either way${m.loss_cap != null ? `, and you never lose more than ${percent(m.loss_cap)} in a day` : ""}.`,
    `You can have up to ${percent(m.max_share)} of your money invested, so ${dollars(m.max_invest)} more right now.`,
    `This morning's move: <b>${percent(m.last_move, true)}</b>.`,
  ] : ["Your pet can withdraw what it has invested."];
  setHTML($("portfolio-body"), `<p>Cash <b>${dollars(pet.money)}</b> · Invested <b>${dollars(pet.invested)}</b></p>`
    + rules.map((r) => `<p>${r}</p>`).join(""));
  $("portfolio-form").querySelector('[data-move="invest"]').disabled = !m || m.max_invest < 0.01;
  $("portfolio-form").querySelector('[data-move="withdraw"]').disabled = pet.invested < 0.01;
}

function campusWhen(ts) {
  return new Intl.DateTimeFormat("en-US", { timeZone: config.clock.timezone, weekday: "short", hour: "numeric",
    minute: "2-digit" }).format(new Date(ts * 1000));
}

async function loadFair() {
  try {
    fair = await api(`/api/pets/${petId}/fair`);
  } catch (e) {
    return toast(e.message, "error");
  }
  renderFair();
}

async function fairAct(action, body) {
  try {
    fair = await api(`/api/pets/${petId}/fair/${action}`, { method: "POST", body: JSON.stringify(body) });
  } catch (e) {
    return toast(e.message, "error");
  }
  renderFair();
}

function renderFair() {
  if (!fair) return setHTML($("fair-body"), "<p>Checking the fair…</p>");
  if (!fair.fair) return setHTML($("fair-body"), `<p>${esc(pet.name)} needs a career before it can apply.</p>`);
  const { fair: f, applications: apps, runs, rules, game } = fair;
  const open = f.state === "open";
  $("fair-title").textContent = `Job fair #${f.id}`;
  const [tone, status] = open ? ["good", `Open until ${campusWhen(f.closes_at)}`]
    : f.state === "upcoming" ? ["", `Opens ${campusWhen(f.opens_at)}`] : ["bad", `Closed · results ${campusWhen(f.results_at)}`];
  // How a fair works, as four steps; the fine print is in the tooltips.
  const steps = [
    ["fair", `Apply to ${apps.limit}`, "Best first", "Starting a scored run locks your applications."],
    ["star", `Play ${game.name}`, `${rules.seconds}s · ${rules.max_hits} hits`,
      "Fairs take turns between minigames each week. Career tiers add hits."],
    ["people", "Top score wins", "Among those who want it most", "Each role goes to the best score among the pets that ranked it highest."],
    ["coin", "Get paid", "Hourly, until the next fair", "Winning pays every hour until the next fair's results."],
  ];
  const facts = [
    chip("fair", `${apps.used}/${apps.limit} applied`),
    chip("star", `${runs.used}/${runs.allowed} scored runs`),
    ...(runs.best ? [chip("upgrade", `Best ${runs.best.score} pts`)] : []),
    ...rules.boosts.map((b) => chip("fast", `${b.label} ${percent(b.value, true)}`)),
    ...(apps.referral ? [`<span class="chip" title="Covers one role you're a tier short of"><i data-icon="check"></i>Referral</span>`] : []),
    ...(apps.locked && apps.late_changes_left ? [chip("plus", `${apps.late_changes_left} swap left`)] : []),
  ];
  const parts = [
    `<div class="fair-top"><span class="badge ${tone}"><i data-icon="clock"></i>${status}</span>
      <span class="badge"><i data-icon="star"></i>${esc(game.name)}</span></div>`,
    `<ol class="steps">${steps.map(([icon, head, sub, tip], i) => `<li title="${esc(tip)}">
      <i data-icon="${icon}" class="big"></i><b>${i + 1}. ${esc(head)}</b><small>${esc(sub)}</small></li>`).join("")}</ol>`,
    `<div class="chips facts">${facts.join("")}</div>`,
  ];
  const mine = fair.roles.filter((r) => r.choice).sort((a, b) => a.choice - b.choice);
  parts.push(`<div class="roles">${fair.roles.map((r) => roleCard(r, apps, open, mine)).join("")}</div>`);
  const left = runs.allowed - runs.used;
  const why = !open ? "The fair isn't open" : !apps.used ? "Apply to a role first" : left <= 0 ? "No scored runs left" : null;
  parts.push(`<div class="fair-runs">
    <button data-play ${why ? "disabled" : ""} title="${esc(why ?? "")}"><i data-icon="fair"></i>Play scored run</button>
    <span class="pips">${"■".repeat(runs.used)}${"□".repeat(Math.max(0, left))}</span>
    ${why && open ? `<small class="why">${esc(why)}</small>` : ""}
    <a class="button quiet" href="${game.page}">Practice</a></div>`);
  const moves = movesSection(fair.moves, open);
  if (moves) parts.push(moves);
  if (fair.last) parts.push(lastResults(fair.last));
  setHTML($("fair-body"), parts.join(""));
}

// Perks that act on another pet at this fair: backing one (Amicus Brief) and shorting one (Short Seller).
function movesSection(moves, open) {
  if (!moves.amicus && !moves.short) return "";
  const others = campus.filter((o) => o.id !== pet.id).sort((a, b) => a.name.localeCompare(b.name));
  const picker = (id) => `<select id="${id}" aria-label="Pet">${others.map((o) =>
    `<option value="${o.id}">${esc(o.name)}${o.career ? ` · ${esc(o.career)}` : ""}</option>`).join("")}</select>`;
  const why = !open ? "The fair isn't open" : !others.length ? "No other pets this semester" : null;
  const rows = [];
  const a = moves.amicus;
  if (a) {
    rows.push(`<div class="move"><i data-icon="people"></i><div><b>Amicus Brief</b>
      <small>Back a pet: ${percent(a.mine, true)} to your runs, ${percent(a.theirs, true)} to theirs.</small></div>${
      a.backing ? `<span class="badge good">Backing ${esc(a.backing)}</span>`
        : a.too_late ? `<small class="why">Only before your first scored run</small>`
          : `${picker("back-pet")}<button data-back ${why ? "disabled" : ""} title="${esc(why ?? "")}">Back</button>`}</div>`);
  }
  const s = moves.short;
  if (s) {
    rows.push(`<div class="move"><i data-icon="coin"></i><div><b>Short Seller</b>
      <small>Bet up to ${dollars(s.max)} that a pet wins no role. Double back if it applies and loses.</small></div>${
      s.on ? `<span class="badge">Shorting ${esc(s.on)} · ${dollars(s.stake)}</span>`
        : `${picker("short-pet")}<input id="short-amount" type="number" min="0.01" step="0.01" max="${s.max}"
            placeholder="Amount" aria-label="Amount"><button data-short ${why || s.max <= 0 ? "disabled" : ""}
            title="${esc(why ?? (s.max <= 0 ? "No money to bet" : ""))}">Short</button>`}</div>`);
  }
  return `<h3>Moves</h3><div class="moves">${rows.join("")}</div>`;
}

function roleCard(r, apps, open, mine) {
  const short = r.requires.filter((q) => q.have < q.tier);
  const needs = r.requires.map((q) => `<span class="chip ${q.have < q.tier ? "unmet" : "met"}"
    title="${esc(`Needs ${q.path} tier ${q.tier}; you have ${q.have}`)}"><i data-icon="${q.have < q.tier ? "lock" : "check"}"></i>${
    esc(q.path)} ${q.tier}</span>`);
  const lowGpa = r.gpa && r.gpa.have < r.gpa.needs;
  if (r.gpa) {
    needs.push(`<span class="chip ${lowGpa ? "unmet" : "met"}" title="${esc(`Needs a ${r.gpa.needs.toFixed(1)} GPA; you have ${r.gpa.have.toFixed(2)}`)}">
      <i data-icon="${lowGpa ? "lock" : "check"}"></i>GPA ${r.gpa.needs.toFixed(1)}</span>`);
  }
  if (!needs.length) needs.push(chip("check", "Open to all"));
  const tags = [];
  if (r.choice) tags.push(`<span class="badge good">${ordinal(r.choice)} choice</span>`);
  if (r.exclusive) tags.push(`<span class="badge good">Exclusive</span>`);
  if (r.top) tags.push(`<span class="badge" title="Only a top-${r.top} score can win it">Top ${r.top}</span>`);
  if (r.outright) tags.push(`<span class="badge good" title="No one else wants it">Yours if you apply</span>`);
  if (r.referral && !r.choice) {
    tags.push(fair.committee_letter ? `<span class="badge" title="Your GPA covers the missing tier">Committee letter</span>`
      : `<span class="badge" title="Your referral covers the missing tier">Referral</span>`);
  }
  // Who else wants it, as a count; the names and their tiers are in the tooltip.
  let crowd = "";
  if (r.rivals) {
    const tiers = (t) => Object.entries(t).map(([path, n]) => `${path} ${n}`).join(", ") || "no tiers";
    crowd = `<span class="chip" title="${esc(r.rivals.map((x) => `${x.name} (${tiers(x.tiers)})`).join("\n") || "No rivals yet")}">
      <i data-icon="people"></i>${r.rivals.length} rival${r.rivals.length === 1 ? "" : "s"}</span>`;
  } else if (r.names) {
    crowd = `<span class="chip" title="${esc(r.names.join(", ") || "No one has applied yet")}"><i data-icon="people"></i>${r.names.length} applied</span>`;
  } else if (r.applicants !== null) {
    crowd = chip("people", `${r.applicants} applied`);
  }
  const buttons = [];
  if (r.choice) {
    if (open && !apps.locked) buttons.push(`<button class="quiet" data-withdraw="${r.id}">Withdraw</button>`);
  } else if (open && apps.locked && apps.late_changes_left > 0) {
    for (const old of mine) buttons.push(`<button data-apply="${r.id}" data-instead="${old.id}">Swap for ${esc(old.company)}</button>`);
  } else if (open) {
    buttons.push(`<button data-apply="${r.id}" ${r.problem ? "disabled" : ""} title="${esc(capitalize(r.problem ?? ""))}">Apply</button>`);
  }
  const blocked = open && !r.choice && r.problem && !(apps.locked && apps.late_changes_left > 0);
  return `<div class="role ${r.choice ? "mine" : ""} ${blocked ? "closed" : ""}">
    <div class="head"><b>${esc(r.company)}</b>${tags.join("")}</div>
    <div class="title">${esc(r.title)}</div>
    <div class="pay"><i data-icon="coin"></i><b>$${Math.round(r.pay_per_week)}</b><span>/wk</span></div>
    <div class="chips">${needs.join("")}${crowd}</div>
    ${buttons.length ? `<div class="buttons">${buttons.join("")}</div>` : ""}
    ${blocked && !short.length && !lowGpa ? `<div class="why">${esc(capitalize(r.problem))}</div>` : ""}
  </div>`;
}

function lastResults(last) {
  const you = last.won ? `${esc(pet.name)} got <b>${esc(last.won.title)}</b> at ${esc(last.won.company)}.`
    : last.applied ? `${esc(pet.name)} didn't get a role.` : `${esc(pet.name)} didn't apply.`;
  const rows = last.placements.map((p) => `<li>${esc(p.company)}, ${esc(p.title)}: ${
    p.winner ? esc(p.winner) : `<span class="muted">no one</span>`}</li>`).join("");
  const s = last.short;
  const bet = !s ? "" : s.payout > s.stake ? ` Your short on ${esc(s.on)} paid ${dollars(s.payout)}.`
    : s.payout > 0 ? ` ${esc(s.on)} didn't apply, so your ${dollars(s.stake)} bet came back.`
      : ` Your ${dollars(s.stake)} short on ${esc(s.on)} lost.`;
  return `<h3>Last fair (#${last.id})</h3>
    <p>${you}${last.run ? ` Best ${esc(last.game)} run: ${last.run.score} pts.` : ""}${bet}</p><ul class="placements">${rows}</ul>`;
}

// --- Other pets ---

async function refreshCampus() {
  let res;
  try {
    res = await api("/api/campus");
  } catch {
    return;
  }
  campus = res.pets;
  syncOthers(res.now);
  if (selected && pet) renderBuilding();
  const before = term;
  term = res.semester;
  if (account && before && before.number !== term.number) rejoin();
}

// A new semester started while the game was open: the old pet is history, and it's time to hatch again.
async function rejoin() {
  account = await api("/api/me").catch(() => account);
  if (account.pet_id === petId) return;
  pet = null;
  $("pet-card").hidden = $("actions").hidden = $("event-card").hidden = true;
  document.body.classList.remove("frozen");
  await play();
}

// Adds, moves, and removes other pets' sprites to match the latest campus snapshot.
function syncOthers(now) {
  const seen = new Set();
  const crowds = {};
  for (const o of campus.filter((o) => o.id !== pet?.id).sort((a, b) => a.id - b.id)) {
    seen.add(o.id);
    let entry = others.get(o.id);
    if (!entry) {
      entry = { locust: new Locust(othersLayer, sheet), tag: Object.assign(document.createElement("div"), { className: "tag pet" }) };
      entry.tag.addEventListener("click", () => {
        view.centerOn(entry.locust.x, entry.locust.y);
        if (entry.pet.building && !entry.pet.trip) select(entry.pet.building);
      });
      $("labels").append(entry.tag);
      others.set(o.id, entry);
    }
    entry.pet = o;
    const place = o.building ?? `house:${o.house}`;
    const slot = crowds[place] = (crowds[place] ?? -1) + 1;
    moveOther(entry.locust, o, CROWD[slot % CROWD.length], now);
    entry.tag.textContent = o.name;
    entry.tag.title = `${o.name} · ${o.career ?? "No career yet"} · ${homeOf(o).name}\n${otherStatus(o)}`;
  }
  for (const [id, entry] of others) {
    if (seen.has(id)) continue;
    entry.locust.remove();
    entry.tag.remove();
    others.delete(id);
  }
}

// Like movePet, but other pets stand a little off their spot so a crowd stays readable.
function moveOther(l, o, [dx, dy], now) {
  l.wear(o.wearing);
  l.setAnimation(o.activity === "sleeping" ? "sleep" : o.activity === "training" ? trainingAnimation(o)
    : o.activity === "task" ? "task" : o.tired ? "tired" : "idle");
  const [x, y] = spotOf(o.building, o.house);
  const to = [x + dx, y + dy];
  const trip = o.trip;
  if (trip) {
    const landed = !l.hop && l.x === to[0] && l.y === to[1];
    if (l.hop?.trip.start === trip.start || landed) return;
    l.walk({ ...trip, from: l.placed ? [l.x, l.y] : trip.from, to }, (now - trip.start) * 1000);
    return;
  }
  const dest = l.hop ? l.hop.to : [l.x, l.y];
  if (!l.placed || dest[0] !== to[0] || dest[1] !== to[1]) l.place(...to);
}

function placeOthers(now) {
  for (const { locust: l, tag } of others.values()) {
    l.update(now, view);
    const [sx, sy] = view.toScreen(l.x, l.y - sheet.anchor[1] - l.lift);
    tag.hidden = sx < -100 || sy < -50 || sx > innerWidth + 100 || sy > innerHeight + 50;
    tag.style.left = `${Math.round(sx)}px`;
    tag.style.top = `${Math.round(sy) - 4}px`;
  }
}

// --- Leaderboard ---

async function openLeaderboard() {
  openModal("leaderboard");
  try {
    renderLeaderboard(await api("/api/leaderboard"));
  } catch (e) {
    toast(e.message, "error");
  }
}

const offerList = (p) => p.offers.map((o) => `${o.title} at ${o.company}`).join(", ");

// The house and pet tables, with `mineId`'s pet and house highlighted.
function boardTables(board, mineId) {
  const mine = board.pets.findIndex((p) => p.id === mineId);
  const house = board.pets[mine]?.house;
  const houseRows = board.houses.map((h, i) => `<tr class="${h.id === house ? "mine" : ""} ${h.members ? "" : "empty"}">
    <td>${h.members ? i + 1 : "-"}</td><td>${esc(h.name)}</td><td>${h.members}</td><td>${dollars(h.net_worth)}</td>
    <td>${Math.round(h.swag)}</td><td>${h.interning}</td><td><b>${Math.round(h.score)}</b></td></tr>`).join("");
  const shown = board.pets.map((p, i) => [p, i]).filter(([, i]) => i < 10 || i === mine);
  const petRows = shown.map(([p, i]) => `<tr class="${i === mine ? "mine" : ""}"><td>${i + 1}</td><td>${esc(p.name)}</td>
    <td>${esc(homeOf(p).name)}</td><td>${esc(p.career ?? "-")}</td><td>${dollars(p.net_worth)}</td><td>${p.swag}</td>
    <td title="${esc(offerList(p))}">${p.offers.length}</td><td><b>${Math.round(p.score)}</b></td></tr>`).join("");
  return `<table class="board"><tr><th>#</th><th>House</th><th>Pets</th><th>Avg worth</th><th>Avg swag</th><th>Interning</th>
      <th>Score</th></tr>${houseRows}</table>
    <h3>Top pets</h3>
    <table class="board pets"><tr><th>#</th><th>Pet</th><th>House</th><th>Career</th><th>Worth</th><th>Swag</th>
      <th title="Internships won at job fairs">Offers</th><th>Score</th></tr>${petRows}</table>`;
}

function renderLeaderboard(board) {
  const s = board.semester;
  const when = s.over ? `Semester ${s.number} is over, so this is its final ranking.`
    : `Semester ${s.number} ends ${campusWhen(s.ends_at)}.`;
  setHTML($("leaderboard-body"), `<p>Each pet scores its net worth (cash, investments, and what it paid for the things it owns) plus its swag. A house
    scores its members' average, so a small house can beat a big one. ${esc(when)}</p>${boardTables(board, pet?.id)}`);
}

// --- End of semester ---

const resultsKey = (number) => `locusts-results-${number}`;

// Semester `number`'s final ranking. With `hatch`, the next semester has started and the player has no pet yet,
// so closing it goes on to hatching one.
async function openResults(number, { hatch = false } = {}) {
  localStorage.setItem(resultsKey(number), "1");
  $("results").dataset.hatch = hatch ? "1" : "";
  $("results-title").textContent = `Semester ${number}: final ranking`;
  setHTML($("results-body"), "<p>Tallying up...</p>");
  openModal("results");
  try {
    const board = await api(`/api/leaderboard?semester=${number}`);
    const mineId = number === account?.semester.number ? petId : account?.previous_pet_id;
    renderResults(board, mineId, hatch);
  } catch (e) {
    toast(e.message, "error");
  }
}

function renderResults(board, mineId, hatch) {
  const pets = board.pets, s = board.semester;
  // Ties go to the higher overall score, since pets come sorted by it. Swag and offers need at least one.
  const best = (score, least = -Infinity) => pets.reduce((top, p) => (score(p) >= least && (!top || score(p) > score(top)) ? p : top), null);
  const awards = [
    ["coin", "Top net worth", best((p) => p.net_worth), (p) => dollars(p.net_worth)],
    ["star", "Most swag", best((p) => p.swag, 1), (p) => `${p.swag} swag`],
    ["fair", "Top recruit", best((p) => p.offers.length, 1), (p) => `${p.offers.length} offer${p.offers.length === 1 ? "" : "s"}`],
  ].map(([icon, title, p, value]) => `<div class="award"><i class="big" data-icon="${icon}"></i><small>${title}</small>
      <b>${p ? esc(p.name) : "Nobody"}</b><span>${p ? esc(value(p)) : "-"}</span></div>`).join("");
  const place = pets.findIndex((p) => p.id === mineId);
  const me = pets[place];
  let mine = "<p>You didn't have a pet this semester.</p>";
  if (me) {
    const house = board.houses.findIndex((h) => h.id === me.house);
    mine = `<div class="you"><b>${esc(me.name)} placed ${ordinal(place + 1)} of ${pets.length}</b>
      <span>${esc(homeOf(me).name)} placed ${ordinal(house + 1)} of ${board.houses.filter((h) => h.members).length} houses.</span>
      <span class="chips"><span class="chip"><i data-icon="coin"></i>${dollars(me.net_worth)} worth</span>
        <span class="chip"><i data-icon="star"></i>${me.swag} swag</span>
        <span class="chip"><i data-icon="fair"></i>${me.offers.length ? esc(offerList(me)) : "No offers"}</span></span></div>`;
  }
  const next = hatch ? `<button data-results-hatch><i data-icon="sun"></i>Hatch a new locust</button>`
    : Date.now() / 1000 < s.next_starts_at
      ? `<p class="muted">Semester ${s.number + 1} starts ${esc(campusWhen(s.next_starts_at))}. Everyone hatches a new locust.</p>` : "";
  setHTML($("results-body"), `${mine}<div class="awards">${awards}</div>${boardTables(board, mineId)}${next}`);
}

function renderPlaces() {
  const rows = [`<button class="quiet" data-place="home"><i data-icon="home"></i>${esc(homeOf(pet).name)}</button>`];
  for (const id of careerBuildings()) {
    rows.push(`<button class="quiet" data-place="${id}"><i data-icon="train"></i>${esc(config.buildings[id].name)}</button>`);
  }
  const gym = config.gym.building;
  rows.push(`<button class="quiet" data-place="${gym}"><i data-icon="energy"></i>${esc(placeName(gym))}</button>`);
  setHTML($("places"), rows.join(""));
}

function weatherLine() {
  if (!weather?.available || !weather.label) return "";
  const temp = Number.isFinite(weather.temperature_f) ? ` · ${weather.temperature_f}°F` : "";
  return `${weather.label}${temp}`;
}

// The forecast is cached on the server, so polling this often still shares one request to the weather service.
async function syncWeather() {
  try {
    const report = await api("/api/weather");
    if (report?.available) {
      weather = report;
      sky?.applyForecast(report);
    }
  } catch {
    // The last forecast stays up, or the date-seeded clouds if Philadelphia never answered.
  }
  if (config) campusClock();
}

function campusClock() {
  const now = new Date();
  const fmt = new Intl.DateTimeFormat("en-US", {
    timeZone: config.clock.timezone, weekday: "short", hour: "numeric", minute: "2-digit", hourCycle: "h23",
  });
  const parts = Object.fromEntries(fmt.formatToParts(now).map((p) => [p.type, p.value]));
  const minutes = (Number(parts.hour) % 24) * 60 + Number(parts.minute);
  const toMin = (hhmm) => { const [h, m] = hhmm.split(":").map(Number); return h * 60 + m; };
  const start = toMin(config.clock.night_start), end = toMin(config.clock.night_end);
  const night = start > end ? minutes >= start || minutes < end : minutes >= start && minutes < end;
  const until = ((night ? end : start) - minutes + 1440) % 1440;
  const h12 = ((Number(parts.hour) + 11) % 12) + 1;
  $("clock-time").textContent = `${parts.weekday} ${h12}:${parts.minute} ${Number(parts.hour) < 12 ? "AM" : "PM"}`;
  const outside = weatherLine();
  $("clock-weather").hidden = !outside;
  $("clock-weather").textContent = outside;
  $("clock-next").textContent = `${night ? "Morning" : "Night"} in ${Math.floor(until / 60)}h ${until % 60}m`;
  // The icon follows the sky, which is real sunrise and sunset once the forecast is in. Before that it
  // follows the game's night, same as the countdown.
  const dark = sky ? sky.light(minutes).lights >= 0.5 : night;
  $("clock-icon").dataset.icon = dark ? "moon" : "sun";
  $("clock-term").hidden = !term;
  if (term) {
    const t = Date.now() / 1000;
    $("clock-term").textContent = t < term.ends_at ? `Semester ${term.number} · ${span(term.ends_at - t)} left`
      : `Semester ${term.number + 1} in ${span(term.next_starts_at - t)}`;
  }
}

// --- Selection and modals ---

function select(id) {
  selected = id;
  $("places").hidden = true;
  renderBuilding();
}

function openModal(id) {
  $("backdrop").hidden = false;
  for (const m of document.querySelectorAll(".modal")) m.hidden = m.id !== id;
}

function closeModal() {
  if (!$("results").hidden && $("results").dataset.hatch) return openHatch("new");
  // Without a pet, only the final ranking of a semester that's over can be put away.
  if (!pet?.career && $("results").hidden) return;
  $("backdrop").hidden = true;
}

// --- Accounts ---

const AUTH = {
  signin: { title: "Sign in", intro: "Welcome back. Sign in with your email.", submit: "Sign in", password: "Password" },
  signup: { title: "Create an account", intro: "Sign up with your email. We'll email you a link to confirm it.",
            submit: "Create account", password: "Password (10+ characters)" },
  forgot: { title: "Reset your password", intro: "We'll email you a link to choose a new one.", submit: "Send link" },
  reset: { title: "Choose a new password", intro: "At least 10 characters.", submit: "Save password",
           password: "New password" },
};

// The sign-in screen, in `mode` (a key of AUTH). `note` replaces the intro; a reset carries its emailed token.
function openAuth(mode, { note, token } = {}) {
  const m = AUTH[mode];
  const form = $("auth-form");
  form.dataset.mode = mode;
  form.dataset.token = token ?? "";
  $("auth-title").textContent = m.title;
  $("auth-intro").textContent = note ?? m.intro;
  $("auth-email-row").hidden = mode === "reset";
  $("auth-password-row").hidden = !m.password;
  $("auth-password-label").textContent = m.password ?? "";
  $("auth-password").autocomplete = mode === "signin" ? "current-password" : "new-password";
  $("auth-password").value = "";
  $("auth-submit").textContent = m.submit;
  for (const b of document.querySelectorAll("[data-auth]")) b.hidden = b.dataset.auth === mode || mode === "reset";
  openModal("auth");
  (mode === "reset" ? $("auth-password") : $("auth-email")).focus();
}

async function submitAuth(e) {
  e.preventDefault();
  const form = $("auth-form"), mode = form.dataset.mode;
  const email = $("auth-email").value.trim(), password = $("auth-password").value;
  const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body) });
  try {
    if (mode === "signup") {
      const r = await post("/api/auth/register", { email, password });
      return openAuth("signin", { note: `${r.detail} (Sent to ${email}.) Clicking it signs you in and starts the game.` });
    }
    if (mode === "forgot") return openAuth("signin", { note: (await post("/api/auth/forgot", { email })).detail });
    account = mode === "reset" ? await post("/api/auth/reset", { token: form.dataset.token, password })
      : await post("/api/auth/login", { email, password });
    $("backdrop").hidden = true;
    if (mode === "reset") toast("Password saved. You're signed in.");
    await play();
  } catch (err) {
    toast(err.message, "error");
  }
}

// Forget the pet on screen and ask to sign in again.
function signedOut() {
  account = null;
  petId = null;
  pet = null;
  $("pet-card").hidden = $("actions").hidden = $("signout-button").hidden = true;
  openAuth("signin", { note: "You've been signed out. Sign in again to keep playing." });
}

// Signed in: load the player's pet, or hatch one. Hatching waits out the break at the end of a semester, and a
// player back from last semester sees how it ended first.
async function play() {
  petId = account.pet_id;
  $("signout-button").hidden = false;
  if (petId) await refresh();
  await refreshCampus();
  if (!pet) {
    const home = homeOf(null);
    locust.place(home.x, home.y);
    view.centerOn(home.x, home.y, { instant: true });
    const s = account.semester;
    if (s.over) openResults(s.number);
    else if (account.previous_pet_id) openResults(s.number - 1, { hatch: true });
    else openHatch("new");
  }
}

// "new" names and hatches a pet; "declare" gives an existing pet from before careers its career.
function openHatch(mode) {
  if (!$("hatch").hidden && $("hatch").dataset.mode === mode) return;
  $("hatch").dataset.mode = mode;
  const declare = mode === "declare";
  $("hatch-title").textContent = declare ? `${pet.name} needs a career` : "A larva appears!";
  $("hatch-intro").textContent = declare
    ? "Its career decides where it trains and which upgrades it can earn. It can't be changed later."
    : "Name your locust, pick its career, and choose the college house it lives in. It will train on campus "
      + "and try to get cracked before the semester ends. Neither can be changed later.";
  $("hatch-name").hidden = declare;
  $("house-heading").hidden = $("house-options").hidden = declare;
  $("hatch-submit").textContent = declare ? "Declare" : "Hatch";
  setHTML($("career-options"), careerChoices.map((c, i) => `<label class="career-option">
    <input type="radio" name="career" value="${c.id}" ${i === 0 ? "checked" : ""}>
    <b>${esc(c.name)}</b><small>${esc(c.summary)}</small>
    <small class="muted">${c.paths.map((p) => esc(p.name)).join(" · ")}</small></label>`).join(""));
  // Starts on the emptiest house, so houses fill evenly unless players care which they're in.
  const counts = {};
  for (const o of campus) counts[o.house] = (counts[o.house] ?? 0) + 1;
  const ids = Object.keys(config.houses);
  const pick = ids.reduce((best, id) => ((counts[id] ?? 0) < (counts[best] ?? 0) ? id : best), ids[0]);
  setHTML($("house-options"), ids.map((id) => {
    const n = counts[id] ?? 0;
    return `<label class="career-option"><input type="radio" name="house" value="${id}" ${id === pick ? "checked" : ""}>
      <b>${esc(config.houses[id].name)}</b><small>${esc(config.houses[id].blurb)}</small>
      <small class="muted">${n} pet${n === 1 ? "" : "s"}</small></label>`;
  }).join(""));
  openModal("hatch");
}

// --- Setup ---

async function setupIcons() {
  // Background positions for every sprite in a sheet at `scale`, for elements with `cls` if given.
  const sheet = async (name, attr, scale, cls = "") => {
    const meta = await (await fetch(`/static/sprites/${name}.json`)).json();
    document.documentElement.style.setProperty(`--${name}${cls && `-${cls}`}-w`, `${meta.size * scale * meta.names.length}px`);
    return meta.names.map((n, i) => `${cls && `.${cls}`}[${attr}="${n}"] { background-position: -${i * meta.size * scale}px 0; }`);
  };
  const rules = [...await sheet("icons", "data-icon", 2), ...await sheet("icons", "data-icon", 4, "big"),
    ...await sheet("items", "data-item-art", 3)];
  document.head.append(Object.assign(document.createElement("style"), { textContent: rules.join("\n") }));
}

function addTag(id, name, spot, cls = "") {
  const tag = Object.assign(document.createElement("div"), { className: `tag ${cls}`, textContent: name });
  tag.spot = spot;
  tag.addEventListener("click", () => (id === "home" ? view.centerOn(...spot) : select(id)));
  $("labels").append(tag);
  tags[id] = tag;
  placeTags();
}

function setupEvents() {
  document.querySelectorAll("#actions button").forEach((button) => {
    button.addEventListener("click", () => act(button.dataset.action, null, { follow: button.dataset.action === "recall" }));
  });
  $("building-card").addEventListener("click", (e) => {
    const el = e.target.closest("button");
    if (!el) return;
    if (el.dataset.close !== undefined) select(null);
    else if (el.dataset.recall !== undefined) act("recall", null, { follow: true });
    else if (el.dataset.task !== undefined) act("task", { building: selected }, { follow: true });
    else if (el.dataset.workout !== undefined) act("workout", null, { follow: true });
    else if (el.dataset.train) act("train", { building: selected, path: el.dataset.train }, { follow: true });
  });
  $("event-card").addEventListener("click", (e) => {
    const el = e.target.closest("button[data-event-option]");
    if (el) answerEvent(Number(el.dataset.eventOption));
  });
  $("upgrade-grid").addEventListener("click", async (e) => {
    const node = e.target.closest("button[data-tier]");
    if (node) {
      const [path, i] = node.dataset.tier.split(":");
      tierShown[path] = Number(i);
      return renderUpgrades();
    }
    const el = e.target.closest("button[data-buy]");
    if (!el) return;
    const path = el.dataset.buy;
    const tier = pet.upgrades[path].tier + 1;
    if (await act("upgrade", { path })) {
      delete tierShown[path];
      toast(`Bought ${pathDef(path).tiers[tier - 1].name}!`);
    }
  });
  $("places").addEventListener("click", (e) => {
    const el = e.target.closest("button[data-place]");
    if (!el) return;
    const id = el.dataset.place;
    const spot = id === "home" ? homeOf(pet) : config.buildings[id];
    view.follow = null;
    view.centerOn(spot.x, spot.y);
    if (id === "home") $("places").hidden = true;
    else select(id);
  });
  $("upgrades-button").addEventListener("click", () => {
    for (const path in tierShown) delete tierShown[path];
    renderUpgrades();
    openModal("upgrades");
  });
  $("shop-button").addEventListener("click", () => { renderShop(); openModal("shop"); });
  $("shop-body").addEventListener("click", async (e) => {
    const el = e.target.closest("button");
    if (!el) return;
    const { item, resume, cancel } = el.dataset;
    if (item && await act("buy", { item })) toast(`${pet.name} got the ${itemDef(item).name}!`);
    else if (resume && await act("buy", { item: resume })) toast(`${itemDef(resume).name} will keep renewing.`);
    else if (cancel && await act("cancel", { item: cancel })) {
      toast(`${itemDef(cancel).name} won't renew. It lasts until ${campusWhen(pet.subscriptions[cancel].until)}.`);
    }
  });
  $("portfolio-button").addEventListener("click", () => { renderPortfolio(); openModal("portfolio"); });
  $("leaderboard-button").addEventListener("click", openLeaderboard);
  $("clock-term").addEventListener("click", () => (term?.over ? openResults(term.number) : openLeaderboard()));
  $("results-body").addEventListener("click", (e) => {
    if (e.target.closest("[data-results-hatch]")) openHatch("new");
  });
  $("fair-button").addEventListener("click", () => {
    fair = null;
    renderFair();
    openModal("fair");
    loadFair();
  });
  $("fair-body").addEventListener("click", (e) => {
    const el = e.target.closest("button");
    if (!el) return;
    if (el.dataset.apply) fairAct("apply", { role: el.dataset.apply, instead_of: el.dataset.instead ?? null });
    else if (el.dataset.withdraw) fairAct("withdraw", { role: el.dataset.withdraw });
    else if (el.dataset.play !== undefined) location.href = `${fair.game.page}?fair`;
    else if (el.dataset.back !== undefined) fairAct("back", { pet: Number($("back-pet").value) });
    else if (el.dataset.short !== undefined) {
      const amount = Number($("short-amount").value);
      if (!(amount > 0)) return toast("Enter an amount first.", "error");
      fairAct("short", { pet: Number($("short-pet").value), amount }).then(refresh);
    }
  });
  $("portfolio-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const move = e.submitter?.dataset.move ?? "invest";
    const amount = Number($("portfolio-amount").value);
    if (!(amount > 0)) return toast("Enter an amount first.", "error");
    if (await act(move, { amount })) {
      $("portfolio-amount").value = "";
      toast(move === "invest" ? `Invested ${dollars(amount)}.` : `Withdrew ${dollars(amount)}.`);
    }
  });
  $("places-button").addEventListener("click", () => { $("places").hidden = !$("places").hidden; });
  $("find-button").addEventListener("click", () => view.centerOn(locust.x, locust.y));
  $("zoom-in").addEventListener("click", () => view.zoom(1));
  $("zoom-out").addEventListener("click", () => view.zoom(-1));
  $("backdrop").addEventListener("click", (e) => {
    if (e.target === $("backdrop") || e.target.closest("[data-close]")) closeModal();
  });
  addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (!$("backdrop").hidden) closeModal();
    else select(null);
  });
  $("hatch-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const career = $("hatch-form").elements.career.value;
    const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body) });
    try {
      if ($("hatch").dataset.mode === "declare") {
        const next = await post(`/api/pets/${petId}/career`, { career });
        $("backdrop").hidden = true;
        render(next);
        toast(`${next.name} is on the ${next.career_tree.name} track!`);
        return;
      }
      const house = $("hatch-form").elements.house.value;
      const created = await post("/api/pets", { name: $("hatch-name").value, career, house });
      petId = account.pet_id = created.id;
      $("backdrop").hidden = true;
      render(created);
      view.centerOn(locust.x, locust.y);
      refreshCampus();
      toast(`${created.name} moved into ${homeOf(created).name}! Open Places to find its ${created.career_tree.name} buildings.`);
    } catch (err) {
      toast(err.message, "error");
    }
  });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) { refresh(); syncWeather(); } });
  $("auth-form").addEventListener("submit", submitAuth);
  for (const b of document.querySelectorAll("[data-auth]")) b.addEventListener("click", () => openAuth(b.dataset.auth));
  $("signout-button").addEventListener("click", async () => {
    await api("/api/auth/logout", { method: "POST" }).catch(() => {});
    location.replace("/");
  });
}

async function refresh() {
  if (!petId) return;
  let next;
  try {
    next = await api(`/api/pets/${petId}`);
  } catch (e) {
    if (e.status === 401) return;
    if (e.status !== 404) return toast(`Couldn't reach campus: ${e.message}`, "error");
    petId = account.pet_id = null;
    pet = null;
    openHatch("new");
    return;
  }
  render(next);
}

let lastClock = 0;
function loop(now) {
  placeOthers(now);
  locust.update(now, view);
  const { name, col } = locust.frame(now);
  showFrame($("portrait"), sheet, sheet.animations[name].row, col);
  for (const [id, el] of portraitWorn) showFrame(el, sheet, sheet.animations[name].row, col, id);
  if (!$("hatch").hidden) {
    showFrame($("hatch-sprite").firstChild, sheet, sheet.animations.idle.row, frameAt(sheet, "idle", now));
  }
  if (view.follow && !locust.hopping) {
    view.follow = null;
    view.centerOn(locust.x, locust.y);
  }
  view.tick();
  sky?.draw(now);
  if (now - lastClock > 1000) {
    campusClock();
    lastClock = now;
  }
  requestAnimationFrame(loop);
}

(async () => {
  [config, sheet, careerChoices] = await Promise.all([api("/api/config"), loadSheet(), api("/api/careers"), setupIcons()]);
  $("coffee-price").textContent = dollars(config.coffee_price);
  $("attribution").textContent = `Map data ${config.map.attribution}`;
  view = new MapView($("world"), config.map, config.buildings, { onSelect: select, onMove: () => placeTags() });
  othersLayer = document.createElement("div");
  view.sprites.append(othersLayer);
  locust = new Locust(view.sprites, sheet);
  Promise.all([api(config.map.lights.lamps), fetch(config.map.lights.windows).then((r) => r.arrayBuffer())])
    .then(([{ lamps }, windows]) => {
      sky = new Sky(view, config.clock, { lamps, windows }, { still: matchMedia("(prefers-reduced-motion: reduce)").matches });
      if (weather?.available) sky.applyForecast(weather);
    });
  $("hatch-sprite").append(Object.assign(document.createElement("div"), { className: "sprite" }));
  const gym = config.buildings[config.gym.building];
  addTag(config.gym.building, gym.name, [gym.x, Math.min(...gym.shape[0].map(([, y]) => y))]);
  renderPlaces();
  setupEvents();
  campusClock();
  syncWeather();
  setInterval(syncWeather, WEATHER_MS);
  // Where an emailed link landed: a confirmed (or expired) sign-up, or a password reset. The token comes out
  // of the address bar straight away, so it isn't left in history or a screenshot.
  const params = new URLSearchParams(location.search);
  const landed = params.get("auth"), reset = params.get("reset");
  if (landed || reset) history.replaceState(null, "", "/");
  localStorage.removeItem("petId");    // from before accounts, when the browser was all that knew whose pet was whose
  account = await api("/api/me").catch(() => null);
  const home = homeOf(null);
  view.centerOn(home.x, home.y, { instant: true });
  if (reset) openAuth("reset", { token: reset });
  else if (!account) {
    await refreshCampus();
    openAuth(landed === "expired" ? "signin" : "signup", landed === "expired"
      ? { note: "That link has expired or was already used. Sign in: if your email isn't confirmed yet, we'll send a fresh link." }
      : {});
  } else {
    if (landed === "verified") toast("Email confirmed. Welcome to Locusts!");
    await play();
  }
  setInterval(refresh, POLL_MS);
  setInterval(refreshCampus, config.campus_poll_seconds * 1000);
  requestAnimationFrame(loop);
})();
