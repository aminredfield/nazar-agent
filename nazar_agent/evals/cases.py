"""The planted cases of the construction demo company and what the agents must do.

Mustahkam Qurilish (NAZAR's ``scripts/seed_construction_demo.py``) is seeded
with known answers. People are named, not referenced by id: ids change on
every reseed, names do not.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# Four workers left ten days ago; the foreman keeps checking them in from his phone.
DEAD_SOULS = ("Тимур Хасанов", "Гулчехра Ибрагимова", "Мохира Юнусова", "Феруза Каримова")
# The foreman himself is neither a target nor protected: suspecting him is the point.
FOREMAN = "Рустам Юлдашев"
# People who did nothing wrong, each with the reason a careless reader could suspect them.
PROTECTED = {
    "Дилшод Мирзаев": "honest foreman, checks in two workers without smartphones from his phone",
    "Хуршид Рахмонов": "no smartphone, checked in by the honest foreman, on the site all day",
    "Анвар Бакиров": "no smartphone, checked in by the honest foreman, on the site all day",
    "Шерзод Алимов": "weak GPS: flags and twice outside the fence, but live selfies and full days",
    "Зухра Набиева": "arrives at 09:00 because the site's schedule says 08:00 by mistake",
    "Камола Ташпулатова": "arrives at 09:00 because the site's schedule says 08:00 by mistake",
    "Дилфуза Эргашева": "arrives at 09:00 because the site's schedule says 08:00 by mistake",
    "Азамат Норматов": "ordinary worker",
    "Бобур Каримов": "ordinary worker",
    "Илхом Тураев": "ordinary worker",
    "Нодира Сафарова": "ordinary worker",
}
WRONG_SCHEDULE_SITE = "ЖК «Яшнабад», отделка"
WRONG_SCHEDULE_CREW = ("Зухра Набиева", "Камола Ташпулатова", "Дилфуза Эргашева")
WRONG_SCHEDULE_REAL_START = "09:00"  # the site says 08:00

# A foreman's list for a new crew: one existing employee (in the other word
# order), one person written twice, one surname only, the rest new.
NEW_CREW_LIST = """Объект Чиланзар:
1. Норматов Азамат
2. Юсупов Сардор
3. Каримов Жасур
4. Юсупов Сардор
5. Эргашев
Объект Сергели:
6. Абдуллаев Ботир"""
LIST_DUPLICATE_OF_EMPLOYEE = "Азамат Норматов"
LIST_TWICE = "Сардор Юсупов"
LIST_SURNAME_ONLY = "Эргашев"


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
