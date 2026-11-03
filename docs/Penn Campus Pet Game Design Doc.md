# Penn Campus Pet Game: Locusts

Oct 1, 2026 · @a

## Concept

A multiplayer pixel-art tamagotchi set on Penn's campus, where each player raises a bug that grows into a pre-professional student over a three-week semester. Servers hold 10 to 20 players, pets live in real college houses and train in real campus buildings, and each semester ends with a ranking by money and swag before the server resets.

The art uses a four-color palette (cream, brick, near-black, and leaf green) with dithering, and campus is shown from a zoomed-out, off-center aerial angle.

## Core loop

A player checks in two or three times a day, and a typical check-in runs through these steps.

1. Feed the pet and send it home to sleep if it needs to.
2. Send it to a sub-building to train a career skill or do a campus task for money.
3. Resolve the task event that comes up during the session, a choice with a tradeoff.
4. Pull it out before it gets too tired.
5. Spend money on food, clothes, and accessories.
6. Once a week, apply to roles at the job fair and play that week's minigame.

## Pets and stats

Every pet starts as an identical larva and becomes a mini student-bug over the semester, specializing into one career and picking up clothes and accessories to match.

- Stats are hunger, sleep, and money. All three are computed from timestamps when someone looks at the pet, so pets keep living while everyone is offline.
- Time at a building drains sleep. Leave a pet too long and it gets too tired to keep training.
- Sleep lines up with real night, so pets rest while their owners do. Night is 11 PM to 9 AM Eastern, a pet at home sleeps automatically, and each campus day starts at 9 AM.
- A pet needs 10 hours of sleep for full effectiveness. Effectiveness drops 5% per missing hour, so 8 hours gives 90%. Sleep is counted over the 24 hours before 9 AM, so daytime naps count toward the next day.
- An exhausted pet (out of energy at a building) is sent home, and for the whole next campus day it burns energy twice as fast and works at half speed. Coffee lasts 4 hours: it makes a pet work 20% faster on any day, and halves the exhaustion penalty.
- Any pet, whatever its career, can work out at Pottruck instead of training. It costs time and energy but no money, and every 4 hours there adds 10 max energy for good, up to 160. Sleep refills a fitter pet proportionally faster, so 10 hours still tops it up. Working out doesn't count toward a training streak.
- Decay rates are tuned so two or three check-ins a day keeps a pet healthy. A pet needs two meals a day, and hunger drops at half speed while it sleeps.
- The player picks a career when the pet hatches, and it can't be changed. Pets hatched before careers existed are asked once.
- A starving pet orders DoorDash right away, wherever it is, unless a meal plan swipe or a perk feeds it first. DoorDash costs $10 for only 30 fullness, and the pet orders even if that puts its money below zero, so neglect costs money instead of stalling the pet.
- A pet nobody plays just sits where it was left (and keeps ordering DoorDash). There's no autopilot and nobody is removed, since every player hatches a new pet each semester anyway.

## Careers and upgrade paths

There are five careers, each with three upgrade paths trained at its own buildings. No building trains two careers. Each career has a signature mechanic, shown on the hatch screen. Of those, only Finance's investing works so far.

| Career | Signature mechanic | Paths | Trains at | Item bonus |
| --- | --- | --- | --- | --- |
| Tech | The baseline tree: one path wins minigames, one makes money, one unlocks exclusive roles | The Grind, Big Tech, The Lab | Towne, Amy Gutmann Hall, Moore, Levine, Skirkanich | Headphones |
| Pre-Med | Leans on GPA, which gates its top roles. Also takes care of other pets | The Gunner, Clinical Hours, The Wet Lab | Van Pelt, Goddard & Richards, Chemistry Labs (1973 Wing), Wistar | Lab coat |
| Consulting | Teams and travel: work alongside other pets, or disappear from the map for big paydays | Case Prep, Billable Hours, The Staffing Model | Steinberg-Dietrich, Vance, Houston | Quarter-zip |
| Finance | Owning things: market investments and Sansom Street restaurants other pets eat at | Investment Banking, The Markets, Private Equity | Huntsman, McNeil, Lauder Fischer | Not yet chosen |
| Pre-Law | Direct duels against other pets, and protecting players who are away | Mock Trial, BigLaw, Public Interest | Irvine, Silverman, Gittis | Not yet chosen |

A player only sees their own career. The server sends a pet's own tree and nothing else, and the map labels and Places list show only its career's buildings plus home. Every building still hosts campus tasks. The hatch screen shows each career's mechanic and path names but not its tiers.

