"""Setting a site up through the chat, end to end on the demo company, checked by code.

    python -m nazar_agent.evals.setup            # every case
    python -m nazar_agent.evals.setup --id S01

Each case is a conversation on a fresh demo company, through the real
dispatcher and setup reading: what lands in the draft (the site, its point,
the shift, the group, each person found or waiting for a photo), what the
plan says and asks, the clashes it finds. Where the plan is complete it is
applied — the demo NAZAR must then hold exactly that — and undone, after
which the company must be as it was.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from nazar_agent.actions import ProposalStore
from nazar_agent.assistant import Assistant
from nazar_agent.config import Settings
from nazar_agent.demo.server import DemoNazar
from nazar_agent.evals.questions import BANK_NOW
from nazar_agent.nazar.connect import connect

DEFAULT_OUT = Path("evals-results")
OLMAZOR_LINK = "https://yandex.uz/maps/10335/tashkent/?ll=69.203411%2C41.285612&z=17"
KEY = "eval"


@dataclass
class Outcome:
    replies: list[str] = field(default_factory=list)  # the dispatcher's reading of each message
    setup: dict[str, Any] | None = None  # the window's setup view after the last message
    applied: dict[str, Any] | None = None  # the run, when the plan was applied
    before: dict[str, Any] | None = None  # the company before, after applying, after undoing
    after_apply: dict[str, Any] | None = None
    after_undo: dict[str, Any] | None = None
    error: str | None = None

    @property
    def draft(self) -> dict[str, Any]:
        return (self.setup or {}).get("draft") or {}

    def person(self, name: str) -> dict[str, Any]:
        return next((item for item in self.draft.get("people", []) if name in (item["full_name"] or item["written"])), {})


Check = Callable[[Outcome], str | None]


@dataclass(frozen=True)
class Case:
    id: str
    lang: str
    messages: tuple[str, ...]
    checks: tuple[tuple[str, Check], ...]
    apply: bool = False


def expect(label: str, test: Callable[[Outcome], bool], why: Callable[[Outcome], Any] = lambda out: None) -> tuple[str, Check]:
    return label, lambda out: None if test(out) else f"got {why(out)!r}"


def routed(*kinds: str) -> tuple[str, Check]:
    return expect(f"read as {'/'.join(kinds)}", lambda out: out.replies[-1:] == [kinds[-1]] and all(r in kinds for r in out.replies),
                  lambda out: out.replies)


def shift(start: str, end: str, days: list[int]) -> tuple[str, Check]:
    return expect(f"shift {start}–{end} {days}", lambda out: (out.draft["shift"]["start"], out.draft["shift"]["end"], out.draft["shift"]["days"]) == (start, end, days),
                  lambda out: out.draft.get("shift"))


def state(name: str, wanted: str) -> tuple[str, Check]:
    return expect(f"{name}: {wanted}", lambda out: out.person(name).get("state") == wanted, lambda out: out.person(name))


def steps(*kinds: str) -> tuple[str, Check]:
    return expect("steps " + ", ".join(kinds), lambda out: [s["kind"] for s in out.setup["steps"]] == list(kinds),
                  lambda out: [s["kind"] for s in (out.setup or {}).get("steps", [])])


def missing(*kinds: str) -> tuple[str, Check]:
    return expect("asks " + ", ".join(kinds), lambda out: [m["kind"] for m in out.setup["missing"]] == list(kinds),
                  lambda out: [m["kind"] for m in (out.setup or {}).get("missing", [])])


def clashes(*names: str) -> tuple[str, Check]:
    return expect("clash " + ", ".join(names), lambda out: sorted(c["name"] for c in out.setup["clashes"]) == sorted(names),
                  lambda out: [c["name"] for c in (out.setup or {}).get("clashes", [])])


def round_trip() -> tuple[str, Check]:
    """Applied: the site, the group and the members are in NAZAR; undone: the company is as before."""
    def test(out: Outcome) -> bool:
        return bool(out.applied and out.applied["status"] == "applied" and out.after_apply != out.before and out.after_undo == out.before)
    return expect("applied, then undone back", test, lambda out: (out.applied or {}).get("status"))


S01 = (
    f"Открываем новый объект ЖК Олмазор на Чиланзаре, вот точка {OLMAZOR_LINK}. Смена с 8 до 5, шестидневка. "
    "Туда бригаду Рустама: Рустам, Бобур Каримов и новенький Жасур Тоиров"
)
CASES: tuple[Case, ...] = (
    Case("S01", "ru", (S01,), (
        routed("setup"),
        expect("site ЖК Олмазор", lambda out: out.draft["site"]["name"] == "ЖК Олмазор", lambda out: out.draft.get("site", {}).get("name")),
        expect("point from the link", lambda out: abs((out.draft["site"]["latitude"] or 0) - 41.285612) < 1e-5, lambda out: out.draft.get("site")),
        shift("08:00", "17:00", [1, 2, 3, 4, 5, 6]),
        expect("group Бригада Рустама", lambda out: out.draft["group"]["name"] == "Бригада Рустама", lambda out: out.draft.get("group")),
        state("Рустам", "add"), state("Бобур Каримов", "add"), state("Жасур Тоиров", "new"),
        steps("create_site", "create_group", "group_on_site", "add_to_group", "assign", "wait_for_photo"),
        clashes("Рустам Юлдашев", "Бобур Каримов"),
        round_trip(),
    ), apply=True),
    Case("S02", "ru", (S01, "Смену сделай с 9 до 18, а Бобура не надо"), (
        routed("setup"), shift("09:00", "18:00", [1, 2, 3, 4, 5, 6]), state("Бобур Каримов", "skip"),
        expect("the site kept", lambda out: out.draft["site"]["name"] == "ЖК Олмазор" and out.draft["site"]["latitude"] is not None,
               lambda out: out.draft.get("site")),
        expect("only Rustam assigned", lambda out: out.setup["steps"][4]["text"].endswith("Рустам Юлдашев."), lambda out: out.setup["steps"]),
    )),
    Case("S03", "en", (("Open a new site Sergeli Warehouse 2 at 41.2268, 69.2190, shift 9 to 18 Monday to Friday, "
                        "and put Dilshod Mirzaev there"),), (
        routed("setup"), shift("09:00", "18:00", [1, 2, 3, 4, 5]), state("Dilshod", "add"),
        expect("point from two numbers", lambda out: out.draft["site"]["point_source"] == "numbers", lambda out: out.draft.get("site")),
        expect("steps in English", lambda out: out.setup["steps"][0]["text"].startswith("Create the location"), lambda out: out.setup["steps"][:1]),
        clashes("Дилшод Мирзаев"), round_trip(),
    ), apply=True),
    Case("S04", "uz", ("Yashnobod obyektiga ikkinchi smena guruhini qo'y: soat 14:00 dan 22:00 gacha, dushanbadan jumagacha",), (
        routed("setup"),
        expect("the existing site", lambda out: out.draft["site"]["existing_id"] != "", lambda out: out.draft.get("site")),
        shift("14:00", "22:00", [1, 2, 3, 4, 5]),
        steps("create_group", "group_on_site"),
        expect("its own shift there", lambda out: "o'z smenasi bilan" in out.setup["steps"][1]["text"], lambda out: out.setup["steps"]),
        round_trip(),
    ), apply=True),
    Case("S05", "ru", ("Открой новый объект Склад Юнусабад",), (
        routed("setup"), missing("point", "shift"),
        expect("not ready", lambda out: not out.setup["ready"], lambda out: out.setup.get("ready")),
    )),
    Case("S06", "ru", ("Поставь Каримова на Сергели",), (
        routed("setup"),
        expect("Sergeli found", lambda out: out.draft["site"]["existing_id"] != "", lambda out: out.draft.get("site")),
        expect("asks which Karimov", lambda out: len(out.person("Каримов").get("choices", [])) == 2, lambda out: out.person("Каримов")),
        missing("choose_person"),
    )),
    Case("S07", "ru", ("Удали объект «Склад-ангар, Сергели»",), (
        routed("unsupported"), expect("no setup", lambda out: out.setup is None, lambda out: bool(out.setup)),
    )),
)


def snapshot(demo: DemoNazar) -> dict[str, Any]:
    """What a setup can change in the demo company."""
    world, setup = demo.world, demo.setup
    return {
        "sites": sorted(site.name for site in world.sites.values()),
        "groups": sorted((group.name, tuple(sorted(group.members))) for group in setup.departments.values()),
        "at_sites": sorted((site, tuple(sorted(groups))) for site, groups in setup.groups.items() if groups),
        "extra": sorted((person, tuple(sorted(sites))) for person, sites in world.extra_sites.items() if sites),
        "homes": sorted((person.id, person.site_id or "") for person in world.people.values()),
    }


async def run_case(case: Case, folder: Path) -> dict[str, Any]:
    started = time.monotonic()
    settings = Settings(demo_mode=True, demo_now=BANK_NOW, telegram_bot_token=None, agent_db_path=str(folder / f"{case.id}.sqlite3"))
    link = connect(settings)
    assert link.demo is not None
    nazar = link.client()
    assistant = Assistant(settings, ProposalStore(settings.agent_db_path), service=nazar)
    out, cost = Outcome(), 0.0
    try:
        for message in case.messages:
            answer = await assistant.ask(message, conversation=KEY)
            out.replies.append(answer.view.get("reply") or "data")
            out.setup = answer.view.get("setup")
            cost += answer.view["stats"]["cost_usd"]
        if case.apply and out.setup and out.setup["ready"]:
            today = settings.now().date()
            out.before = snapshot(link.demo)
            applied = await assistant.setup.apply(KEY, out.draft["id"], nazar, "eval", today)
            out.applied = applied["run"]
            out.after_apply = snapshot(link.demo)
            await assistant.setup.undo(KEY, out.applied["id"], nazar, "eval")
            out.after_undo = snapshot(link.demo)
    except Exception as exc:  # noqa: BLE001 — a failed case is a result too
        out.error = f"{type(exc).__name__}: {exc}"
    finally:
        await nazar.aclose()
    failed = {label: why for label, check in case.checks if (why := _safe(check, out)) is not None}
    if out.error:
        failed["error"] = out.error
    return {
        "id": case.id, "lang": case.lang, "messages": list(case.messages), "passed": not failed, "failed": failed,
        "checks": len(case.checks), "cost_usd": round(cost, 4), "seconds": round(time.monotonic() - started),
        "outcome": asdict(out),
    }


def _safe(check: Check, out: Outcome) -> str | None:
    try:
        return check(out)
    except (KeyError, TypeError, IndexError) as exc:
        return f"not there: {exc}"


async def main_async(cases: list[Case], folder: Path) -> list[dict[str, Any]]:
    results = []
    for case in cases:  # one at a time: each builds its own demo company
        result = await run_case(case, folder)
        (folder / f"{case.id}.json").write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        mark = "PASS" if result["passed"] else "FAIL"
        print(f"{case.id} {mark} {case.messages[-1][:60]} — {result['seconds']}s ${result['cost_usd']}"
              + "".join(f"\n    {label}: {why}" for label, why in result["failed"].items()), flush=True)
        results.append(result)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--id", nargs="*", help="only these cases")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    cases = [case for case in CASES if not args.id or case.id in args.id]
    folder = args.out / f"setup-{datetime.now():%Y-%m-%d-%H%M%S}"
    folder.mkdir(parents=True, exist_ok=True)
    results = asyncio.run(main_async(cases, folder))
    passed = sum(result["passed"] for result in results)
    checks = sum(result["checks"] for result in results)
    failed_checks = sum(len(result["failed"]) for result in results)
    print(f"\n{passed}/{len(results)} cases, {checks - failed_checks}/{checks} checks, "
          f"${sum(result['cost_usd'] for result in results):.4f}, saved in {folder}")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
