# Changes made to NAZAR during the hackathon

NAZAR itself is closed source and existed before the hackathon. To connect the
agent, a few small changes are needed on the NAZAR side. Every such change
made between Oct 26 and Nov 4, 2026 is published here as a diff, so judges can
see exactly what was added to the existing product.

| Change | Why | Diff |
|---|---|---|
| — | No changes yet | — |

Planned:

- feature `ai_agent`, switched on per company through the existing plan
  feature overrides;
- an "Assistant" menu entry in the admin cabinet, visible only with the feature on;
- a service admin user for the agent and sign-in by a short-lived token;
- the device of a check-in (`browser_hash`) in the event detail API, which the
  attendance analyst needs to see a phone shared by several people.