Upgrades work like Bloons TD 6 monkeys. Each career has three paths of five tiers, and a pet can take one path to tier 5 and a second to tier 2, with the third locked. Tiers 1 and 2 are small, broadly useful boosts, since that's all a side path ever gets, and each tier 5 changes a rule of the game instead of adding a bigger number. Within a career, paths differ in risk: for Tech, Big Tech is the safe path, the Grind is high variance, and the Lab is slow but uncontested. Each path trains at its own buildings, so other players can read a pet's build from where it trains. Tiers unlock better tasks and buffs, and they count toward job fair role requirements. Careers should differ in shape, not just income rate, for example tech paying well with exhausting sessions and pre-med paying little early and more late.

Buying tiers:

- Training a path at one of its buildings earns XP for that path only, so XP can't be moved between paths.
- A tier is bought with its path's XP, and only when the crosspath rule allows the next tier.
- A path that is maxed out or locked by the crosspath rule can't be trained, though its buildings still host tasks.
- If a purchase locks the path the pet is training, the pet stops and goes home. XP it banked on a path that later locks is lost, which is the cost of committing.
- Every career's tiers cost the same, set in `config.py` rather than per career: tier n costs `tier_sessions[n - 1]` sessions of `xp_per_session` XP. With 1, 2, 3, 4, and 6 sessions of 45 XP, tiers cost 45, 90, 135, 180, and 270 XP, and a full 5-2-0 costs 855 XP. That's sized so a dedicated player finishes just before the last job fair and a casual player ends around 4-2-0.

The game loads paths from `locusts/careers/<career>.toml`. Tech:

| Tier | The Grind | Big Tech | The Lab |
| --- | --- | --- | --- |
| Focus | Job fair minigames | Money | Exclusive roles (engineering research) |
| Trains at | Towne | Moore | Levine, Skirkanich |
| 1 | CIS 1200: OCaml Trauma: +10% time in tech minigames | GitHub Green Squares: tasks pay +10% | Email 40 Professors: unlocks research-assistant tasks at Levine that also give +10% skill gain |
| 2 | CIS 1210: Big-O Nightmares: one free retry per job fair | Handshake Resume: apply to one extra role at each of the first two fairs | Overfit: your first model counts as a "project" for role requirements |
| 3 | LeetCode Streak: +3% minigame score per consecutive training day, up to +30%, reset if you skip a day | Referral: once per fair, qualify for a role you're one skill short of | Reviewer 2: after a job fair rejection, +25% minigame score at the next fair |
| 4 | Quant-pilled: unlocks quant roles that pay 75% more but need a top-3 minigame score | L4 Promo: internship pay +50% | GRASP Lab Keycard: unlocks robotics and hardware roles that pay 80% of a normal role, but only Lab pets can apply |
| 5 | Market Maker: at every fair, see who applied to each role before your applications lock, and quant roles only need a top-4 score | Rest and Vest: once you've held an internship, keep 75% of its pay for the rest of the semester, even after losing it | Pennovation Spinout: your startup pays a third of an internship's weekly pay for the rest of the semester, plus a small robot that follows your pet |

The other careers' tiers are in their TOML files, copied from the career skill tree spreadsheet.

Perks that touch the pet simulation or job fairs work now. Each tier lists them under `perks` along with an `effects` table, and `locusts/perks.py` combines the effects of every tier a pet owns: numbers and counts add, flags turn on, and role lists merge. Perks that need a system that isn't built yet are listed under `later`, and the Upgrades screen shows them as not active yet. Those systems are mostly pets interacting and random items. These simulation perks work now:

- Pay: Big Tech's and Billable Hours' +10% task pay, Excel Shortcuts' +33%, and Night Shift's +40% with 25% faster energy drain at buildings. Medical Scribe pays 30% of task pay while asleep, and Document Review pays 70% while home and awake. Summer Associate halves meal prices.
- Better tasks at one building: research-assistant tasks at Levine and legal aid at Gittis also bank 10% of training XP on their path, and deal sourcing at Lauder Fischer pays 10% more.
- Six-Minute Increments: once a campus day, pulling a pet out of a task bills it to the end of the hour. The daily limit stops players from cashing in by repeatedly starting and stopping tasks.
- Investment Banking: a pet that gets hungry at a building has a full meal delivered at the normal price instead of DoorDash (Seamless Dinner). On a penalty day, training gets 25% faster instead of half speed (100-Hour Week). With Burnout Immunity, a pet that runs out of energy keeps working, and only going hungry sends it home.
- Wet Lab experiments: going to work at the Chemistry Labs or Wistar starts an experiment (two with Wistar Badge). Each one runs 12 hours and speeds up all of the pet's training by 10%, wherever it trains.
- The Markets: a pet can move cash into and out of the market, up to a share of its total money set by its tier. Every morning at 9 AM, invested money moves by drift + swing × z. z is between -1 and 1, the same for every pet that day, and drawn from a seeded hash of the date. Index Funds caps a day's loss at 2%.

