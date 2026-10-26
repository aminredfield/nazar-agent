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

## Meta models

| Role | Model |
|---|---|
| Dispatcher, analysts, recommendation writer | Muse Spark 1.3 — tool calling, structured output, vision |
| Skeptic, nightly scan, anything with photos or personal data | Muse Spark Open — self-hosted or a hosted provider |
| Voice messages (Russian, Uzbek) | Muse Spark 1.2 audio input |

## Status

| Part | State |
|---|---|
| Agent engine: roles, tool calling loop, step budget, trace | done |
| NAZAR API client: session, refresh, error envelope | done |
| Attendance analyst with read tools | done |
| Structured findings and the fact checker | planned |
| Skeptic, recommendations, action catalog, executor, audit | planned |
| Assistant window, Telegram bot, voice | planned |
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
```

Tests do not need network access or keys:

```bash
pytest
```
