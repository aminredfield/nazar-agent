"""The Scenarios page's routes: the new abilities played on staged scenes, one model call each.

Each call goes into the spend ledger like a question; the public demo limits it by its daily
budget only, so trying every scene does not use up a visitor's questions.
A person may bring their own picture instead of the staged one.
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

# One scene is a short call: counted at this for the public demo's daily budget.
SCENE_COST_ESTIMATE_USD = 0.01
Lang = Literal["en", "ru", "uz"]


class ExplainAsk(BaseModel):
    scene: str
    lang: Lang = "en"


class ExplainCheck(BaseModel):
    scene: str
    said: str = Field(min_length=2, max_length=1000)
    lang: Lang = "en"


def scenario_routes(
    static: Path,
    caller: Callable[..., Any],
    reserve: Callable[..., str],
    add_cost: Callable[..., None],
    read_upload: Callable[[UploadFile | None], Awaitable[bytes | None]],
) -> APIRouter:
    router = APIRouter()
    pictures = static / "scenarios"

    async def played(http: Request, current: Any, lang: str, what: str, play: Callable[[LLMClient, str], Awaitable[Any]]) -> Any:
        # A scene is a few cents and not a question: only the day's budget limits it.
        day = reserve(http, current, question=False, lang=lang, budget_only=True)
        try:
            return await current.assistant.scenario(what, play)
        except ScenarioError as exc:
            raise HTTPException(422, str(exc)) from exc
        except OpenAIError as exc:
            raise HTTPException(502, "The model did not answer — try again in a minute.") from exc
        finally:
            add_cost(http, current, day, SCENE_COST_ESTIMATE_USD)

    def picture(name: str) -> bytes:
        return (pictures / name).read_bytes()

    @router.get("/scenarios", include_in_schema=False)
    async def page() -> FileResponse:
        return FileResponse(static / "scenarios.html")

    @router.get("/api/scenarios")
    async def scenes(current: Any = Depends(caller)) -> dict[str, Any]:
        return {"explain": EXPLAIN, "roster": ROSTER, "headcount": HEADCOUNT, "places": PLACES, "place_gps": PLACE_GPS}

    @router.post("/api/scenarios/explain/ask")
    async def explain_ask(http: Request, body: ExplainAsk, current: Any = Depends(caller)) -> dict[str, str]:
        case = _scene(EXPLAIN, body.scene)
        message = await played(http, current, body.lang, "explain: ask", lambda llm, model: ask(llm, model, case, body.lang))
        return {"message": message}

    @router.post("/api/scenarios/explain/check")
    async def explain_check(http: Request, body: ExplainCheck, current: Any = Depends(caller)) -> dict[str, str]:
        case = _scene(EXPLAIN, body.scene)
        return await played(http, current, body.lang, f"explain: {body.said[:80]}",
                            lambda llm, model: check(llm, model, case, body.said, body.lang))

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
        return await played(http, current, lang, f"headcount: {scene}",
                            lambda llm, model: count_shift(llm, model, data, mime, case["checked_in"], lang))

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
        return await played(http, current, lang, f"place: {scene}",
                            lambda llm, model: same_place(llm, model, references, new, lang))

    return router


def _scene(scenes: dict[str, Any], name: str) -> Any:
    if name not in scenes:
        raise HTTPException(404, "No such scene.")
    return scenes[name]