Job fair perks work too. They change the scored run (extra time, retries, score multipliers from LeetCode Streak and Reviewer 2), applications (extra applications, one referral per fair for a role one tier short, late swaps after applications lock, seeing how many or which pets applied and their builds), winning (unlocking exclusive roles, roles won outright, a looser top-N cutoff, Landmark's reserved clerkship for a pet that wins nothing), and internship pay (raises, loyalty raises for keeping a role, free food while interning, exit bonuses, Rest and Vest, startup pay, and Equity Partner's share of everyone's new internship pay). Goldman's IB role is lost if the pet gets exhausted.

Two perks act on another pet of the same semester while a fair is open, from the fair screen's Moves section:

- Amicus Brief (Pre-Law): once per fair, before its own first scored run, a pet backs another. The backer's runs get +20% and the backed pet's runs +10%, from then on, since a run's multiplier is set when it starts.
- Short Seller (Finance): once per fair, a pet bets up to half its money that another pet wins no role. If the target applied and won nothing, the bet pays back double; if it won a role, the bet is lost; if it never applied, the bet comes back. The target loses nothing either way, and the last fair's results say how the bet went.

## Economy

Campus tasks keep a pet fed, and real money comes only from internships won at job fairs.

- Pets start the semester with some money.
- Tasks at buildings pay the bare minimum, enough to eat but not much more.
- Internships pay a steady income until the next job fair.
- Food is the baseline expense.
- Clothes and accessories give small career-matched bonuses (lab coat for pre-med, headphones for tech) and double as visible status. Bonuses stay small enough that swag is mostly about showing off.
- Some items are limited per server or seasonal, so they stay worth buying when players get rich.
- The Shop sells items for any career, listed in `locusts/items.toml`. Their effects stack with career perks:
  - Gear: a used ThinkPad ($60, +10% training XP), which trades up to a MacBook Pro ($180, +20%), noise-cancelling headphones ($90, +10% task pay and 10% less energy burned at work), a mechanical keyboard ($120, +10% task pay), and an iPad and Pencil ($150, +10% training XP).
  - Wardrobe: a Penn hoodie ($40), a Penn cap ($25), a Patagonia vest ($130), a Canada Goose parka ($450), and Ray-Bans ($170). They're all swag, and they show on the pet.
  - Dorm room: a memory foam topper ($50, sleep restores energy 20% faster), blackout curtains ($30, each hour of sleep counts 10% more toward rest), a mini fridge ($70, a pet that gets hungry at home has a normal meal instead of DoorDash), an espresso machine ($110, 10% less energy burned at work), a weighted blanket ($65, sleep 15% faster), and LED strip lights ($25, swag only).
  - Subscriptions bill weekly from the pet's cash and lapse if it can't pay. Cancelling keeps one running until the paid week ends. The meal plan ($55/week) gives 14 swipes a week that don't roll over: Feed uses a swipe before money, and a starving pet swipes in wherever it is. The AI subscription ($20/week) gives +15% training XP and task pay.
  - Getting around: an Indego membership ($40) makes the pet walk 50% faster, and an electric scooter ($160) 40% faster on top of that.
  - Each permanent item adds swag worth less than its price.
  - Wearables (the wardrobe and the headphones) show on the pet on the map, in the HUD portrait, and to everyone else on campus. Each has a slot (shirt, outer layer, head, ears, eyes), and in each slot the pet wears the one with the most swag, so a parka covers a vest.
- Random items come later.

## Training and task events

While a pet works (training or a task, but not working out at the gym), something comes up about once every 2 to 3 hours of work (`event_hours` in `config.py`). The templates live in `locusts/events.toml`, and `locusts/events.py` loads and checks them.

- An event waits on the pet card for 45 minutes (`event_window_minutes`). If nobody answers, the pet ignores it, nothing changes, and the next one is scheduled.
- A free chance has one option that is all upside, like free pizza on Locust Walk. Ignoring it only misses the bonus.
- A tradeoff has two options. Each moves something up and something else down, and both come out slightly ahead: go to office hours (+0.06 GPA, -10 energy) or keep working instead (+1.2 hours of XP, -0.02 GPA).
- Events can be limited to training or tasks, to careers, and to buildings. Every career has its own, like PennApps for Tech and moot court for Pre-Law.
- Effects can change XP (in hours of training on the current path), money, energy, fullness, and GPA. XP only shows up in training events. An option can't be picked if the pet can't afford it or it would empty the pet's energy.
- The countdown is counted in hours worked, not wall-clock time, so the simulation can land events exactly. Which event comes up is seeded from the pet and its event count, so the same pet history always gets the same events.

### GPA

Every pet has a GPA, starting at 3.5 and capped at 4.0. It's shown on the pet card and only changes through events. Pre-Med leans on it hardest:

- Some Pre-Med roles need a GPA (Kaplan tutor 3.4, Monell intern 3.5, Perelman SURP 3.6), and the job fair shows it next to the skill requirements.
- Front Row Seat halves GPA losses from events.
- Office Hours Regular adds 0.1 GPA on the fourth day in a campus week (Monday to Monday) that the pet trains at Van Pelt. It lands that day rather than at the end of the week, so a semester's last week counts.
- Committee Letter lets a pet with a 3.7 GPA or better apply to any role it's one tier short of, as often as it likes, without using a referral.
- Summa Track adds 50% XP while GPA is 3.9 or higher.
- Curve Wrecker cuts every applicant with a lower GPA from the roles the pet applied to, when results are drawn. A tie isn't cut, and a Curve Wrecker can be cut by another one with a higher GPA.

## Job fairs

A job fair runs once a week, three per semester, and is the main place players compete directly.

- Fairs are asynchronous. Each one stays open for a day, applicants play whenever they want within that window, and results come out when it closes.
- Each company posts several roles, and each role requires specific skills (chem wet lab experience, math intuition, a project).
- Each pet applies to a limited number of roles, likely two, and applications lock in before anyone can see who else applied.
- A competitive minigame decides between applicants, with relevant skills giving advantages. Minigames are chosen for fun and don't need to relate to the job.
- Build a pool of six to ten minigames over time and rotate them, rather than shipping new ones every week.
- Winning a role gives an internship that pays until the next fair, so early leaders have to keep winning.

How the first version works:

- Schedule: a fair opens every Wednesday of a semester at 9 AM campus time and closes 24 hours later (`fair_weekday` and `fair_hours` in `config.py`). Results come out 5 minutes after closing, so a run started just before close still counts. A week whose results wouldn't be out before the semester ends has no fair, and an internship from the last fair pays until the semester ends.
- Roles live in each career's TOML as `[[role]]` entries with a company, title, pay (a multiple of the $300 weekly base), and required tiers. Each fair posts three of a career's regular roles, always including one with no requirements, picked by a seeded hash of the opening time. Exclusive roles that a perk unlocks are posted at every fair, and a pet only sees its own career's roles and the exclusive roles it has unlocked.
- Applications: a pet applies to up to two roles, best first, and doesn't see other applicants unless a perk shows them. Applications lock when the pet starts its first scored run.
- Advantage: career tiers give extra hits. A pet can take one more hit per two tiers it owns (`tiers_per_extra_hit`), so a 5-2 pet gets six hits instead of three.
- Matching: pets that applied and played are matched by deferred acceptance, with pets proposing in preference order and each role keeping the best score. Pets that applied but never played aren't considered. Top-N roles like Jane Street only take a pet whose raw score ranks in the top N among everyone who played at that fair.
- Internships pay by the hour, whatever the pet is doing, from one fair's results to the next. A pet that wins the same role again keeps it, which counts for loyalty raises.
- There's no scheduler. Every request settles the fairs first: it posts the next fair, and resolves any fair whose results time has passed by advancing every pet to that moment before matching. That keeps pet pay the same no matter when anyone looks.
- Minigames: each fair plays one game, and fairs take turns through the pool in order (Locust Walk, then Hey Day Stacker, then Locust Walk again). The game is stored on the fair when it's posted. Career tiers and perks work the same way in every game: extra hits, extra time, extra attempts, and score multipliers.

### Minigame: Locust Walk

The first minigame in the pool is a dodger. The pet runs up Locust Walk for 60 seconds while the path scrolls toward it, and the player switches between five lanes to avoid obstacles.

- Obstacles come in rows and have distinct patterns. Flyer-handers stand in one lane, tour groups block two lanes and take longer to pass, and bikes and scooters sweep across every lane. The Button and the LOVE statue are landmarks that get a row to themselves every 15 rows.
- Score is distance covered. A hit stuns the pet for a moment, which costs distance, and three hits end the run early (more at job fairs, from career tiers). Free swag pickups add a small bonus.
- Each attempt gets its own server-generated seed. To keep different seeds fair, the schedule is the same for everyone: where each row is, how many lanes it blocks, and how fast the pet runs. The seed only picks which obstacles fill a row and which lanes they take. The generator checks each row and redraws it until a perfect run can get through without a hit.
- Each applicant gets one scored attempt per fair. Unscored practice runs on throwaway seeds are unlimited.
- The game runs at a fixed 60 ticks per second with integer positions, and the client logs inputs as lane changes by tick. That makes the server replay exact.
- Grind perks plug in directly. OCaml Trauma's extra time stretches the run to 66 seconds, so it covers more distance. Big-O Nightmares adds a second scored attempt, and the better score counts. LeetCode Streak multiplies the score, and quant roles compare raw scores across everyone who played at the fair.
- A scored run starts from the job fair screen and opens `/walk?fair`. It has to be submitted within two minutes of the run's time ending, so a run left paused in a background tab for longer than that is lost.

### Minigame: Hey Day Stacker

The second minigame is a timing game. The pet builds the junior class tower on College Green for 60 seconds: a block slides back and forth above the tower, and the player taps to drop it.

- Whatever hangs past the block below is trimmed off and falls, so the tower narrows with every sloppy drop. Missing the tower completely, or leaving a sliver under 2 pixels wide, costs a hit, and the next block keeps the tower's width. Three hits end the run early (more at job fairs, from career tiers).
- A drop within 2 pixels of the block below is perfect: it snaps into place and scores a bonus. Every third perfect drop in a row widens the tower by 4 pixels, up to its starting width.
- Score is 10 per floor plus 5 per perfect drop. Blocks slide faster with every floor, up to a cap.
- The field is more than three blocks wide, so a block can miss even the starting tower, and every block slides in from one edge of the field. The speed schedule is the same for every seed, which only picks the side each block comes from.
- Like Locust Walk, it runs at 60 ticks per second with integer positions. The client logs the tick of each drop that lands, and the server replays them. Practice is at `/stacker`, and scored runs at `/stacker?fair`.

## Semesters, servers, and houses

A semester lasts three weeks, ends with a server-wide ranking, and then resets so everyone starts even.

- Semesters run back to back from `semester_epoch` (Monday Oct 26 2026), each starting at 9 AM and lasting `semester_weeks` weeks (`locusts/semesters.py`). The last day (`semester_break_days`) is a break: play stops Sunday at 9 AM and the next semester starts Monday at 9 AM. The HUD clock shows the time left, and during the break, the time until the next one.
- At the end, every pet freezes as it was: it stops starving, earning, and moving, and nothing can be done with it. Job fairs skip the break. The final ranking pops up once, and stays open from the clock and the Houses screen.
- The final ranking covers net worth, swag, and recruiting outcome: the house and pet standings, the player's own place, and awards for top net worth, most swag, and the most internship offers won. The long-term goal is a cracked pet with lots of money and swag, measured against the rest of the server.
- Every player hatches a new pet each semester (one per account per semester, and not during the break), and sees how last semester ended before hatching. Old pets stay in the database with their semester's ranking (`/api/leaderboard?semester=N`). Pets from before semesters joined the one running when the server first started with them.
- Each server holds 10 to 20 fixed players.
- New players queue, and a fresh server starts when enough are waiting, instead of joining a semester already underway.
- Anyone can sign up; an email address is enough.
- Each pet lives in a real college house, picked when it hatches and fixed after that: Harnwell, Harrison, Rodin, Du Bois, Gutmann, Gregory, Stouffer, Kings Court English, Hill, or Lauder (the Quad's buildings aren't named on the map yet). The hatch screen starts on the emptiest house. A pet walks home to its house and sleeps there, so where it lives decides how far its career buildings are. Each house also has one small perk, like 5% faster sleep regen or 10% cheaper meals, listed in `locusts/houses.toml`.
- The Houses leaderboard scores each pet by net worth (cash, investments, and what it paid for the items it owns for good, an upgrade counting the item it traded in; not subscriptions, food, or coffee) plus swag, and each house by its members' average, so a small house can beat a big one. It also lists the top pets.

## Multiplayer and notifications

Outside job fairs, other players matter through what you can see of them on campus.

- Every pet is visible on the campus map in real time, including which sub-building it is training in. Two pets grinding the chem wet lab all week tells you who is coming for the same role.
  - Other pets are drawn with name tags, hopping along their walks. A tag's tooltip shows the pet's career, house, and what it's doing. Pets at the same building stand around it instead of on top of each other, and a building's card lists who else is there.
  - Only public facts are shared: name, career, house, building, walk, activity, and whether it looks tired. Stats, money, and upgrades stay private (the leaderboard shows net worth and swag).
  - For now every pet shares one world. Servers of 10 to 20 players come later.
- Job fairs and the house leaderboard are the direct competition and loose cooperation.
- Notifications go out by email for fair openings, fair results, and a pet that is starving or exhausted.

## Technical notes

The server is Python and owns all game state, and the browser client only draws and sends input.

- FastAPI with WebSockets and SQLite on the server, a thin JavaScript canvas client, hosted on Render or Fly.io.
- No ticking game loop for pets. Stats are computed from stored timestamps on read.
- Pets walk between buildings at a fixed speed (`travel_px_per_second`), so farther buildings take longer. The server stores each trip, and a pet only starts training, a task, or sleep once it arrives. The client animates the stored trip, so a reload picks the walk up partway.
- The campus map is rendered once by a Python script from OpenStreetMap building footprints, extruded into the aerial view. Credit "© OpenStreetMap contributors" in the game. Landmark buildings get hand-added detail from Google Earth reference. The script is `tools/render_campus.py`, which reads `data/map.osm` and writes `campus.png` and `buildings.json` to `locusts/static/map/`. It also renders every tree twice more, with its crown blown 1 and then 2 pixels east (the top twice as far), and writes the pixels that change to `breeze1.png` and `breeze2.png`, with each tree's box in `breeze.json`. The client leans every tree by one shared wind: two slow sways crossing campus at different speeds and angles, with gusts of varying strength and width sweeping west to east on top. Neighboring trees lean together and ease over and back a pixel at a time, but each tree catches the wind a little late or early, bends more or less than its neighbors, and now and then rustles back for a moment in a strong gust. The breeze is off when the browser asks for reduced motion, and when zoomed out too far to see it. The map covers the undergrad core and leaves out the hospital block, so HUP and BRB aren't playable buildings.
- Each street's drawn width is split into lanes by `lane_layout` in the renderer, from OSM's lane tags where they exist. Every lane is at least 2.6 m wide (a car is 1.9 m), so OSM counts that don't fit the drawn width are trimmed. A curb parking strip goes on each side that still leaves 2.8 m lanes. The same layout is painted onto the map (dashed lines between lanes going the same way, a center line between directions that is doubled on the big streets, and a solid line along each parking strip) and written into `traffic.json` for the cars.
- Cars aren't baked into the map. The renderer writes the drivable streets as a graph to `traffic.json` (each edge pointing the way its forward traffic flows, with its lane offsets, plus bridge crossings), a car sheet `cars.png` with 16 headings in six paints, and `occluders.png`, the pixels of every building, tree, and lamp that stands in front of a street, plus footbridges over streets (like Locust Walk over 38th), so cars drive under them. Streets that cross on the same level without sharing an OSM node get a junction where they cross. `locusts/static/campus/traffic.mjs` drives about one car per 120 m of lane. Each car has a style:
  - **Cruisers** wander the grid, mostly going straight.
  - **Loopers** circle their blocks, always turning the same way.
  - **Commuters** drive fast and straight across campus and off the map, and new cars drive in from the edges.
  - **Parkers** pull over at the curb for a while, then head off again.
  - Some cars stop briefly at intersections.
- Cars don't overlap:
  - **Lanes:** right turns go from the curb-side lane and left turns from the innermost. A car picks its turn about 45 m out and changes lanes for it, one lane at a time and only into a gap. If it can't make it in time, it picks a turn its lane allows. A car going straight takes the shorter line: every 2 seconds it counts the cars ahead in its lane and the ones beside it (80 m out), and moves over when a neighbor has at least two fewer. A gap only counts if it leaves room to brake, for the car moving over and for whatever is coming up behind it.
  - **Turns:** each one is a curve whose ends run along both lanes, so a car always leaves and enters lanes facing the way they go.
  - **Following:** each car keeps back from the car ahead, including one turning into or out of its lane.
  - **Entering an intersection:** a car only starts a turn when there's room to clear the intersection completely, and nobody is standing on its turn path.
  - **Intersections:** a car picks its whole way through a compound intersection at once, weighed by the turn it makes from end to end, so two rights across a divided street's middle count as the U-turn they are (and almost never happen). It lines up in the lane for that overall turn. Turning, it arcs straight from its lane onto the far street rather than following the links (which run between the halves' centerlines, so a car in the inside lane would swing out the wrong way to reach one); going straight across, it drives the links. Turns are shaped like road corners: straight in, an even curve as wide as the shorter side allows, and straight out, joining the far street a few meters in when it starts too close to the corner to round it. Each car reserves its whole route through the intersection, including the links of a compound one. Routes that cross take turns, while ones that don't cross (like opposite through-traffic) go together. The approach that gets the intersection keeps it for a platoon of up to 3 cars if they're coming, then the longest wait goes next. Straight-through traffic crosses at 7 m/s, and turns at 4.5 m/s.
  - **Stop lines:** each one is placed where a waiting car is clear of every other approach's turns. Lanes too short for that, like the links in a divided street's intersection, are treated as part of the intersection, and cars cross them in one go.
  - **Merges and forks:** where streets meet at a sharp angle, lanes are trimmed back until they separate.
  - **Parking:** parked cars sit in the parking strip and only pull out into a gap.
  - **Bridges:** cars passing under one are hidden.
