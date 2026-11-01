from nazar_agent.findings.models import AnalystReport, Claim, EvidenceRef, Fact, Finding
from nazar_agent.findings.pipeline import report_in_uzbek_latin
from nazar_agent.latin import fix_uzbek_latin, to_latin


def test_names_meet_in_one_spelling():
    assert to_latin("Khasanov") == to_latin("Хасанов") == to_latin("Xasanov")


def test_a_models_umlauts_become_uzbek_apostrophes():
    assert fix_uzbek_latin("körsatilgan") == "ko'rsatilgan"
    assert fix_uzbek_latin("Ğarbiy bōlim, toǵri") == "G'arbiy bo'lim, tog'ri"
    assert fix_uzbek_latin("bo'lgan edi") == "bo'lgan edi"  # already right: untouched


def test_the_report_is_fixed_everywhere_the_owner_reads():
    ref = EvidenceRef(kind="employee", id="e1")
    report = AnalystReport(
        answer=[Fact(text="körsatilgan", evidence=[ref], claims=[Claim(ref=ref, field="status", value="absent")])],
        findings=[Finding(
            title="Jadval köp tekshirilsin", subject_kind="object", subject_id="o1", subject_name="Maple Court",
            concern="setup", facts=[Fact(text="töliq", evidence=[ref], claims=[])],
            hypothesis="jadval notöğri", alternatives=["boshqa sabab bölishi mumkin"], confidence="medium",
        )],
        data_gaps=["kesilgan rö'yxat"],
        summary="Hech kim kelmagani körsatilmagan",
    )

    fixed = report_in_uzbek_latin(report)

    assert fixed.answer[0].text == "ko'rsatilgan"
    assert fixed.findings[0].title == "Jadval ko'p tekshirilsin"
    assert fixed.findings[0].facts[0].text == "to'liq"
    assert fixed.data_gaps == ["kesilgan ro'yxat"]
    assert fixed.findings[0].hypothesis == "jadval noto'g'ri"
    assert fixed.findings[0].alternatives == ["boshqa sabab bo'lishi mumkin"]
    assert fixed.summary == "Hech kim kelmagani ko'rsatilmagan"
    assert fixed.answer[0].claims[0].value == "absent"  # claims stay exactly as the tool returned them
    assert report.summary == "Hech kim kelmagani körsatilmagan"  # the original is not mutated
