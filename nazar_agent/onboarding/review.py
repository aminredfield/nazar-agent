"""Checks on a read list, in code: what the person should look at before creating.

Only rows with something to decide are highlighted, each with the reason:

- not legible — the model was not sure it read the name; the line as written
  is shown next to it;
- only a surname — add the name or keep it as it is;
- looks like an existing employee — likely a duplicate;
- the same person twice in this list;
- site not found — pick an existing one; a new site needs its point on the
  map, which a person sets in NAZAR, not the agent.

Rows are matched to NAZAR's own sites and employees; nothing is guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from nazar_agent.onboarding.parse import ListedPerson

SAME_PERSON = 0.88  # name similarity to call two names one person
SAME_SITE = 0.6


@dataclass(frozen=True, slots=True)
class Site:
    id: str
    name: str
    address: str


@dataclass(frozen=True, slots=True)
class ImportRow:
    full_name: str
    as_written: str
    site_hint: str
    site_id: str | None
    issues: tuple[str, ...]


def review(people: list[ListedPerson], sites: list[Site], employees: list[dict[str, Any]]) -> list[ImportRow]:
    rows = []
    seen: list[str] = []
    for person in people:
        issues: list[str] = []
        name = " ".join(person.full_name.split())
        if not person.legible:
            issues.append(f"плохо читается: «{person.as_written}»" + (f" — {person.note}" if person.note else ""))
        if len(name.split()) == 1:
            issues.append("только фамилия — допишите имя или оставьте так")
        twin = next((employee for employee in employees if same_person(name, employee["full_name"])), None)
        if twin is not None:
            issues.append(f"похож на действующего сотрудника «{twin['full_name']}» — может быть дубль")
        if any(same_person(name, other) for other in seen):
            issues.append("встречается в списке дважды")
        seen.append(name)

        site_id = None
        if person.site.strip():
            site = match_site(person.site, sites)
            if site is None:
                issues.append(f"объект «{person.site}» не найден — выберите из списка")
            else:
                site_id = site.id
        rows.append(ImportRow(name, person.as_written, person.site, site_id, tuple(issues)))
    return rows


def same_person(first: str, second: str) -> bool:
    a, b = _normal(first), _normal(second)
    if not a or not b:
        return False
    # Word order differs between lists ("Иванов Алишер" / "Алишер Иванов").
    if sorted(a.split()) == sorted(b.split()):
        return True
    return SequenceMatcher(None, " ".join(sorted(a.split())), " ".join(sorted(b.split()))).ratio() >= SAME_PERSON


def match_site(hint: str, sites: list[Site]) -> Site | None:
    """The site a hint names, if exactly one fits well."""
    wanted = _normal(hint)
    scored = []
    for site in sites:
        name, address = _normal(site.name), _normal(site.address)
        if wanted and (wanted in name or name in wanted or (address and wanted in address)):
            score = 1.0
        else:
            score = max(SequenceMatcher(None, wanted, name).ratio(), SequenceMatcher(None, wanted, address).ratio())
        scored.append((score, site))
    good = [site for score, site in scored if score >= SAME_SITE]
    if len(good) == 1:
        return good[0]
    exact = [site for score, site in scored if score == 1.0]
    return exact[0] if len(exact) == 1 else None


def _normal(text: str) -> str:
    text = text.casefold().replace("ё", "е")
    text = re.sub(r"[^\w\s]", " ", text)
    return " ".join(text.split())
