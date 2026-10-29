"""Short answers the dispatcher gives without the analyst, in the owner's language.

Fixed text, not a model's: a greeting, something off-topic or forbidden
always gets the same honest answer and a word on what the assistant can do,
and nothing in the question can talk it into more.
"""

from __future__ import annotations

from nazar_agent.clarify import Choice, Clarification
from nazar_agent.roles.dispatcher import Lang, Request

CAN_DO: dict[Lang, str] = {
    "ru": (
        "Я помощник NAZAR по учёту рабочего времени. Могу ответить, кто сегодня на месте, кто опоздал или не пришёл, "
        "сколько часов отработал сотрудник, что проверить в табеле перед выплатой и какие отметки выглядят подозрительно. "
        "Спросите, например: «Кто сегодня опоздал?»"
    ),
    "uz": (
        "Men NAZAR ish vaqti hisobi bo'yicha yordamchiman. Bugun kim joyida, kim kechikdi yoki kelmadi, xodim necha soat "
        "ishladi, to'lovdan oldin tabelda nimani tekshirish kerak va qaysi belgilar shubhali — shularga javob bera olaman. "
        "Masalan, so'rang: «Bugun kim kechikdi?»"
    ),
    "uz-cyrl": (
        "Мен NAZAR иш вақти ҳисоби бўйича ёрдамчиман. Бугун ким жойида, ким кечикди ёки келмади, ходим неча соат "
        "ишлади, тўловдан олдин табелда нимани текшириш керак ва қайси белгилар шубҳали — шуларга жавоб бера оламан. "
        "Масалан, сўранг: «Бугун ким кечикди?»"
    ),
    "en": (
        "I am the NAZAR time-and-attendance assistant. I can tell you who is on site today, who was late or absent, "
        "how many hours someone worked, what to check in the timesheet before payday and which check-ins look "
        "suspicious. Try asking: “Who was late today?”"
    ),
}

OPENING: dict[str, dict[Lang, str]] = {
    "greeting": {"ru": "Здравствуйте!", "uz": "Assalomu alaykum!", "uz-cyrl": "Ассалому алайкум!", "en": "Hello!"},
    "off_topic": {
        "ru": "С этим я не помогу: я отвечаю только о сотрудниках, объектах и рабочем времени вашей компании.",
        "uz": "Bu bilan yordam bera olmayman: men faqat kompaniyangiz xodimlari, obyektlari va ish vaqti haqida javob beraman.",
        "uz-cyrl": "Бу билан ёрдам бера олмайман: мен фақат компаниянгиз ходимлари, объектлари ва иш вақти ҳақида жавоб бераман.",
        "en": "I can't help with that: I only answer about your company's employees, sites and working time.",
    },
    "unsupported": {
        "ru": (
            "Этого я сделать не могу — это делается в кабинете NAZAR. Я предлагаю только такие действия: пометить отметку "
            "как проверенную, поправить или аннулировать смену, поменять график объекта, открыть объект с группой и "
            "людьми — и каждое вы подтверждаете сами."
        ),
        "uz": (
            "Buni men qila olmayman — bu NAZAR kabinetida qilinadi. Men faqat shunday amallarni taklif qilaman: belgini "
            "tekshirilgan deb belgilash, smenani tuzatish yoki bekor qilish, obyekt jadvalini o'zgartirish, guruh va "
            "odamlar bilan yangi obyekt ochish — har birini o'zingiz tasdiqlaysiz."
        ),
        "uz-cyrl": (
            "Буни мен қила олмайман — бу NAZAR кабинетида қилинади. Мен фақат шундай амалларни таклиф қиламан: белгини "
            "текширилган деб белгилаш, сменани тузатиш ёки бекор қилиш, объект жадвалини ўзгартириш, гуруҳ ва "
            "одамлар билан янги объект очиш — ҳар бирини ўзингиз тасдиқлайсиз."
        ),
        "en": (
            "I can't do that — it is done in the NAZAR dashboard. The only actions I propose are marking a check-in as "
            "reviewed, correcting or voiding a shift, changing a site's schedule, opening a site with a group and its "
            "people — and you confirm each one yourself."
        ),
    },
    "secrets": {
        "ru": "Свои инструкции, пароли и ключи я не показываю.",
        "uz": "O'z ko'rsatmalarim, parollar va kalitlarni ko'rsatmayman.",
        "uz-cyrl": "Ўз кўрсатмаларим, пароллар ва калитларни кўрсатмайман.",
        "en": "I don't share my own setup, passwords or keys.",
    },
    "other_company": {
        "ru": "Я вижу только вашу компанию: данные других компаний мне недоступны, и показывать их я не буду.",
        "uz": "Men faqat sizning kompaniyangizni ko'raman: boshqa kompaniyalar ma'lumotlari menga ochiq emas va ularni ko'rsatmayman.",
        "uz-cyrl": "Мен фақат сизнинг компаниянгизни кўраман: бошқа компаниялар маълумотлари менга очиқ эмас ва уларни кўрсатмайман.",
        "en": "I only see your own company: other companies' data is closed to me, and I won't show it.",
    },
    "penalty": {
        "ru": (
            "Наказывать скопом и удалять отметки я не буду: штраф, неоплаченный день, подтверждённое нарушение или удаление — "
            "это решение по каждому случаю после проверки, и принимаете его вы. Могу показать, кто и насколько опоздал и что стоит проверить по каждому."
        ),
        "uz": (
            "Hammani birdaniga jazolamayman va belgilarni o'chirmayman: jarima, to'lanmagan kun, tasdiqlangan qoidabuzarlik yoki "
            "o'chirish — har bir holat bo'yicha tekshiruvdan keyingi qaror, uni siz qabul qilasiz. Kim va qancha kechikkanini, har biri bo'yicha nimani "
            "tekshirish kerakligini ko'rsatishim mumkin."
        ),
        "uz-cyrl": (
            "Ҳаммани бирданига жазоламайман ва белгиларни ўчирмайман: жарима, тўланмаган кун, тасдиқланган қоидабузарлик ёки "
            "ўчириш — ҳар бир ҳолат бўйича текширувдан кейинги қарор, уни сиз қабул қиласиз. Ким ва қанча кечикканини, ҳар бири бўйича нимани "
            "текшириш кераклигини кўрсатишим мумкин."
        ),
        "en": (
            "I won't penalise people in bulk or delete check-ins: a fine, an unpaid day, a confirmed violation or a deletion "
            "is a decision about each case after a check, and it is yours to make. I can show who was late and by how much, and what is worth "
            "checking for each of them."
        ),
    },
    "unclear": {
        "ru": "Не понял вопрос.",
        "uz": "Savolni tushunmadim.",
        "uz-cyrl": "Саволни тушунмадим.",
        "en": "I didn't understand the question.",
    },
}