- Each frame, cars are drawn sorted by how far down the screen they are, clipped to the island, and then the occluder pixels around each car go back on top, so buildings and trees stay in front of the street. If the browser asks for reduced motion, every car stays parked.
- Day and night, and the weather, are drawn by `locusts/static/campus/sky.mjs` as three canvases over the map and pets but under the labels and HUD:
  - **Light:** a multiply layer tints the whole scene by the campus clock, around the game's night: dawn in the 90 minutes before night ends, golden hour and twilight in the 2.5 hours before it starts, and dark blue through the night.
  - **Night lights:** a screen layer lights them as it gets dark: the windows, the street lamps (`lights.json`), and headlights ahead of every car.
  - **Windows:** they switch on and off one at a time over the night. Only real building windows light up: the renderer tags each window as it draws a wall (a punched window, one floor of a ribbon or glass wall, a shopfront), so parked cars and other glass never glow. Each window's box and exact visible pixels go into `windows.bin`, in a random order that is the order they switch, so a window always lights up whole and switching one never touches its neighbors. Nearly all are lit through the evening; people start going to bed at midnight, only about one in eight is still lit at 4 AM, and they come back on until about half are lit by 9:30 AM, when daylight washes them out. Every second and a half some window flips against the trend for a minute or so.
  - **Clouds:** pixel clouds drift east above their own shadows and fade as you zoom in so they never hide the pets. How cloudy it is and where each cloud starts come from the campus date and the time, so every player sees the same sky.
  - **Testing:** add `?time=HH:MM` to the page URL to hold the light at a campus time.
