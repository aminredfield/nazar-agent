"""The Scenarios page's routes: the new abilities played on staged scenes, one model call each.

Each call goes into the spend ledger like a question; the public demo limits it by its daily
budget only, so trying every scene does not use up a visitor's questions.
A person may bring their own picture instead of the staged one. After a verdict the page
offers the actions code allows for it (scenarios/acts.py); the explain scene talks to the
employee through a cabinet of their own (/scenarios/explain/cabinet).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from openai import OpenAIError
from pydantic import BaseModel, Field

from nazar_agent.llm import LLMClient
from nazar_agent.scenarios import (
    EXPLAIN,
    HEADCOUNT,
    PLACE_GPS,
    PLACES,
    ROSTER,
    ScenarioError,
    ask,
    check,
    count_shift,
    read_roster,
    same_place,
)
from nazar_agent.scenarios.acts import ACTIONS, ActRefused, SceneDesk, effect
from nazar_agent.usage import Meter

# One scene is a short call: counted at this for the public demo's daily budget.
SCENE_HOLD_USD = 0.05
Lang = Literal["en", "ru", "uz"]
# The pages of the gallery, one per ability; the window's scenarios.js knows the same names.
SCENE_PAGES = ("gps", "explain", "headcount", "roster")
# A page asks the server every time: after a release no one is left on the old one.
PAGE_HEADERS = {"Cache-Control": "no-cache"}


class ExplainAsk(BaseModel):
    scene: str
    lang: Lang = "en"  # the employee's: the question is written in it
    window_lang: Lang | None = None  # the owner's: the verdict is written in it


class ExplainCheck(BaseModel):
    scene: str
    said: str = Field(min_length=2, max_length=1000)
    lang: Lang = "en"


class CabinetReply(BaseModel):
    scene: str
    said: str = Field(min_length=2, max_length=1000)


class SceneAct(BaseModel):
    page: Literal["gps", "explain", "headcount"]
    scene: str
    action: Literal[ACTIONS]  # type: ignore[valid-type]
    undo: bool = False
    confirmed: bool = False  # said yes twice: an action that changes pay needs it


def scenario_routes(
    static: Path,
    caller: Callable[..., Any],
    reserve: Callable[..., str],
    add_cost: Callable[..., None],
    read_upload: Callable[[UploadFile | None], Awaitable[bytes | None]],
    desk: SceneDesk,
) -> APIRouter:
    router = APIRouter()
    pictures = static / "scenarios"

    async def played(http: Request, current: Any, lang: str, what: str, play: Callable[[LLMClient, str], Awaitable[Any]]) -> Any:
        # A scene is a few cents and not a question: only the day's budget limits it. It holds
        # a few cents while it runs; what its calls really cost replaces that, a large photo included.
        day = reserve(http, current, question=False, lang=lang, budget_only=True, hold=SCENE_HOLD_USD)
        meter = Meter()
        try:
            return await current.assistant.scenario(what, play, meter)
        except ScenarioError as exc:
            raise HTTPException(422, str(exc)) from exc
        except OpenAIError as exc:
            raise HTTPException(502, "The model did not answer — try again in a minute.") from exc
        finally:
            add_cost(http, current, day, meter.cost, held=SCENE_HOLD_USD)

    def picture(name: str) -> bytes:
        return (pictures / name).read_bytes()

    @router.get("/scenarios", include_in_schema=False)
    async def page() -> FileResponse:
        return FileResponse(static / "scenarios.html", headers=PAGE_HEADERS)

    # One scene on its own page: the same file, the script reads which from the address.
    # The employee's side of the explain scene: the cabinet the agent's question arrives in.
    @router.get("/scenarios/explain/cabinet", include_in_schema=False)
    async def cabinet_page() -> FileResponse:
        return FileResponse(static / "cabinet.html", headers=PAGE_HEADERS)

    @router.get("/scenarios/{scene}", include_in_schema=False)
    async def scene_page(scene: str) -> FileResponse:
        if scene not in SCENE_PAGES:
            raise HTTPException(404, "No such scenario.")
        return FileResponse(static / "scenarios.html", headers=PAGE_HEADERS)

    @router.get("/api/scenarios")
    async def scenes(current: Any = Depends(caller)) -> dict[str, Any]:
        return {"explain": EXPLAIN, "roster": ROSTER, "headcount": HEADCOUNT, "places": PLACES, "place_gps": PLACE_GPS}

    @router.post("/api/scenarios/explain/ask")
    async def explain_ask(http: Request, body: ExplainAsk, current: Any = Depends(caller)) -> dict[str, str]:
        case = _scene(EXPLAIN, body.scene)
        message = await played(http, current, body.lang, "explain: ask", lambda llm, model: ask(llm, model, case, body.lang))
        desk.asked(current.session_id, body.scene, message, body.window_lang or body.lang)
        desk.judged(current.session_id, "explain", body.scene, "")  # a new question: no verdict yet
        return {"message": message}

    async def judge_reply(http: Request, current: Any, scene: str, said: str, lang: str) -> dict[str, Any]:
        case = _scene(EXPLAIN, scene)
        found = await played(http, current, lang, f"explain: {said[:80]}", lambda llm, model: check(llm, model, case, said, lang))
        return offered({**found}, desk.judged(current.session_id, "explain", scene, found["verdict"]), "explain", scene)

    @router.post("/api/scenarios/explain/check")
    async def explain_check(http: Request, body: ExplainCheck, current: Any = Depends(caller)) -> dict[str, Any]:
        return await judge_reply(http, current, body.scene, body.said, body.lang)

    @router.get("/api/scenarios/inbox")
    async def inbox(scene: str, current: Any = Depends(caller)) -> dict[str, Any]:
        """The employee's cabinet and the owner's window both read the conversation from here."""
        case = _scene(EXPLAIN, scene)
        return {**desk.inbox(current.session_id, scene), "employee": case["employee"], "place": case["place"],
                "replies": case["replies"], "applied": desk.applied(current.session_id, "explain", scene)}

    @router.post("/api/scenarios/inbox/reply")
    async def inbox_reply(http: Request, body: CabinetReply, current: Any = Depends(caller)) -> dict[str, Any]:
        """The employee answers in their cabinet; the verdict is written in the owner's window language."""
        _scene(EXPLAIN, body.scene)
        asked = desk.inbox(current.session_id, body.scene)
        if not asked["message"]:
            raise HTTPException(409, "The employee has no question yet.")
        found = await judge_reply(http, current, body.scene, body.said, asked["lang"])
        desk.replied(current.session_id, body.scene, body.said, found)
        return found

    @router.post("/api/scenarios/act")
    async def act(body: SceneAct, current: Any = Depends(caller)) -> dict[str, Any]:
        """Apply or undo what the verdict allows, on the scene's own record: nothing here reaches NAZAR."""
        try:
            return desk.act(current.session_id, body.page, body.scene, body.action, undo=body.undo, confirmed=body.confirmed)
        except ActRefused as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/api/scenarios/roster")
    async def roster(
        http: Request, scene: str = Form(""), lang: Lang = Form("en"), file: UploadFile | None = File(None),
        current: Any = Depends(caller),
    ) -> dict[str, Any]:
        own = await read_upload(file)
        data, mime = (own, file.content_type or "image/png") if own else (picture(_scene(ROSTER, scene)["file"]), "image/png")
        found = await played(http, current, lang, "roster", lambda llm, model: read_roster(llm, model, data, mime, lang))
        return {"people": found.people, "plan": found.plan, "unclear": found.unclear}

    @router.post("/api/scenarios/headcount")
    async def headcount(
        http: Request, scene: str = Form(...), lang: Lang = Form("en"), file: UploadFile | None = File(None),
        current: Any = Depends(caller),
    ) -> dict[str, Any]:
        case = _scene(HEADCOUNT, scene)
        own = await read_upload(file)
        data, mime = (own, file.content_type or "image/jpeg") if own else (picture(case["file"]), "image/jpeg")
        found = await played(http, current, lang, f"headcount: {scene}",
                             lambda llm, model: count_shift(llm, model, data, mime, case["checked_in"], lang))
        verdict = "all_here" if found["in_team"] == found["checked_in"] else "missing" if found["in_team"] < found["checked_in"] else "extra"
        return offered({**found, "outcome": verdict}, desk.judged(current.session_id, "headcount", scene, verdict), "headcount", scene)

    @router.post("/api/scenarios/place")
    async def place(
        http: Request, scene: str = Form(...), candidate: int = Form(0), lang: Lang = Form("en"),
        file: UploadFile | None = File(None), current: Any = Depends(caller),
    ) -> dict[str, Any]:
        case = _scene(PLACES, scene)
        own = await read_upload(file)
        if own is None and not 0 <= candidate < len(case["candidates"]):
            raise HTTPException(404, "No such check-in in this scene.")
        new = own or picture(case["candidates"][candidate]["file"])
        references = [picture(name) for name in case["references"]]
        found = await played(http, current, lang, f"place: {scene}",
                             lambda llm, model: same_place(llm, model, references, new, lang))
        # A photo of the visitor's own has no check-in behind it: the verdict, no actions.
        if own is not None:
            desk.judged(current.session_id, "gps", scene, "")
            return offered({**found}, [], "gps", scene)
        verdict = "same" if found["same_place"] else "not_same"
        actions = desk.judged(current.session_id, "gps", scene, verdict, subject=str(candidate))
        return offered({**found}, actions, "gps", scene, str(candidate))

    return router


def offered(found: dict[str, Any], actions: list[str], page: str, scene: str, subject: str = "") -> dict[str, Any]:
    """The verdict with what may follow it: the actions' names and what each would change, before it is applied."""
    return {**found, "actions": actions, "offers": [effect(page, scene, action, subject) for action in actions]}


def _scene(scenes: dict[str, Any], name: str) -> Any:
    if name not in scenes:
        raise HTTPException(404, "No such scene.")
    return scenes[name]
