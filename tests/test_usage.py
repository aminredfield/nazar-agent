from datetime import UTC, datetime, timedelta

from nazar_agent.roles.dispatcher import route
from nazar_agent.usage import Call, Ledger, Meter, count, metering
from tests.fakes import ScriptedLLM, turn


def test_calls_outside_a_metered_run_are_not_kept_and_inside_are_summed_by_role():
    count("analyst", "muse-spark-1.3", 10, 5)  # nobody meters this
    meter = Meter()

    with metering(meter):
        count("attendance_analyst", "muse-spark-1.3", 1_000_000, 0)
        count("attendance_analyst", "muse-spark-1.3", 0, 1_000_000)
        count("skeptic", "meta-models/muse-spark-open-1026", 1_000, 1_000)

    analyst, skeptic = meter.by_role()
    assert (analyst["calls"], analyst["input_tokens"], analyst["output_tokens"]) == (2, 1_000_000, 1_000_000)
    assert analyst["cost_usd"] == 5.5 and analyst["estimated"] is False  # 1.25 + 4.25 per million
    assert skeptic["estimated"] is True and skeptic["cost_usd"] > 0  # no known price: priced high, never free


async def test_the_dispatcher_is_counted_too():
    llm = ScriptedLLM([turn(text='{"analyst": "timesheet", "reason": "табель"}')])
    meter = Meter()

    with metering(meter):
        await route(llm, "muse-spark-1.3", "Что в табеле?")

    assert meter.calls == [Call("dispatcher", "muse-spark-1.3", 10, 5)]


def test_the_ledger_sums_today_the_week_and_all_per_company(tmp_path):
    ledger = Ledger(tmp_path / "usage.sqlite3")
    ledger.record(company="c1", source="ask", question="q1", calls=[Call("attendance_analyst", "muse-spark-1.3", 1000, 100)])
    ledger.record(company="c1", source="voice", question="v", calls=[Call("voice", "muse-spark-1.2", 500, 50)])
    ledger.record(company="c2", source="ask", question="other", calls=[Call("skeptic", "unknown-model", 10, 10)])
    ledger.record(company="c1", source="ask", question="nothing", calls=[])

    summary = ledger.summary("c1")
    tomorrow = ledger.summary("c1", now=datetime.now(UTC) + timedelta(days=1))

    assert summary["today"]["runs"] == 2 and summary["all"]["runs"] == 2
    assert summary["today"]["input_tokens"] == 1500 and summary["today"]["estimated"] is False
    assert {row["role"] for row in summary["by_role"]} == {"attendance_analyst", "voice"}
    assert tomorrow["today"]["runs"] == 0 and tomorrow["week"]["runs"] == 2
    assert ledger.summary("c2")["all"]["estimated"] is True


def test_usage_reads_well_in_telegram(tmp_path):
    from nazar_agent.telegram.render import usage_text

    ledger = Ledger(tmp_path / "usage.sqlite3")
    ledger.record(company="c", source="telegram", question="q", calls=[
        Call("attendance_analyst", "muse-spark-1.3", 120_000, 4_000),
        Call("skeptic", "meta-models/muse-spark-open-1026", 9_000, 6_000),
    ])

    text = usage_text(ledger.summary("c"))

    assert text.startswith("💰 <b>Расходы на модели</b>")
    assert "запусков 1 · токенов 129 000 → 10 000" in text
    assert "(часть — оценка)" in text and "• скептик — meta-models/muse-spark-open-1026" in text
