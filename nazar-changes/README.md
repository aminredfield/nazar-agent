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

Demo data, not code: the demo company runs on the "Management" plan
(`business`). Timesheet corrections are on every plan, but recording the
review of a check-in is not on the entry plan.

Planned:

- feature `ai_agent`, switched on per company through the existing plan
  feature overrides;
- an "Assistant" menu entry in the admin cabinet, visible only with the feature on;
- a service admin user for the agent and sign-in by a short-lived token
  (password sign-in is behind Cloudflare Turnstile in production);
- the device of a check-in (`browser_hash`) in the event detail API, which the
  attendance analyst needs to see a phone shared by several people.