- Animations are sprite sheets timed by elapsed milliseconds, with sprites kept on whole pixels. The locust sheet, HUD icons, and shop item sprites (`items.png`, one per id in `locusts/items.toml`) are drawn in code by `tools/draw_sprites.py` in the map's four-color palette. The locust is a student standing on its hind legs, about two cars tall on the map. Its body is one green with black and white detailing, and red is saved for what it carries: a backpack, coffee, a laptop while training, a headband while working out at Pottruck (running in place, bag set down), and a broom on tasks. Clothes from the shop are drawn into every frame too, in their place among its layers (a hood behind the head, sleeves on the near arm), and `wear.png` keeps just the pixels each one changes, which the client stacks over the sprite. The HUD fonts are Silkscreen and Pixelify Sans (both OFL).
- The game client (`locusts/static/campus/`) draws the map into a full-window canvas with the HUD as HTML panels on top. Zoom is continuous and eases toward wherever the wheel, pinch, or buttons point it. Everything on the map (cars, breeze, building highlights, and last the time of day's light and night lights, blended in) draws into that one canvas in map pixels through the same transform, so overlays land on the same device pixels as the art at any zoom. Pets are HTML sprites over it, tinted to the light by a CSS filter, and the clouds get one more canvas above them, at CSS-pixel resolution. Building highlights are built once per building as pixel masks at map resolution, so their edges follow the art's pixel grid.
  - **Memory:** the map-sized overlays (the breeze frames, `occluders.png`, and the lit windows) are mostly empty, so the client keeps only their used tiles, packed into atlases (`sparse.mjs`), and loads them one at a time. Chrome holds each full-window canvas about three times over, and blend modes and window-sized HTML layers cost more window-sized GPU buffers, so the page keeps to two canvases, no CSS blend modes, and its own small layer for each HUD panel and map label.
- Other pets come from `GET /api/campus`, which each client polls every `campus_poll_seconds`. The server advances every pet to the request time without saving, the same way reading one pet does. Walks are stored trips, so a client animates them smoothly between polls. The leaderboard is `GET /api/leaderboard`. Polling can become a WebSocket push once servers exist.
- The server validates every client message as a request, never applying client-reported state.
- **Accounts and security** (`locusts/auth.py`, routes in `locusts/app.py`):
  - Players register with an email address and a password of 10 to 128 characters. The account works once the emailed link is clicked. Each sign-up's link carries the password chosen with it, so someone who registers another person's email first can't pick the password that person's click confirms. A sign-up for an email that already has an account changes nothing and tells the owner. "Forgot password" emails a reset link that works for an hour; using it signs out every other session.
  - Passwords are hashed with scrypt, each with its own salt. Sessions are 30-day HttpOnly, SameSite=Lax cookies, Secure over HTTPS. The server stores only SHA-256 hashes of session and emailed-link tokens. Answers are the same whether or not an email has an account, and a password check takes as long either way.
  - Each account owns one pet (`pet_owners`). One middleware guards every `/api/pets/{id}/...` route: signed out gets 401, and someone else's pet answers 404 as if it didn't exist. The map, leaderboard, config, and minigame practice stay public.
  - Unsafe API requests need an `X-Locusts: 1` header and, if they name an origin, this site's. A page on another site can't add the header without a CORS preflight, which the server never approves, so it can't forge a player's requests. Every response sends a strict Content-Security-Policy (scripts and requests from this server only, no framing), `nosniff`, and no Referer, so a reset link's token can't leak.
  - Sign-in attempts are rate-limited per email (10 per 15 minutes) and per IP, emails per address (3 an hour), and resets per IP. Request bodies are capped at 256 KB, and a run at 20,000 inputs. Pet names are 1 to 20 characters with no control characters. Every query binds player input as parameters, and the connection refuses to attach another database or read files from SQL. A pet id in a URL has to be a short integer. A body can't include fields the route doesn't take, and money amounts have to be finite.
  - Configuration, by environment variable: `LOCUSTS_BASE_URL` (the site's public `https://` address, for emailed links and the allowed origin; set it in production), `LOCUSTS_SMTP_HOST`, `LOCUSTS_SMTP_PORT` (587 for STARTTLS, 465 for TLS), `LOCUSTS_SMTP_USER`, `LOCUSTS_SMTP_PASSWORD`, `LOCUSTS_MAIL_FROM`, and `LOCUSTS_TRUST_PROXY=1` behind a reverse proxy, so rate limits see players' addresses. Emailed links always use `LOCUSTS_BASE_URL` when it is set, and otherwise only localhost, 127.0.0.1, or a host listed in `LOCUSTS_ALLOWED_HOSTS`, so a forged Host header can't point a confirmation or reset link somewhere else. Without an SMTP host, emails, links and all, go to the server log, for development.
  - Pets from before accounts had no owner, so the migration to accounts cleared them.
- Minigame attempts use a server-generated seed, and the server replays logged inputs to verify each score. Minigames live in `locusts/minigames/`, with tuning values in each game's config (`WalkConfig`, `StackerConfig`), and `minigames/games.py` lists them in rotation order. Each game's browser simulation (`locusts/static/walk/sim.mjs`, `locusts/static/stacker/sim.mjs`) is tested in Node against its Python replay. The pages share their overlay and fair flow through `locusts/static/minigame/`.
- Shop items are in `locusts/items.toml`, loaded and validated by `locusts/shop.py`. Items may only use a whitelist of perk effects, and `perks_of` adds owned items and running subscriptions after the career's tiers. Subscription bills are segment boundaries in the pet simulation, so a lapse mid-shift ends its perks at the exact moment it lapses.
- Job fairs are in `locusts/fairs.py`, stored in the `fairs`, `fair_entries`, and `fair_attempts` tables. Each fair records its game, and each scored run records game-specific stats as JSON. Settling and every fair action run inside a SQLite `BEGIN IMMEDIATE` transaction.

## Deferred decisions

- [ ] Balancing: decay rates, prices, task pay, and how much skills outweigh minigame skill at job fairs
- [x] Upgrade paths for every career
- [x] Perks that only touch the pet simulation (pay, food, energy, XP, investing, experiments)
- [x] Job fair perks (extra time, retries, score boosts, applications, referrals, exclusive roles, internship pay)
- [ ] Perks that wait on task events and GPA, items, or pets interacting (listed under `later` in each career file)
- [ ] Item bonuses for Finance and Pre-Law
- [ ] Whether a pet can ever switch careers (for now it can't)
- [x] A second job fair minigame (Hey Day Stacker), with fairs taking turns between games
- [ ] More minigames for the pool (candidates: Wawa hoagie rush, Quad snake, code typing race)
- [x] How career tiers turn into a minigame advantage (extra hits, one per two tiers)
- [x] Job fair schedule within a semester (weekly, three per semester)
- [x] Penalty for an exhausted pet
- [x] Penalty for a starving pet (DoorDash, into debt if need be)
- [x] Autopilot and removal thresholds (neither: idle pets just sit there until the semester ends)
- [x] When a pet declares its major (at hatching)
- [x] Stat-boosting items (the Shop) and Pottruck fitness
- [ ] Random items, and what swag does beyond showing off
- [ ] Game name
