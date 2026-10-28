# Deploying the agent next to NAZAR

The agent runs as one container on the server that runs NAZAR, on NAZAR's
Docker network. People reach it at `https://agent.nazarhr.uz`, opened from
the NAZAR cabinet with a one-minute pass; there is no other way in
(`REQUIRE_SIGN_IN=true`).

## 1. NAZAR

NAZAR needs the changes in [`nazar-changes/`](../nazar-changes/) (migration
0094 included) and two settings in its `.env.prod`:

```sh
AI_AGENT_URL=https://agent.nazarhr.uz
AI_AGENT_KEY=<at least 32 random characters, the same as NAZAR_AGENT_KEY below>
```

Copy the demo faces into NAZAR's read-only models folder, so the seeds can
read them inside the backend container:

```sh
cp -r docs/investment/demo-faces /opt/faceid-startup-mvp/models/demo-faces
```

Seed the construction demo once (its AI switch is on by the seed itself).
The password is typed, not put on the command line, so it stays out of the
shell history and the process list:

```sh
cd /opt/faceid-startup-mvp
read -r DEMO_EMAIL && read -rs DEMO_PASSWORD && export DEMO_EMAIL DEMO_PASSWORD
docker compose -f docker-compose.prod.yml --env-file .env.prod exec -T -e DEMO_EMAIL -e DEMO_PASSWORD backend \
  sh -c 'python scripts/seed_construction_demo.py --email "$DEMO_EMAIL" --password "$DEMO_PASSWORD" --photos-dir /app/models/demo-faces'
```

## 2. The agent

```sh
git clone https://github.com/aminredfield/nazar-agent.git /opt/nazar-agent
cd /opt/nazar-agent/deploy
cp ../.env.example .env      # fill in MODEL_API_KEY, the skeptic, NAZAR_AGENT_KEY,
                             # DEMO_QUESTIONS_PER_SESSION, DEMO_BUDGET_USD_PER_DAY;
                             # NAZAR_EMAIL/NAZAR_PASSWORD only for the Telegram bot
docker compose -p nazar-agent up -d --build
```

Add [`Caddyfile.agent`](Caddyfile.agent) to the server's Caddyfile and reload
the proxy. The DNS record `agent.nazarhr.uz` must point to the server first.

## 3. Nightly reset

Create `deploy/demo.env` with `DEMO_EMAIL` and `DEMO_PASSWORD` (mode 600) and
add [`reset-demo.sh`](reset-demo.sh) to cron.

## 4. The public demo (no NAZAR account behind it)

The same agent on the demo company built in its own container, for judges
and visitors who have no NAZAR account. It reads the model keys from the
same `deploy/.env`; the Telegram bot is off in it.

```sh
cd /opt/nazar-agent/deploy
docker compose -p nazar-agent-demo -f docker-compose.demo.yml up -d --build
```

The DNS record `demo-agent.nazarhr.uz` must point to the server; its block
is in [`Caddyfile.agent`](Caddyfile.agent). Limits: `DEMO_QUESTIONS_PER_SESSION_PUBLIC`
(default 8 per browser) and `DEMO_BUDGET_USD_PER_DAY_PUBLIC` (default $5 for
all visitors together). No cron is needed: the demo company starts over every
day by itself.

## 5. Check

- the cabinet of the demo company shows "Assistant"; it opens the window;
- a question gets cards; "Apply" changes NAZAR and "Undo" puts it back;
- after the limit, the window says so instead of answering;
- the bot answers in Telegram from an allowed chat (if `api.telegram.org`
  is blocked from the server, set `TELEGRAM_PROXY`).
