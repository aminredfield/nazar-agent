"""The planted cases of the construction demo company and what the agents must do.

Northline Builders (NAZAR's ``scripts/seed_construction_demo.py``) is seeded
with known answers. People are named, not referenced by id: ids change on
every reseed, names do not.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# Four workers left ten days ago; the foreman keeps checking them in from his phone.
DEAD_SOULS = ("Tom Harris", "Grace Ingram", "Megan Young", "Fiona Carter")
# The foreman himself is neither a target nor protected: suspecting him is the point.
FOREMAN = "Mike Turner"
# People who did nothing wrong, each with the reason a careless reader could suspect them.
PROTECTED = {
    "Daniel Morgan": "honest foreman, checks in two workers without smartphones from his phone",
    "Henry Ross": "no smartphone, checked in by the honest foreman, on the site all day",
    "Alex Baker": "no smartphone, checked in by the honest foreman, on the site all day",
    "Sam Ellis": "weak GPS: flags and twice outside the fence, but live selfies and full days",
    "Zoe Nash": "arrives at 09:00 because the site's schedule says 08:00 by mistake",
    "Kate Palmer": "arrives at 09:00 because the site's schedule says 08:00 by mistake",
    "Diana Evans": "arrives at 09:00 because the site's schedule says 08:00 by mistake",
    "Adam Novak": "ordinary worker",
    "Ben Carter": "ordinary worker",
    "Ian Foster": "ordinary worker",
    "Nora Sanders": "ordinary worker",
}
WRONG_SCHEDULE_SITE = "Maple Court residential, finishing"
WRONG_SCHEDULE_CREW = ("Zoe Nash", "Kate Palmer", "Diana Evans")
WRONG_SCHEDULE_REAL_START = "09:00"  # the site says 08:00

# A foreman's list for a new crew: one existing employee (in the other word
# order), one person written twice, one surname only, the rest new.
NEW_CREW_LIST = """Site Riverside:
1. Novak Adam
2. Collins Sean
3. Carter Jason
4. Collins Sean
5. Edwards
Site Eastgate:
6. Abbott Seth"""
LIST_DUPLICATE_OF_EMPLOYEE = "Adam Novak"
LIST_TWICE = "Sean Collins"
LIST_SURNAME_ONLY = "Edwards"


@dataclass(frozen=True, slots=True)
class Case:
    name: str
    kind: Literal["analysis", "import"]
    question: str
    analyst: str = "attendance"


CASES = {
    case.name: case
    for case in (
        Case("dead_souls", "analysis", "Что стоит проверить в отметках за последние две недели?"),
        Case(
            "wrong_schedule",
            "analysis",
            "Что стоит проверить в табеле за последние две недели перед оплатой?",
            analyst="timesheet",
        ),
        Case("new_crew_list", "import", NEW_CREW_LIST),
    )
}
