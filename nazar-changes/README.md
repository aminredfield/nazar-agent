# Changes made to NAZAR during the hackathon

NAZAR itself is closed source and existed before the hackathon. To connect the
agent, a few small changes are needed on the NAZAR side. Every such change
made between Oct 26 and Nov 4, 2026 is published here as a diff, so judges can
see exactly what was added to the existing product.

| # | Change | Why | Diff |
|---|---|---|---|
| 0001 | NAZAR trusts requests marked `X-Request-Origin: nazar_agent`, as it already trusts its mobile app | NAZAR's CSRF guard rejects state-changing requests that carry a session cookie but no `Origin`. That guard protects browsers; the agent is a server-side client and sends no `Origin`. A browser cannot set this custom header cross-site without a CORS preflight, which NAZAR grants only to its own origins. | [0001-trust-agent-client-marker.diff](0001-trust-agent-client-marker.diff) |

Demo data, not code: the local demo company runs on the Business plan,
because the Standard plan excludes timesheet corrections and event reviews —
the two things the agent proposes.

Planned:

- feature `ai_agent`, switched on per company through the existing plan
  feature overrides;
- an "Assistant" menu entry in the admin cabinet, visible only with the feature on;
- a service admin user for the agent and sign-in by a short-lived token
  (password sign-in is behind Cloudflare Turnstile in production);
- the device of a check-in (`browser_hash`) in the event detail API, which the
  attendance analyst needs to see a phone shared by several people.
