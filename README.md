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
| | Small changes in NAZAR to connect the agent (feature flag, menu entry, service user) — published as a diff in [`nazar-changes/`](nazar-changes/) |

The git history of this repository starts on Oct 27, 2026.

## Architecture

```
Input: Telegram (text, voice), assistant window, schedule (morning brief)
   │
   ▼
Dispatcher ─────────── understands the request, picks the analysts
   │
   ├─► Attendance analyst ─ check-ins, anti-fraud flags, devices, photos (vision)
   ├─► Timesheet analyst ── timesheet, schedules, absences, period readiness
   └─► Setup analyst ────── sites, geofences, employees, importing lists
   │       read-only, each with its own tools; output: findings = facts + hypothesis
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

## Meta models

| Role | Model |
|---|---|
| Dispatcher, analysts, recommendation writer | Muse Spark 1.3 — tool calling, structured output, vision |
| Skeptic, nightly scan, anything with photos or personal data | Muse Spark Open — self-hosted or a hosted provider |
| Voice messages (Russian, Uzbek) | Muse Spark 1.2 audio input (Muse Voice Transcribe does not cover Russian or Uzbek) |

Development and evals run on `muse-spark-1.3-contributor` against a synthetic
demo company with AI-generated faces: the same model at a fraction of the
price, in exchange for Meta training on that traffic. Real companies' data
goes only to the standard model or to self-hosted open weights.

## Status

| Part | State |
|---|---|
| Agent engine: roles, tool calling loop, step budget, trace | done |
| NAZAR API client: session, refresh, error envelope | done |
| Attendance analyst with read tools | done |
| Timesheet analyst: reads NAZAR's timesheet and setup check, never recalculates | done |
| Structured findings (structured output) and the fact checker with correction rounds | done |
| Skeptic on a separate model; confidence merged by fixed rules in code | done (Muse Spark 1.2 until Muse Spark Open on Baseten is available) |
| Recommendation writer, fixed action catalog, validation with policy in code and NAZAR's own before → after | done |
| Executor after human confirmation: atomic claim, policy re-check, refuses if the day changed since it was shown; audit trail; undo | done |
| Assistant window: ask, cards, apply / disagree / postpone / undo, audit trail | done (localhost; opening it from the NAZAR cabinet with a token is next) |
| Telegram bot, voice | planned |
| List import, evals, public demo account | planned |

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

Tests do not need network access or keys:

```bash
pytest
```
