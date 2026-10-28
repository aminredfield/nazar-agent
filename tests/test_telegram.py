import asyncio
from types import SimpleNamespace

import pytest

from nazar_agent.actions import Executor, ProposalStore
from nazar_agent.assistant import AssistantBusy
from nazar_agent.telegram.bot import Bot
from nazar_agent.telegram.render import card_keyboard, card_text
from nazar_agent.views import proposal_view
from tests import factories
from tests.test_executor import propose

OWNER = 1001
STRANGER = 666


class FakeApi:
    def __init__(self):
        self.sent: list[tuple] = []
        self.edited: list[tuple] = []
        self.answers: list[str] = []
        self._next_id = 100

    async def send_message(self, chat_id, text, keyboard=None):
        self._next_id += 1
        self.sent.append((chat_id, text, keyboard))
        return {"message_id": self._next_id}

    async def edit_message(self, chat_id, message_id, text, keyboard):
        self.edited.append((message_id, text, keyboard))

    async def edit_keyboard(self, chat_id, message_id, keyboard):
        self.edited.append((message_id, None, keyboard))

    async def answer_callback(self, callback_id, text=""):
        self.answers.append(text)

    async def send_chat_action(self, chat_id, action="typing"):
        pass

    async def download(self, file_id):
        return b"ogg-bytes"


class FakeAssistant:
    """The real executor and store; the team's answer is prepared by the test."""

    def __init__(self, store, nazar, cards):
        self.store = store
        self._nazar = nazar
        self.cards = cards
        self.busy = False
        self.questions: list[str] = []

    def executor(self, nazar=None):
        return Executor(self.store, nazar or self._nazar)

    async def ask(self, question, analyst=None, *, nazar=None, source="ask"):
        if self.busy:
            raise AssistantBusy
        self.questions.append(question)
        return SimpleNamespace(view={
            "summary": "Стоит проверить смену <Зарины>.",
            "data_gaps": [],
            "notes": [],
            "cards": self.cards,
            "fact_checker_rounds": [["finding 1, fact 1: wrong"]],
            "removed_by_fact_checker": [],
            "stats": {"tool_calls": 7, "cost_usd": 0.015},
        })


def card(*proposals) -> dict:
    return {
        "title": "Смена засчитана со второй отметки",
        "subject": {"kind": "employee", "id": factories.EMPLOYEE, "name": "Зарина <b>Хакимова</b>"},
        "confidence": "medium",
        "analyst_confidence": "high",
        "skeptic": {"verdict": "inconclusive", "reasoning": "r"},
        "facts": [{"text": "Приход в 09:06, засчитано с 10:14", "evidence": []}],
        "hypothesis": "Табель взял вторую отметку.",
        "alternatives": ["Первая отметка была пробной"],
        "check_steps": ["Посмотреть фото"],
        "actions_why": "",
        "actions": [proposal_view(p) for p in proposals],
    }


@pytest.fixture
def setup(tmp_path):
    store = ProposalStore(tmp_path / "agent.sqlite3")
    nazar, seen = factories.nazar()
    proposal = propose(store)
    api = FakeApi()
    assistant = FakeAssistant(store, nazar, [card(proposal)])

    async def transcriber(audio):
        assert audio == b"ogg-bytes"
        return "Что проверить перед выплатой?"

    bot = Bot(api, assistant, frozenset({OWNER}), transcriber)
    return bot, api, assistant, proposal, seen


def message(text=None, chat=OWNER, **extra):
    return {"update_id": 1, "message": {"chat": {"id": chat}, "from": {"first_name": "Шерзод", "username": "sherzod"}, "text": text, **extra}}


def press(data, chat=OWNER, message_id=102):
    return {"update_id": 2, "callback_query": {
        "id": "cb", "data": data, "from": {"first_name": "Шерзод", "username": "sherzod"},
        "message": {"chat": {"id": chat}, "message_id": message_id},
    }}


async def test_strangers_get_their_chat_id_and_nothing_else(setup):
    bot, api, assistant, _, _ = setup

    await bot.handle(message("Покажи табель", chat=STRANGER))

    [(chat, text, _)] = api.sent
    assert chat == STRANGER and str(STRANGER) in text and "Нет доступа" in text
    assert assistant.questions == []


async def test_question_gets_summary_cards_with_buttons_and_footer(setup):
    bot, api, assistant, proposal, _ = setup

    await bot.handle(message("Что проверить перед выплатой?"))

    texts = [text for _, text, _ in api.sent]
    assert "1–3 минуты" in texts[0]
    assert "&lt;Зарины&gt;" in texts[1]  # model text is escaped
    assert "Зарина &lt;b&gt;Хакимова&lt;/b&gt;" in texts[2]
    assert "Опоздание: 74 → <b>6</b> мин" in texts[2]
    assert api.sent[2][2][0][0]["callback_data"] == f"a:{proposal.id}"
    assert "исправлено 1" in texts[3]


