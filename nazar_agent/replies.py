"""Short answers the dispatcher gives without the analyst, in the owner's language.

Fixed text, not a model's: a greeting, something off-topic or forbidden
always gets the same honest answer and a word on what the assistant can do,
and nothing in the question can talk it into more.
"""

from __future__ import annotations

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
            "как проверенную, поправить или аннулировать смену, поменять график объекта, и каждое вы подтверждаете сами."
        ),
        "uz": (
            "Buni men qila olmayman — bu NAZAR kabinetida qilinadi. Men faqat shunday amallarni taklif qilaman: belgini "
            "tekshirilgan deb belgilash, smenani tuzatish yoki bekor qilish, obyekt jadvalini o'zgartirish — har birini "
            "o'zingiz tasdiqlaysiz."
        ),
        "uz-cyrl": (
            "Буни мен қила олмайман — бу NAZAR кабинетида қилинади. Мен фақат шундай амалларни таклиф қиламан: белгини "
            "текширилган деб белгилаш, сменани тузатиш ёки бекор қилиш, объект жадвалини ўзгартириш — ҳар бирини "
            "ўзингиз тасдиқлайсиз."
        ),
        "en": (
            "I can't do that — it is done in the NAZAR dashboard. The only actions I propose are marking a check-in as "
            "reviewed, correcting or voiding a shift and changing a site's schedule, and you confirm each one yourself."
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