FUTURE: dict[Lang, str] = {
    "ru": "За {period} отметок ещё нет — это ещё впереди.",
    "uz": "{period} uchun belgilar hali yo'q — bu hali oldinda.",
    "uz-cyrl": "{period} учун белгилар ҳали йўқ — бу ҳали олдинда.",
    "en": "There are no check-ins for {period} yet — that is still ahead.",
}


CHOOSE: dict[Lang, str] = {
    "ru": "Подходят несколько сотрудников: {names}. О ком вопрос? Выберите кнопкой.",
    "uz": "Bir nechta xodim mos keladi: {names}. Savol kim haqida? Tugma bilan tanlang.",
    "uz-cyrl": "Бир нечта ходим мос келади: {names}. Савол ким ҳақида? Тугма билан танланг.",
    "en": "Several employees fit: {names}. Who do you mean? Pick a button.",
}
NOT_FOUND: dict[Lang, str] = {
    "ru": "Не нашёл сотрудника «{name}» в NAZAR.",
    "uz": "NAZARda «{name}» xodimini topmadim.",
    "uz-cyrl": "NAZARда «{name}» ходимини топмадим.",
    "en": "I found no employee called “{name}” in NAZAR.",
}
MAYBE: dict[Lang, str] = {
    "ru": " Возможно, вы имели в виду: {names}.",
    "uz": " Balki siz {names} haqida so'ragandirsiz.",
    "uz-cyrl": " Балки сиз {names} ҳақида сўрагандирсиз.",
    "en": " Perhaps you meant {names}.",
}
CHECK_NAME: dict[Lang, str] = {
    "ru": " Проверьте, как он записан в списке сотрудников.",
    "uz": " Xodimlar ro'yxatida qanday yozilganini tekshiring.",
    "uz-cyrl": " Ходимлар рўйхатида қандай ёзилганини текширинг.",
    "en": " Check how the name is written in the employee list.",
}
# Questions offered as buttons after a short answer: what the assistant is good at.
SUGGESTIONS: dict[Lang, tuple[tuple[str, str], ...]] = {
    "ru": (("Кто сегодня опоздал", "Кто сегодня опоздал?"), ("Кто не пришёл", "Кто сегодня не пришёл на работу?"),
           ("Табель перед оплатой", "Что проверить в табеле перед оплатой за прошлую неделю?")),
    "uz": (("Bugun kim kechikdi", "Bugun kim kechikdi?"), ("Kim kelmadi", "Bugun kim ishga kelmadi?"),
           ("To'lovdan oldin tabel", "O'tgan hafta uchun to'lovdan oldin tabelda nimani tekshirish kerak?")),
    "uz-cyrl": (("Бугун ким кечикди", "Бугун ким кечикди?"), ("Ким келмади", "Бугун ким ишга келмади?"),
                ("Тўловдан олдин табел", "Ўтган ҳафта учун тўловдан олдин табелда нимани текшириш керак?")),
    "en": (("Who was late today", "Who was late today?"), ("Who is absent", "Who did not come to work today?"),
           ("Before payday", "What should I check in last week's timesheet before paying?")),
}
SUGGESTED_AFTER = {"greeting", "off_topic", "unsupported", "unclear", "future"}


def clarification(found: Clarification, lang: Lang) -> str:
    names = ", ".join(choice.label for choice in found.choices)
    if found.found:
        return CHOOSE[lang].format(name=found.name, names=names)
    return NOT_FOUND[lang].format(name=found.name) + (MAYBE[lang].format(names=names) if names else CHECK_NAME[lang])


def suggestions(request: Request) -> tuple[Choice, ...]:
    if request.intent not in SUGGESTED_AFTER:
        return ()
    return tuple(Choice(label, question) for label, question in SUGGESTIONS[request.lang])


def reply(request: Request) -> str:
    """The short answer to a request the analyst does not take."""
    lang = request.lang
    if request.intent == "future":
        opening = FUTURE[lang].format(period=_period_text(request))
    else:
        opening = OPENING[request.intent][lang]
    return f"{opening} {CAN_DO[lang]}"


def _period_text(request: Request) -> str:
    if request.period is None:
        return "?"
    first, last = request.period
    if first == last:
        return first.strftime("%d.%m.%Y")
    return f"{first.strftime('%d.%m.%Y')}–{last.strftime('%d.%m.%Y')}"