async def test_voice_is_transcribed_shown_and_answered(setup):
    bot, api, assistant, _, _ = setup

    await bot.handle(message(None, voice={"file_id": "f1", "duration": 3}))

    assert "🎙" in api.sent[0][1] and "Что проверить перед выплатой?" in api.sent[0][1]
    assert assistant.questions == ["Что проверить перед выплатой?"]


async def test_pay_change_asks_twice_then_writes_and_can_be_undone(setup):
    bot, api, assistant, proposal, seen = setup
    await bot.handle(message("Что проверить?"))

    await bot.handle(press(f"a:{proposal.id}"))
    assert api.edited[-1][2][0][0]["callback_data"] == f"y:{proposal.id}"  # confirmation, nothing written yet
    assert not any(r.url.path.endswith("/timesheet/adjustments") for r in seen)

    await bot.handle(press(f"y:{proposal.id}"))
    assert assistant.store.get(proposal.id).status == "executed"
    assert api.answers[-1] == "Выполнено в NAZAR."
    assert "Статус: выполнено в NAZAR" in api.edited[-1][1]
    assert api.edited[-1][2][0][0]["callback_data"] == f"u:{proposal.id}"
    executed = assistant.store.audit(proposal_id=proposal.id)[0]
    assert executed.actor == "Шерзод (Telegram @sherzod)"

    await bot.handle(press(f"u:{proposal.id}"))
    assert assistant.store.get(proposal.id).status == "undone"


async def test_disagreeing_takes_the_next_message_as_the_reason(setup):
    bot, api, assistant, proposal, _ = setup
    await bot.handle(message("Что проверить?"))

    await bot.handle(press(f"d:{proposal.id}"))
    await bot.handle(message("У неё правда был сбой телефона"))

    assert assistant.store.get(proposal.id).status == "declined"
    assert assistant.store.audit(proposal_id=proposal.id)[0].details == {"reason": "У неё правда был сбой телефона"}
    assert len(assistant.questions) == 1  # the reason was not taken as a new question


async def test_busy_assistant_says_so(setup):
    bot, api, assistant, _, _ = setup
    assistant.busy = True

    await bot.handle(message("Что проверить?"))

    assert "ещё отвечаю" in api.sent[0][1]


async def test_postpone_hides_the_buttons_and_keeps_the_proposal(setup):
    bot, api, assistant, proposal, _ = setup
    await bot.handle(message("Что проверить?"))

    await bot.handle(press(f"p:{proposal.id}"))

    assert api.edited[-1][2] == []
    assert assistant.store.get(proposal.id).status == "pending"


def test_card_renders_only_safe_html():
    view = card()
    view["facts"] = [{"text": "<script>alert(1)</script>", "evidence": []}]

    text = card_text(view, 1)

    assert "<script>" not in text and "&lt;script&gt;" in text
    assert card_keyboard(view) == []


async def test_polling_survives_a_blocked_network(setup, monkeypatch):
    import httpx

    bot, api, _, _, _ = setup
    calls = {"n": 0}

    async def get_updates(offset):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectTimeout("timed out")
        raise asyncio.CancelledError  # stop the loop after the retry

    async def no_wait(seconds):
        return None

    api.get_updates = get_updates
    monkeypatch.setattr("nazar_agent.telegram.bot.asyncio.sleep", no_wait)

    with pytest.raises(asyncio.CancelledError):
        await bot.run()
    assert calls["n"] == 2


def test_schedule_change_reads_as_hours_before_and_after():
    from nazar_agent.telegram.render import action_text

    action = {
        "name": "change_schedule",
        "level": "changes_pay",
        "status": "pending",
        "params": {"object_id": "x", "shift_start": "09:00", "shift_end": "19:00", "reason": "r"},
        "preview": {
            "before": {"shift_start": "08:00", "shift_end": "18:00", "working_days": [1, 2, 3, 4, 5, 6]},
            "after": {"shift_start": "09:00", "shift_end": "19:00", "working_days": [1, 2, 3, 4, 5]},
        },
    }

    text = action_text(action, 1)

    assert "Изменить график объекта" in text and "прошлые дни не пересчитываются" in text
    assert "Смена: 08:00–18:00 → <b>09:00–19:00</b>" in text
    assert "Рабочие дни: пн, вт, ср, чт, пт, сб → <b>пн, вт, ср, чт, пт</b>" in text
