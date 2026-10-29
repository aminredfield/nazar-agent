"""The draft and its plan as the window shows them: cards, steps in words, what is missing, clashes."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from nazar_agent.setup.draft import Draft
from nazar_agent.setup.plan import Plan
from nazar_agent.setup.wording import clash_text, missing_text, step_text, text


def setup_view(draft: Draft, plan: Plan, lang: str) -> dict[str, Any]:
    return {
        "draft": asdict(draft),
        "steps": [{"kind": step.kind, "text": step_text(step, plan, lang)} for step in plan.steps],
        "missing": [{"kind": kind, "about": about, "text": missing_text(kind, about, lang)} for kind, about in plan.missing],
        "clashes": [
            {
                "employee_id": item.person.employee_id, "name": item.person.name, "site_id": item.site_id,
                "text": clash_text(item, lang),
                "options": [{"kind": option.kind, "shift": asdict(option.shift) if option.shift else None} for option in item.options],
            }
            for item in plan.clashes
        ],
        "unknown_schedules": text("unknown_schedule", lang, names=", ".join(plan.unknown_schedules)) if plan.unknown_schedules else "",
        "link_problem": text(f"link_{draft.link_problem}", lang) if draft.link_problem else "",
        "preview": list(plan.preview),
        "ready": plan.ready,
    }


def summary(plan: Plan, lang: str) -> str:
    return text("summary_ready", lang, count=len(plan.steps)) if plan.ready else text("summary_missing", lang)
