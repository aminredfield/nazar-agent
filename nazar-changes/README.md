# Changes made to NAZAR during the hackathon

NAZAR itself is closed source and existed before the hackathon; its
repository is private. To connect the agent, a few small changes are needed
on the NAZAR side. Every such change made between Oct 26 and Nov 4, 2026 is
published here as the exact diff applied to NAZAR, so judges can see
everything that was added to the existing product. The date of each change
is the date it was committed to this public repository.

| # | Change | Why | Diff |
|---|---|---|---|
| 0001 | NAZAR trusts requests marked `X-Request-Origin: nazar_agent`, as it already trusts its mobile app. Applied Oct 27 | NAZAR's CSRF guard rejects state-changing requests that carry a session cookie but no `Origin`. That guard protects browsers; the agent is a server-side client and sends no `Origin`. A browser cannot set this custom header cross-site without a CORS preflight, which NAZAR grants only to its own origins. | [0001-trust-agent-client-marker.diff](0001-trust-agent-client-marker.diff) |
| 0002 | The AI assistant is switched on per company by the platform admin, separately from the plan (`company_settings.ai_agent_enabled`, migration 0094, a switch in the superadmin billing card). The cabinet gets an "Assistant" entry; it asks NAZAR for a one-minute pass (`POST /ai-agent/handoff`) and opens the agent window with it in the URL fragment; the agent service exchanges it, with the shared `AI_AGENT_KEY`, for that person's own NAZAR session (`POST /ai-agent/session`). | The assistant acts with the rights of the person who opened it — never a shared service account — and NAZAR's audit names that person. A pass alone is not a session; switched off, no pass and no exchange. | [0002-ai-agent-flag-and-sign-in.diff](0002-ai-agent-flag-and-sign-in.diff) |
| 0003 | Check-in events in the list and detail API carry `device_key`: the first 16 characters of the phone's browser hash from the existing device binding, or `null` without a binding. | The attendance analyst needs to see one phone used by several people. A short key is enough to compare check-ins and does not hand out the device fingerprint itself. | [0003-device-key-on-check-ins.diff](0003-device-key-on-check-ins.diff) |
| 0004 | The device key leaves NAZAR only for a company with the AI assistant switched on. A second demo company, a construction firm (`scripts/seed_construction_demo.py`): three sites, three weeks, the assistant on; check-in photos drawn from the AI-generated demo faces (`scripts/demo_photos.py`). | Everything new in NAZAR stays behind the per-company AI switch. The demo company holds the cases the agents are evaluated on: four workers checked in by their foreman after they left, an honest foreman checking in workers without smartphones, a worker with weak GPS, a crew whose site schedule is wrong. | [0004-device-key-behind-the-flag-and-construction-demo.diff](0004-device-key-behind-the-flag-and-construction-demo.diff) |

Demo data, not code: the demo company runs on the "Management" plan
(`business`). Timesheet corrections are on every plan, but recording the
review of a check-in is not on the entry plan.
