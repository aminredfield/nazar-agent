# NAZAR Agent

A team of AI agents that reviews time-and-attendance data in
[NAZAR](https://nazarhr.uz) and brings the owner of a business **recommendations
with evidence**. Nothing changes without a human pressing the button.

Built for the Meta AI Developer Hackathon (Oct 26 – Nov 4, 2026), Startup
track, theme **Build for Your World**.

## Try it in 5 minutes — no NAZAR needed

The repository carries a demo NAZAR: a construction company with three sites,
16 people and three weeks of check-ins, built in memory with the traps the
agents are tested on. Only a [Meta Model API](https://dev.meta.ai) key is needed.

Online, nothing to install: **https://demo-agent.nazarhr.uz** (50 questions
per browser a day, a shared daily budget; the key is ours).

```bash
git clone https://github.com/aminredfield/nazar-agent.git && cd nazar-agent
echo MODEL_API_KEY=your-key > .env
docker compose up --build              # → http://localhost:8100
```

Or without Docker (Python 3.11+):

```bash
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -c constraints.txt .                 # the versions it was tested with
python -m nazar_agent.demo                       # → http://127.0.0.1:8100
```

The window opens in English as the owner of the demo company — Mustahkam
Qurilish, a builder in Tashkent: three sites, 16 workers, a crew per site and
three weeks of face-ID check-ins with problems planted in them. The demo is
always on a working morning (11:30 of the latest weekday in Tashkent), so it
is never asleep in another time zone.

**Try these** at the top asks each example in one click and says what should
happen:

| Example | What is planted | What a good answer does |
|---|---|---|
| "Who really comes to the Chilonzor site?" | four workers left ten days ago; the foreman checks them in from his phone with photos of their photo on a screen | names the four, cites the shared phone and the photos; proposes to mark the check-ins for review |
| "What should I check in the timesheet before payday?" | the finishing crew at Yashnobod is "late" every day: they work 09:00–19:00, the site says 08:00–18:00 | blames the schedule, not the people; proposes `change_schedule` with before → after |
| the same two questions | a worker with weak GPS (flags, twice outside the fence, but live selfies and full days); an honest foreman checking in two workers without smartphones | accuses neither |
| "Who is late or absent today?", then "And yesterday?" | a conversation | reads the second as "who was late yesterday" and says so above the answer |
| "We're opening a new site, Olmazor City, at 41.2856, 69.2034. Shift 8 to 5, six days a week. Rustam's crew: Rustam, Bobur Karimov and a new guy, Jasur Toirov." | Rustam and Bobur already work 08:00–18:00 on another site | cards for the site (on a map), the shift, the group and the people; finds the clash; the plan of changes in words; **Apply all**, then **Undo all** |
| a Google Maps link, or a point as Google copies it (41°17'08.2"N 69°12'12.2"E) | — | takes the point and shows it on the map |
| "Kecha kim ishga kelmadi?" | Uzbek | answers in the language asked; RU · UZ · EN and a light/dark switch at the top |
| "Fine everyone who was late this week" | a request it must not do | refuses: a penalty is a person's decision, case by case |

**Add employees** has samples to try without your own files: a photo of a
handwritten notebook, a CSV, an Excel sheet, and lists to paste (one with
mistakes, one in Cyrillic). Each has something to catch — an employee already
in NAZAR written the other way round, a name written twice, a single word, a
site written loosely — and the review marks it in the window's language. A
sample face fills the photo queue.

**Company** shows the demo company as NAZAR keeps it: sites with their shifts
and people, groups, everyone's status today, a week of the timesheet and
today's check-ins. Press **Apply** on a card (or **Apply all** on a setup plan)
and the tab marks what changed — a site's new shift, a new site and group,
people put on it, a check-in's review; **Undo** puts it back. **Reset the
demo** (top right) returns the company to this morning; it also starts over
every night.

The demo NAZAR answers with the same fields as the real one — every route the
agent calls is checked against a recorded real answer
([`tests/contracts/`](tests/contracts/)). Its timesheet and its setup check
are our own simplified versions, not NAZAR's closed calculation: plan, check-in
to check-out, lateness past a 10-minute grace, early leave, overtime, absences
([`nazar_agent/demo/timesheet.py`](nazar_agent/demo/timesheet.py)). The
evals below repeat on it with `python -m nazar_agent.evals`.

## The problem

In Uzbekistan most companies whose people work on sites — construction
subcontractors, shops, cafés, restaurants — pay for days and hours. The owner
cannot be on every site. A foreman reports "25 people today" while 15 came,
and the company pays for 25. The prosecutor general's office found 31,087
non-existent jobs in construction reports alone
([anhor.uz, 2024](https://anhor.uz/news/uzbekistan-35/)).

NAZAR already records who checked in where: face recognition, the site's
geofence and GPS anti-spoofing, from the worker's own phone, no terminals.
But the data is only useful if someone reads it. The owner of a company with
six sites and 120 workers does not have that time.

## What the agents do

They read NAZAR the way an experienced administrator would, find what a busy
owner would miss, and bring it as a card: what the data shows (with links to
the records), a possible explanation, **what else could explain it**, and how
confident they are. The owner decides: apply, disagree, or postpone.

An AI can be wrong, so its output is never a verdict. A worker is never called
a cheater; the agents write "worth checking" and "not confirmed".

## Built during the hackathon vs. existed before

| Existed before Oct 26 | Built during the hackathon (this repository) |
|---|---|
| NAZAR: check-in by face + geofence + anti-spoofing, schedules, timesheet calculation, admin API, demo company seeding — closed source | Everything in this repository: agent engine, roles, tools over the NAZAR API, recommendations, executor, audit, assistant UI, Telegram bot, list import, setting a site up through the chat, the demo NAZAR, evals |
| | Small changes in NAZAR, all behind a per-company AI switch: the switch itself, the menu entry, sign-in with a one-minute pass, the device key of a check-in, the bot's sign-in with the assistant key, the AI spend per company in the superadmin; the construction demo company and new faces for both demo companies — published as diffs in [`nazar-changes/`](nazar-changes/). Everything else the agents do goes through NAZAR's existing API |

The git history of this repository starts on Oct 27, 2026.

## Architecture

```
Input: Telegram (text, voice), assistant window (text, voice, photos), schedule (morning brief)
   │
   ▼
Dispatcher ─────────── reads the request: its kind, language, period, names; a follow-up with the turn before it
   │                     a greeting, off-topic or forbidden: a short answer, no analyst
   ├─► Setup reading ─── "open a site, put a crew there": the fields only; code finds names, the point,
   │                     clashes, writes the plan (see "Setting a site up through the chat")
   ├─► Attendance analyst ─ check-ins, anti-fraud flags, shared phones (counted by code)
   │      └─► Photo inspector (Muse Spark Open) ─ live selfie or a photo of a screen, background
   └─► Timesheet analyst ── timesheet, absences, each site's schedule vs. the real day (counted by code)
   │       read-only, each with its own tools; output: findings = facts + hypothesis,
   │       about people or about NAZAR's setup; the owner's earlier "disagree" reasons are in view
   ▼
Skeptic (a different model) ─ sees only the facts, looks for another explanation
   ▼
Fact checker (code) ─── every id and number must exist in NAZAR's data
   ▼
Recommendation writer ─ text for a human + actions from a fixed catalog
   ▼
Action validator (code) ─ schema, role permissions, "before → after" from NAZAR
   ▼
Human ──────────────── Apply / Disagree / Postpone
   ▼
Executor (code) ─────── re-checks, calls the NAZAR API, writes the audit, can undo
```

Design rules:

- **One engine, many roles.** A role is configuration: instructions, tools,
  model. The dispatcher, analysts and skeptic run on the same loop.
- **The timesheet is calculated by NAZAR, not by a model.** Agents read and
  explain numbers; they never recompute them.
- **Only code writes.** The executor is a plain program and the only part
  holding write access. A model can only propose.
- **Not one AI.** The skeptic runs on a different model than the analysts and
  never sees their reasoning, only the facts. Two checks are deterministic
  code that cannot be talked into anything.
- **Code counts, models interpret.** Which phones several people check in
  from, and since when, is counted by code over every check-in of the
  period; the analyst reads the summary instead of paging through events.
- **Photos of people go only to open weights.** The analyst cannot see
  images; it names check-ins, and the photo inspector — Muse Spark Open, a
  model a company can run in its own country — describes what is in them.
  It describes; it does not judge, and its words are cited as what another
  model saw, not as proof.

## How a fact gets checked

An analyst investigates with its tools, then writes its report with
structured output: findings made of facts, a hypothesis and innocent
alternatives. Every value a fact relies on is also stated as a claim — record
id, field, value. The fact checker, plain code, looks each claim up in what
the tools actually returned in this run. Problems go back to the model as a
list; what still fails after two rounds is removed and shown separately.

A real example from development: the model wrote that an earlier check-in
was "also 6.6 km from the shop". 6.6 km was the distance *between* the two
check-ins. The checker rejects that claim — the earlier event has no
distance-to-site field — and the corrected report says the earlier check-in
was inside the geofence. That case is now a test
([`tests/test_checker.py`](tests/test_checker.py)).

## The skeptic and the actions

The skeptic gets only the verified facts and the NAZAR records behind them —
not the analyst's hypothesis, title or confidence — and runs on a different
model. Its job is to protect people from being wrongly suspected: find the
innocent explanation that fits the facts best and give a verdict. The final
confidence comes from fixed rules in code: if the skeptic finds an innocent
explanation likely, the card is low confidence whatever the analyst thought;
without a skeptic's opinion it never goes above medium.

A recommendation writer then picks at most two actions per card from a fixed
catalog. Code validates each one: typed parameters, only records a tool
actually returned, policy (recording a confirmed violation or not paying a
day needs high confidence *and* a skeptic who agrees), and for anything that
changes pay, NAZAR's own preview of the day before and after. Actions in the
employee's favour — counting a shift from the real arrival — are encouraged.

## Applying an action

Every valid action is stored as a pending proposal. Nothing reaches NAZAR
until a person presses "Apply" (and, for anything that changes pay, confirms
once more). The executor is plain code and the only part that writes:

- it claims the proposal atomically, so a double click cannot run it twice;
- it checks the policy again and asks NAZAR for the day once more — if the
  day changed since the person saw "before → after", it refuses;
- the reason written into NAZAR names the person who confirmed;
- "Undo" uses NAZAR's own cancellation (an adjustment is deactivated, its
  history stays); "Disagree" requires a reason and keeps it as feedback.

Every step — proposed, executed, declined with the reason, refused, failed,
undone — is in an append-only audit trail.

Verified end to end on Oct 27 against a local NAZAR: the agent proposed
counting a shift from the real arrival (09:06) instead of a duplicate
check-in (10:14); NAZAR previewed late 74 → 0 min, paid 455 → 522 min;
applied — NAZAR showed the corrected day with the confirming person in the
reason; undone — NAZAR showed the day as before.

## A wrong schedule is not a people problem

A crew that starts at 09:00 on a site set to 08:00 is "late" every day. The
timesheet analyst reads NAZAR's own setup check, then compares each site's
schedule with the real working day — the typical first check-in and last
check-out, counted by code over every check-in of the period — and reports
the site, not the people. The action it proposes is `change_schedule`: the
site's own shift hours, with NAZAR's schedule before and after. NAZAR keeps
schedule versions, so the change applies from today and past days stay as
they were counted.

A finding says what is in question: how *people* worked, or how NAZAR is
*set up*. The skeptic judges the two differently. For people, it looks for
the innocent explanation; for a setting, a wrong schedule *is* the innocent
explanation, so it judges whether the pattern is steady enough to change the
setting. The evals found this: before the split, the skeptic answered "the
people are fine" — which agreed with the finding — and the rules in code
turned that into low confidence and blocked the fix.

## "Disagree" is data

Declining a proposal needs a reason, and the reason is kept. The next
analysis sees the owner's recent reasons, the skeptic sees the ones about the
same person or site as the owner's word (a possible innocent explanation, not
a fact), and the card shows "you disagreed before and explained: …" — so the
team does not bring the same thing back as if it were new. The window shows
how many proposals the owner applied out of those decided; that share is the
quality measure for a real company.

## Importing a list of employees

A new site starts with a list the foreman already has — often a photo of a
notebook. Muse Spark reads it (vision for a photo; an Excel or CSV becomes
text) and only reports what is written, marking what it could not read.
Code then checks every row against NAZAR and highlights only what needs a
decision: illegible, one word only, looks like an existing employee, twice
in the list, site not found — in Latin or Cyrillic letters alike, since
owners write names both ways ("Норматов Азамат" finds "Azamat Normatov"). The person fixes those rows and confirms.

NAZAR creates an employee only with a face photo, and a model must not try to
match faces to names. So confirmed people wait for a photo; the foreman walks
along the brigade with a phone — "Next: Ivanov Alisher" → photo — and each
one becomes a NAZAR employee: NAZAR's own photo check, the employee, then the
assignment to the site, keeping everyone already assigned there.

Verified on Oct 27 against a local NAZAR: a handwritten-style notebook photo
came back as six rows with an existing employee, a surname-only row and a
duplicate flagged; a photo without a face was refused in NAZAR's own words;
NAZAR's seat limit was reported as is; with a face photo the employee was
created and added to the site, the site's other assignments unchanged.

## Setting a site up through the chat

"We open the Olmazor site in Chilanzar, here is the point (a map link), shift
8 to 5, six days, Rustam's crew: Rustam, Bobur Karimov and the new one, Jasur
Toirov." Nothing is created at once. The team turns the request into cards
the owner keeps, changes or skips, and a plan of changes in words:

1. **Site** — a new one with its name, address and point (clicked on the map,
   "I am here", a pasted Yandex/Google/2GIS link or two numbers), or one the
   company has.
2. **Shift** — hours and days, with the next dates on that schedule (NAZAR's
   own preview for a site it has).
3. **Group** — new or existing; on an existing site it gets its own shift.
4. **People** — each name found in NAZAR by code; two close names are asked
   back; a name NAZAR does not have waits for a face photo, as in the import.

The model does one thing: one short structured call reads the fields out of
the owner's words. Code does the rest ([`nazar_agent/setup/`](nazar_agent/setup)):

- **The point** ([`links.py`](nazar_agent/setup/links.py)): Google writes
  latitude first, Yandex and 2GIS longitude first; a pin wins over the map's
  centre. A short link is opened redirect by redirect, only over HTTPS and
  only to Google's and Yandex's map hosts — no address someone made up is
  fetched.
- **Clashes** ([`overlaps.py`](nazar_agent/setup/overlaps.py)): a person who
  already works elsewhere at the same hours — their own shift there, their
  group's, or the site's — is found before anything is created, night shifts
  and Sunday-into-Monday included, with ways out that clash with nothing
  ("move the shift to 18:00–03:00", "leave them out").
- **What is missing** is asked, never guessed: a point, a shift, which of two
  Karimovs.
- **Each step is a sentence written by code** from the parameters NAZAR will
  get, in Russian, Uzbek or English, reading the same before and after it is
  done: "Create the location “Olmazor City”: point 41.28560, 69.20340, radius
  100 m; shift 08:00–17:00, Mon–Sat."
- **The next message changes the same draft**: "make it 9 to 18, not Bobur".
- **Apply all** rebuilds the plan from what NAZAR says at that moment, runs
  the steps in order with the rights of whoever presses it, writes each to
  the audit trail and keeps what its undo needs; the first step that fails
  stops it. **Undo all** takes the steps back, last first, once — and says
  what it cannot undo. Moving someone off another site is not offered yet:
  NAZAR has no clean undo for it.

The demo NAZAR learned the routes this needs (`POST/DELETE /objects`, groups
at a site, `/departments`, the schedule preview), recorded from a real NAZAR
on a site and a group made for it and deleted again; the contract tests keep
the demo honest.

```bash
python -m nazar_agent.evals.setup    # seven conversations on a fresh demo company each
```

| | Case | Checked by code |
|---|---|---|
| S01 | a new site from a Yandex link, a crew, a newcomer | the site, its point, the shift, the group; two found, one waits for a photo; six steps; both clashes found; applied → the demo holds it → undone → as before |
| S02 | the same, then "make it 9 to 18, not Bobur" | the shift changed, Bobur left out, the site and point kept |
| S03 | in English, a point as two numbers in a sentence | the point, the steps in English, a clash; applied and undone |
| S04 | in Uzbek, a second-shift group on a site the company has | the existing site, a group with its own 14:00–22:00 shift; applied and undone |
| S05 | a new site with no point and no shift | asks for both; nothing to apply |
| S06 | "put Karimov on Sergeli" | the site found; which of the two Karimovs is asked |
| S07 | "delete the Sergeli site" | refused as unsupported; no setup |
| | **All** | **7/7, 38/38 checks, $0.008 for the whole run**; 20–80 s per message |

The first run found a real gap: coordinates written inside a sentence were
not read (only a message that was nothing but two numbers). Fixed, and the
run above is the second.

## Meta models

| Role | Model |
|---|---|
| Dispatcher, analysts, recommendation writer | Muse Spark 1.3 — tool calling, structured output, vision |
| Skeptic, nightly scan, anything with photos or personal data | Muse Spark Open (open weights) — on Baseten here, self-hosted later |
| Voice messages (Russian, Uzbek) | Muse Spark 1.2 audio input (Muse Voice Transcribe does not cover Russian or Uzbek) |
| Faces of the demo companies' employees | Muse Image 1.0, web and image search off |

Development and evals run on `muse-spark-1.3-contributor` against synthetic
demo companies: the same model at a fraction of the price, in exchange for
Meta training on that traffic. Real companies' data goes only to the
standard model on a paid balance (while an account spends free credits, Meta
may use the content for product improvement too) or to self-hosted open
weights.

**How long each role thinks.** Most of an answer's time was the models
thinking: an analyst wrote 12–15 thousand tokens for one question, the
dispatcher 1.6 thousand to sort a message. Muse Spark takes a reasoning
effort per request, and each role now has its own (`REASONING_EFFORT`):
the dispatcher at `minimal` reads a message in 2–4 s instead of 7–9 with the
same reading; the analysts, the writer, the photo inspector and the list
reader at `low`. On 24 heavy questions of the bank that halved the median
time (164 s → 72 s) and the tokens, with the same questions passing; the
dead-souls questions passed 10 of 10. The skeptic keeps the model's own
judgement — it is the second opinion.

Every demo employee is a fictional person whose portrait Muse Image drew for
that name, age and look ([`scripts/generate_demo_faces.py`](scripts/generate_demo_faces.py));
the prompt, model, date and hash of each photo are kept with it. A face
service has a harder test than "looks real": NAZAR's own recognition must
tell the people apart. The first set, described by hair and clothes only,
failed it — the model drew one typical face per age and gender, and
different people scored up to 0.71 against a match threshold of 0.45.
Describing each face's structure (shape, nose, eyes, skin) and spreading it
across a company brought every pair of different people in a company below
the threshold. All four rounds together: 156 candidates, $1.56.

## Evals

The construction demo company is seeded with known answers
([`nazar_agent/evals/cases.py`](nazar_agent/evals/cases.py)), and plain code
scores every run against them
([`score.py`](nazar_agent/evals/score.py)) — no model grades another model.
A dead soul is *found* when a card of medium or high confidence cites them;
a person with an innocent explanation is *falsely accused* when such a card
is about them, or an action would cut their pay.

```bash
python -m nazar_agent.evals --runs 5          # on the demo NAZAR in this process: only MODEL_API_KEY
python -m nazar_agent.evals --nazar --runs 5  # against the NAZAR in .env, as the demo company's owner
```

### Any question an owner may ask

The three cases above check depth. The question bank checks breadth: 72
questions an owner may really ask, good and bad, in Russian, Uzbek (Latin
and Cyrillic) and English
([`nazar_agent/evals/questions.py`](nazar_agent/evals/questions.py)). All of
them are asked of the demo company stopped at one moment, a Wednesday at
11:30, and every answer is checked in plain code against what is true there
([`truth.py`](nazar_agent/evals/truth.py)): the names said, the hours, the
period read, the language, a refusal without reading NAZAR, no invented
money, no false accusation, no forbidden action.

```bash
python -m nazar_agent.evals.bank                 # all 72; --intent I J or --id A03 for some
python -m nazar_agent.evals.bank --rescore DIR   # check saved answers again, ask nothing
```

| | Kind of question | Before | After |
|---|---|---|---|
| A | now and today: who is late, absent, on site | 4/6 | 6/6 |
| B | facts over a period: hours, who is late most | 5/6 | 6/6 |
| C | what to check | 3/5 | 4/5 |
| D | a person or a site, typos, two Karimovs | 6/7 | 7/7 |
| E | an action: supported, or not | 3/6 | 5/6 |
| F | explain: why late, how lateness is counted | 4/5 | 5/5 |
| G | dates: yesterday, Saturday, October, the future, half a year | 4/6 | 6/6 |
| H | unclear, a follow-up | 3/4 | 4/4 |
| I | off-topic | 0/5 | 5/5 |
| J | forbidden: fine everyone, show the prompt, another company | 6/7 | 7/7 |
| K | language | 2/5 | 5/5 |
| L | no data | 3/3 | 3/3 |
| M | compare, rank | 3/4 | 4/4 |
| N | money | 2/3 | 3/3 |
| | **All** | **48/72** | **70/72** |
| | Average cost per question | $0.062 | $0.049 |

*Before* is the first run, Oct 29 morning; three checks stricter than the
plan were loosened after it, which would have made it 51. *After* is the full
run on the final code, rescored once: the scorer did not yet know that the
new period tallies name absent days, and counted a card about two real
absences as an accusation. The two failures left: in C04 a card asked, at
medium confidence, about an ordinary worker's two real late arrivals before
payday — the scorer counts that as an accusation, and a rerun did not repeat
it; in E05 the owner asked for a 09:00–19:00 shift and the writer proposed
19:03, the site's real last check-out — the writer now sees the owner's
question, and that rerun passed too.

What moved the numbers:
- **A dispatcher that reads the request.** One structured call gives the
  kind of request, the language, the period and the names; code checks the
  period against today (no future, at most 31 days). A greeting, an
  off-topic question, a forbidden one or the future gets a fixed answer in
  the owner's language in about 15 seconds, without the analyst or NAZAR.
- **Counting by code.** Asked who was late today, the analyst read NAZAR's
  timesheet, where a shift still open has no lateness yet, and answered
  "nobody" while a crew had come an hour late. `get_today` counts today from
  the check-ins; `count_period` gives totals per company, site and day with
  names; `find_people_and_sites` finds a name written with a typo or in the
  other alphabet.
- **A direct answer, checked.** The report names who, when and how many,
  fact by fact, and the fact checker checks those facts like a finding's;
  every number in the summary must be one a tool returned, or its sentence
  is cut.
- **Asking back.** A name that fits two people is asked back with a button
  for each, before anyone investigates; a name no one has is answered at once.

The price of depth is time. With each role's reasoning effort set (see Meta
models), the Oct 30 run of all 72 questions, four asked at once, took a
median of 66 s for a question the analysts investigate (it was 140 s) and
4 s for a greeting, an off-topic or a forbidden one (it was 11–14 s); 70/72
passed. The two misses — a question about check-ins sent to the timesheet
analyst, a card about someone late twice in a week — got a rule each, and
those questions with their neighbours then passed 16 of 16.

Oct 29, the demo NAZAR (`nazar_agent/demo`), final code:

| Case | Runs | Passed | Found | Falsely accused | Fact checker clean first time | Actions valid | Avg cost |
|---|---|---|---|---|---|---|---|
| 4 «dead souls» checked in from the foreman's phone | 5 | 5/5 | 20/20 people | 0 in 5 runs | 0/5 | 16/16 | $0.032 |
| Wrong site schedule (08:00 instead of 09:00) | 5 | 5/5 | schedule 5/5, fix proposed 5/5 | 0 in 5 runs | 0/5 | 5/5 | $0.023 |
| New crew list with a duplicate, a repeat and a surname only | 5 | 5/5 | 15/15 flags, 0 false | — | — | — | — |

The series before it, on the same demo, passed 13 of 15 and showed two
weaknesses of the agent, not of the demo — the real NAZAR answers the same
way. In one dead-souls run the analyst asked to check the weak-GPS worker's
check-in outside the fence at medium confidence: the trap worked once. In
one wrong-schedule run an ordinary worker's single absence was cited only as
"1 absent day" in his totals: two weeks of the timesheet have more unusual
days than the analyst is shown (150), mostly leaving a few minutes early, and
the absence was cut off. The list is now cut by weight — absences first,
minutes of early leave last — and that run's mistake did not come back.

Oct 28, local NAZAR with the construction demo:

| Case | Runs | Passed | Found | Falsely accused | Fact checker clean first time | Actions valid | Avg cost |
|---|---|---|---|---|---|---|---|
| 4 «dead souls» checked in from the foreman's phone | 15 | 9/15 | 36/36 people | 0 in 9 runs | 4/9 | 19/19 | $0.032 |
| Wrong site schedule (08:00 instead of 09:00) | 5 | 5/5 | schedule 5/5, fix proposed 5/5 | 0 in 5 runs | 1/5 | 5/5 | $0.024 |
| New crew list with a duplicate, a repeat and a surname only | 5 | 5/5 | 15/15 flags, 0 false | — | — | — | — |

Six of the fifteen dead-souls runs did not finish: the model API timed out
or the connection dropped (a network outage on the development machine
during the series). They count as failed; every run that finished found all
four and accused no one. An earlier series on the previous code passed 9 of
10: in the tenth the photo inspector had seen four photos of a screen, and
the report came back as a summary "Placeholder" with no findings. A report
with no findings after an investigation is now asked for once more.

The wrong-schedule case first failed every run: the analyst found the site,
but there was no action to fix a schedule. With `change_schedule` added it
still failed, now because of the skeptic (see above). The list case failed
4 of 5 runs until matching a site ignored the word a foreman writes before
its name ("Объект Чиланзар"). Real, random absences
in the demo data are not counted as false accusations: asking about an
unexcused absence before paying is right.

## Public demo

Two ways in. **Without NAZAR**: the demo above runs as a container next to
NAZAR ([`deploy/docker-compose.demo.yml`](deploy/docker-compose.demo.yml)) at
its own address; every browser asks on its own, visitors share the demo
company, and it starts over every day. **Through NAZAR**: everyone who tries
it signs in to the same NAZAR demo company and opens the assistant from its
cabinet. Each window session may ask a limited
number of questions, and all sessions together have a daily budget in
dollars (`DEMO_QUESTIONS_PER_SESSION`, `DEMO_BUDGET_USD_PER_DAY`); the
window says so when a limit is reached. A question is counted before any
model runs and its cost is added even if it fails; a model without a known
price is counted at a high one. Signing in again gives a new session, so the
per-session limit only paces a visitor — the daily budget is what protects
the credits. What a visitor types when disagreeing reaches the models only
as a short quoted line, marked as data. Every night the demo company is
seeded again ([`deploy/reset-demo.sh`](deploy/reset-demo.sh)), so what one
visitor applied is gone by morning. Deployment next to NAZAR:
[`deploy/`](deploy/README.md).

## Status

| Part | State |
|---|---|
| Agent engine: roles, tool calling loop, step budget, trace | done |
| NAZAR API client: session, refresh, error envelope | done |
| Attendance analyst with read tools | done |
| Shared phones counted by code; photo inspector on Muse Spark Open | done (live on the construction demo: the four "dead souls" found, high confidence, the honest foreman and the weak-GPS worker not accused) |
| Timesheet analyst: reads NAZAR's timesheet and setup check, never recalculates | done |
| Structured findings (structured output) and the fact checker with correction rounds | done |
| Skeptic on a separate model; confidence merged by fixed rules in code | done (Muse Spark Open on Baseten; it always reasons, so it gets a large output budget) |
| Recommendation writer, fixed action catalog, validation with policy in code and NAZAR's own before → after | done |
| Executor after human confirmation: atomic claim, policy re-check, refuses if the day changed since it was shown; audit trail; undo | done |
| Assistant window: ask, cards, apply / disagree / postpone / undo, audit trail | done (opened from the NAZAR cabinet with a one-minute pass; acts with that person's rights) |
| Dispatcher reads the request: kind, language, period, names; short answers without the analyst; a name asked back with buttons | done |
| Direct answers checked like findings; today, period totals and names counted by code; failures said in plain words | done |
| Short answer first: what the team thinks, what it will do if the owner agrees — each action said by code from its own parameters (names, dates, hours, pay before → after) in Russian, English or Uzbek; the reasoning folded under "Подробнее", in the bot under an expandable quote | done |
| The window in Russian, Uzbek or English, with NAZAR's own terms; light by default in NAZAR's colours, dark at a click; failures said in the window's language, which also answers a question with no clear language of its own | done |
| Telegram bot: text and voice questions, cards with buttons, a reason to disagree, undo; allowlisted chats | done |
| Voice: Telegram Ogg/Opus → WAV → Muse Spark 1.2 audio input (Russian verified; Uzbek still to verify) | done |
| Setting a site up through the chat: cards (site with a map, "I am here" or a map link; shift; group; people), clashes counted by code, the plan of changes in words in three languages, Apply all / Undo all with the audit trail; the demo NAZAR answers the routes it needs | done (7/7 setup cases; a move to another site is not offered yet) |
| A conversation, in the window and in a Telegram chat: "А вчера?" after "Кто сегодня опоздал?" is read with the turn before it — the dispatcher writes it out in full ("I took the question as: …"), the analyst gets that and the previous answer; earlier answers stay below, folded, their buttons live; "New conversation" starts over | done |
| Voice and photos in the window: a microphone (the same transcription as the bot, the text put in the box to read before sending; no speech heard is said so, not typed in); one photo zone everywhere — drop, paste, choose, or take with the webcam or the phone's camera | done (Russian speech heard right; English from a synthetic voice comes back garbled — Muse Spark 1.2's limit, Muse Spark 1.3 takes no audio) |
| Morning brief at a set local time and on `/brief` | done |
| Importing a list of employees: photo of a notebook / Excel / CSV / text → checked table → waiting for a photo → shooting queue → employee in NAZAR | done |
| Demo employees' faces generated with Muse Image, checked against NAZAR's face recognition | done |
| Wrong schedule: each site's schedule vs. the real day, counted by code; `change_schedule` with before → after; findings about people or setup | done |
| "Disagree" reasons fed to the next analysis, the skeptic and the card; share of applied proposals in the window | done |
| Evals over the planted cases and a bank of 72 owner questions, scored by code | done (bank: 48/72 → 70/72) |
| Every model call recorded: role, model, tokens, price (an estimate where no price is published); spend today, this week and in all in the window and `/usage` in the bot | done |
| Public demo: limits per session and per day, nightly reset, container next to NAZAR, always a working morning | done |
| The window explains itself: what the product does on the first screen, "Try these" in one click, import samples, a Company tab that marks what Apply changed | done |
| Demo NAZAR in the repository: the demo company in memory, every route checked against recorded real answers, `DEMO_MODE`, one-command start | done |

## Run it against NAZAR

The demo needs nothing but a model key (see the top). Against a real NAZAR it
requires Python 3.11+ and access to a NAZAR instance with a demo company.
Use a demo company and a dedicated admin user for the agent — never a real
client's account.

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows; on Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env          # fill in MODEL_API_KEY and the NAZAR account
python -m nazar_agent --trace "Who did not show up yesterday?"
python -m nazar_agent.service   # the assistant window at http://127.0.0.1:8100
```

With `TELEGRAM_BOT_TOKEN` set, the same process runs the Telegram bot (long
polling, no public URL needed) and, with `BRIEF_TIME`, the morning brief.
The window and the bot share proposals and the audit trail.

Tests do not need network access or keys:

```bash
pytest
```
