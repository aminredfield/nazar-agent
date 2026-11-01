"""The Scenarios page's abilities: the model reads and compares, code builds the plan, the counts and the words."""

import json

import pytest

from nazar_agent.scenarios import EXPLAIN, HEADCOUNT, PLACES, ScenarioError, ask, check, count_shift, read_roster, roster_plan
from tests.fakes import ScriptedLLM, turn


def said(payload) -> ScriptedLLM:
    return ScriptedLLM([turn(text=json.dumps(payload))])


async def test_the_employee_is_asked_in_their_language_and_their_answer_checked_against_the_records():
    case = EXPLAIN["cafe-late"]
    llm = ScriptedLLM([turn(text=json.dumps({"message": "Salom Lena! …"})),
                       turn(text=json.dumps({"verdict": "contradicts", "why": "Wi-Fi at 10:39.", "next_step": "Talk to her."}))])

    message = await ask(llm, "spark", case, "uz")
    result = await check(llm, "spark", case, "I was there from ten", "en")

    assert message == "Salom Lena! …" and "Uzbek" in llm.requests[0]["instructions"]
    assert "10:41" in llm.requests[0]["input"] and "Wi-Fi at 10:39" in llm.requests[1]["input"]
    assert result["verdict"] == "contradicts" and llm.requests[1]["role"] == "scenario"


async def test_an_unreadable_answer_is_said_not_shown():
    with pytest.raises(ScenarioError):
        await check(ScriptedLLM([turn(text="{oops")]), "spark", EXPLAIN["cafe-late"], "hello", "en")


def test_the_plan_is_built_by_code_a_location_per_workplace_a_shift_per_group():
    people = [
        {"name": "Jake", "workplace": "Corner Café", "days": [3, 4, 5, 6, 7], "shift_start": "10:00", "shift_end": "22:00"},
        {"name": "Zara", "workplace": "Corner Café", "days": [7, 3, 4, 5, 6], "shift_start": "10:00", "shift_end": "22:00"},
        {"name": "Frank", "workplace": "Corner Café", "days": [1, 2, 3, 4, 5], "shift_start": "07:00", "shift_end": "15:00"},
        {"name": "Andy", "workplace": "Fresh Market", "days": [1, 3, 5], "shift_start": "09:00", "shift_end": "18:00"},
        {"name": "Emma", "workplace": " ", "days": [1], "shift_start": "09:00", "shift_end": "13:00"},
    ]

    assert roster_plan(people) == [
        "Create the location “Corner Café”.",
        "Shift 10:00–22:00, Wed–Sun: Jake, Zara.",
        "Shift 07:00–15:00, Mon–Fri: Frank.",
        "Create the location “Fresh Market”.",
        "Shift 09:00–18:00, Mon, Wed, Fri: Andy.",
        "Ask where they work — the picture does not say: Emma.",
        "Add 5 people — each waits for a face photo before NAZAR creates them.",
    ]


async def test_a_picture_becomes_people_a_plan_and_what_was_unclear():
    person = {"name": "Dana", "workplace": "Fresh Market", "days": [1, 2], "shift_start": "09:00", "shift_end": "18:00"}
    llm = said({"people": [person], "unclear": ["Sophie's days"]})

    roster = await read_roster(llm, "spark", b"png", "image/png")

    assert roster.people == [person] and roster.unclear == ["Sophie's days"] and roster.plan[0] == "Create the location “Fresh Market”."
    [message] = llm.requests[0]["input"]
    assert message["content"][0]["image_url"].startswith("data:image/png;base64,")


@pytest.mark.parametrize(("in_team", "verdict"), [
    (7, "Sees 7 in the photo — all 7 who checked in."),
    (5, "Sees 5 in the photo, but 7 checked in: 2 are not in the photo — worth asking who."),
    (6, "Sees 6 in the photo, but 7 checked in: 1 is not in the photo — worth asking who."),
    (9, "Sees 9 in the photo, but only 7 checked in: someone is working without a check-in."),
])
async def test_the_shift_photo_is_compared_with_the_check_ins_by_code(in_team, verdict):
    result = await count_shift(said({"people": in_team + 1, "in_team": in_team, "notes": ""}), "spark", b"jpg", "image/jpeg",
                               HEADCOUNT["cafe"]["checked_in"])

    assert result["verdict"] == verdict and result["checked_in"] == 7


def test_every_staged_scene_names_pictures_that_exist():
    from nazar_agent.service.app import STATIC

    files = [scene["file"] for scene in HEADCOUNT.values()]
    files += [name for scene in PLACES.values() for name in scene["references"]]
    files += [item["file"] for scene in PLACES.values() for item in scene["candidates"]]
    files += ["roster-excel.png", "roster-chat.png"]
    assert all((STATIC / "scenarios" / name).is_file() for name in files)
    # Each scene answers three ways — fits, contradicts, cannot tell — in every window language.
    for scene in EXPLAIN.values():
        assert sorted(reply["expect"] for reply in scene["replies"]) == ["cannot_tell", "consistent", "contradicts"]
        assert all(set(reply["text"]) == {"en", "ru", "uz"} for reply in scene["replies"])
