# NAZAR Agent

A team of AI agents that reviews time-and-attendance data in
[NAZAR](https://nazarhr.uz) and brings the owner of a business **recommendations
with evidence**. Nothing changes without a human pressing the button.

Built for the Meta AI Developer Hackathon (Oct 26 – Nov 4, 2026), Startup
track, theme **Build for Your World**.

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
| NAZAR: check-in by face + geofence + anti-spoofing, schedules, timesheet calculation, admin API, demo company seeding — closed source | Everything in this repository: agent engine, roles, tools over the NAZAR API, recommendations, executor, audit, assistant UI, Telegram bot, list import, evals |
| | Small changes in NAZAR, all behind a per-company AI switch: the switch itself, the menu entry, sign-in with a one-minute pass, the device key of a check-in, the bot's sign-in with the assistant key, the AI spend per company in the superadmin; the construction demo company and new faces for both demo companies — published as diffs in [`nazar-changes/`](nazar-changes/). Everything else the agents do goes through NAZAR's existing API |

The git history of this repository starts on Oct 27, 2026.

## Architecture

```
Input: Telegram (text, voice), assistant window, schedule (morning brief)
   │
   ▼
Dispatcher ─────────── understands the request, picks the analysts
   │
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
decision: illegible, only a surname, looks like an existing employee, twice
in the list, site not found. The person fixes those rows and confirms.

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

Everyone who tries the demo signs in to the same NAZAR demo company and
opens the assistant from its cabinet. Each window session may ask a limited
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
| Dispatcher: picks the analyst for a question | done |
| Telegram bot: text and voice questions, cards with buttons, a reason to disagree, undo; allowlisted chats | done |
| Voice: Telegram Ogg/Opus → WAV → Muse Spark 1.2 audio input (Russian verified; Uzbek still to verify) | done |
| Morning brief at a set local time and on `/brief` | done |
| Importing a list of employees: photo of a notebook / Excel / CSV / text → checked table → waiting for a photo → shooting queue → employee in NAZAR | done |
| Demo employees' faces generated with Muse Image, checked against NAZAR's face recognition | done |
| Wrong schedule: each site's schedule vs. the real day, counted by code; `change_schedule` with before → after; findings about people or setup | done |
| "Disagree" reasons fed to the next analysis, the skeptic and the card; share of applied proposals in the window | done |
| Evals over the planted cases, scored by code | done |
| Every model call recorded: role, model, tokens, price (an estimate where no price is published); spend today, this week and in all in the window and `/usage` in the bot | done |
| Public demo: limits per session and per day, nightly reset, container next to NAZAR | done (deployment: in progress) |

## Run it

Requires Python 3.11+ and access to a NAZAR instance with a demo company.
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
