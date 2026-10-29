"""One company's setting up: the drafts of its conversations, its applied plans, and what the window asks of them.

The assistant reads a setup request into the conversation's draft; the
window shows that draft with its plan, changes it card by card, applies it
and undoes it — all through here, so a plan is always rebuilt from what
NAZAR says at that moment.
"""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

from nazar_agent.actions import ProposalStore
from nazar_agent.nazar import NazarClient
from nazar_agent.onboarding import DraftStore
from nazar_agent.setup.apply import Run, SetupRefused, SetupStore, apply, undo
from nazar_agent.setup.draft import Drafts
from nazar_agent.setup.edits import EditError, SetupEdit, edit
from nazar_agent.setup.links import LinkError, resolve
from nazar_agent.setup.plan import build
from nazar_agent.setup.view import setup_view
from nazar_agent.setup.wording import step_text, text
from nazar_agent.tools.common import call_nazar


async def company(nazar: NazarClient) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Sites, employees and groups, as a setup is checked against them."""
    sites, employees, groups = await asyncio.gather(
        call_nazar(nazar.get("/objects", {"include_inactive": False})),
        call_nazar(nazar.get("/employees")),
        call_nazar(nazar.get("/departments")),
    )
    return sites, employees, groups


def run_view(run: Run) -> dict[str, Any]:
    done = {index: item for index, item in enumerate(run.done)}
    return {
        "id": run.id,
        "status": run.status,
        "steps": [
            {"text": step["text"], "done": index in done, "undone": bool(done.get(index, {}).get("undone"))}
            for index, step in enumerate(run.steps)
        ],
        "error": run.error,
    }


class SetupDesk:
    def __init__(self, audit: ProposalStore, people: DraftStore) -> None:
        self.drafts = Drafts()
        self.runs = SetupStore(audit.path)
        self._audit = audit
        self._people = people
        self._last: dict[str, str] = {}  # conversation → its last applied plan

    def clear(self) -> None:
        self.drafts.clear()
        self._last.clear()

    async def view(self, key: str, nazar: NazarClient, today: date) -> dict[str, Any]:
        draft = self.drafts.get(key)
        shown = None
        if draft is not None:
            shown = setup_view(draft, await build(draft, nazar, today), draft.lang, await company(nazar))
        last = self._last.get(key)
        return {"setup": shown, "run": run_view(self.runs.get(last)) if last else None}

    async def change(self, key: str, change: SetupEdit, nazar: NazarClient, today: date) -> dict[str, Any]:
        draft = self._draft(key)
        sites, employees, groups = await company(nazar)
        self.drafts.put(key, edit(draft, change, employees=employees, sites=sites, groups=groups))
        return await self.view(key, nazar, today)

    async def point(self, key: str, draft_id: str, link: str, nazar: NazarClient, today: date) -> dict[str, Any]:
        """A map link pasted on the site's card: its point, or why there is none, in the draft's language."""
        draft = self._draft(key)
        try:
            found = await resolve(link)
        except LinkError as exc:
            raise EditError(text(f"link_{exc.kind}", draft.lang)) from exc
        change = SetupEdit.model_validate({
            "draft_id": draft_id, "site": {"latitude": found.latitude, "longitude": found.longitude, "point_source": found.source},
        })
        return await self.change(key, change, nazar, today)

    async def apply(self, key: str, draft_id: str, nazar: NazarClient, actor: str, today: date) -> dict[str, Any]:
        draft = self._draft(key)
        if draft.id != draft_id:
            raise EditError("The draft changed; open it again.")
        plan = await build(draft, nazar, today)  # what NAZAR says now, not when the plan was shown
        if not plan.ready:
            raise SetupRefused("The plan is not complete yet.")
        texts = [step_text(step, plan, draft.lang) for step in plan.steps]
        run = await apply(
            list(plan.steps), texts, draft_id=draft.id, nazar=nazar, store=self.runs, audit=self._audit,
            drafts=self._people, actor=actor, today=today,
        )
        self._last[key] = run.id
        if run.status == "applied":
            self.drafts.drop(key)
        shown = None if run.status == "applied" else setup_view(draft, plan, draft.lang, await company(nazar))
        return {"setup": shown, "run": run_view(run)}

    async def undo(self, key: str, run_id: str, nazar: NazarClient, actor: str) -> dict[str, Any]:
        if self._last.get(key) != run_id:
            raise SetupRefused("This plan was not applied from this window.")
        run = await undo(run_id, nazar=nazar, store=self.runs, audit=self._audit, drafts=self._people, actor=actor)
        return {"setup": None, "run": run_view(run)}

    def discard(self, key: str) -> None:
        self.drafts.drop(key)

    def _draft(self, key: str):
        draft = self.drafts.get(key)
        if draft is None:
            raise EditError("There is no setup being made; ask in the chat first.")
        return draft
