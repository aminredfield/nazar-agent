"""The staged situations the Scenarios page plays, one set per new ability, across the kinds of
companies NAZAR serves: cafés, stores, cleaning teams, factories, offices.

Staged on purpose, and the page says so: the records are written for the scene, not taken
from the demo company. The photos were drawn by Muse Image (static/scenarios/photos.csv);
no real person is in them.
"""

from __future__ import annotations

from typing import Any

# The employee explains — a card with an open question; three replies the visitor can send, each in
# every window language: one the records fit, one they contradict, one they cannot settle.
EXPLAIN: dict[str, dict[str, Any]] = {
    "cafe-late": {
        "segment": "Café",
        "place": "Corner Café, 15 Market Street",
        "employee": "Lena Ortiz",
        "finding": "Late three times this week; on Thursday the first check-in was at 10:41 for a 10:00 shift.",
        "records": [
            "Shift: 10:00–22:00, Wednesday to Sunday.",
            "Thu 29.10: check-in 10:41 inside the café's zone, live selfie; check-out 22:02.",
            "Thu 29.10: her phone first joined the café's Wi-Fi at 10:39.",
            "Wed 28.10: check-in 10:18. Fri 30.10: check-in 10:12.",
        ],
        # What the page shows in Russian and Uzbek; the model reads the English above.
        "shown": {
            "ru": {"finding": "Три опоздания за неделю; в четверг первая отметка в 10:41 при смене с 10:00.",
                   "records": ["Смена: 10:00–22:00, со среды по воскресенье.",
                               "Чт 29.10: отметка 10:41 в зоне кафе, живое селфи; уход 22:02.",
                               "Чт 29.10: её телефон впервые подключился к Wi-Fi кафе в 10:39.",
                               "Ср 28.10: отметка 10:18. Пт 30.10: отметка 10:12."]},
            "uz": {"finding": "Hafta davomida uch marta kechikkan; payshanba kuni 10:00 dagi smenaga birinchi belgi 10:41 da.",
                   "records": ["Smena: 10:00–22:00, chorshanbadan yakshanbagacha.",
                               "Pa 29.10: belgi 10:41, kafe hududida, jonli selfi; chiqish 22:02.",
                               "Pa 29.10: telefoni kafe Wi-Fi siga birinchi marta 10:39 da ulangan.",
                               "Ch 28.10: belgi 10:18. Ju 30.10: belgi 10:12."]},
        },
        "replies": [
            {"expect": "consistent", "text": {
                "en": "The bus was late on Thursday, I got there at 10:40. Sorry.",
                "ru": "В четверг автобус опоздал, я пришла в 10:40. Извините.",
                "uz": "Payshanba kuni avtobus kechikdi, 10:40 da yetib keldim. Kechirasiz.",
            }},
            {"expect": "contradicts", "text": {
                "en": "I was there from ten, the app just didn't work in the morning.",
                "ru": "Я была на месте с десяти, просто приложение утром не работало.",
                "uz": "Soat o'ndan beri joyimda edim, ertalab ilova ishlamadi.",
            }},
            {"expect": "cannot_tell", "text": {
                "en": "The manager asked me to pick up milk for the café on the way in.",
                "ru": "Управляющий попросил меня по дороге купить молоко для кафе.",
                "uz": "Boshqaruvchi yo'lda kafe uchun sut olib kelishimni so'ragan edi.",
            }},
        ],
    },
    "store-screen": {
        "segment": "Store",
        "place": "Fresh Market, Northside",
        "employee": "Owen Edwards",
        "finding": "Saturday's check-in photo looks like a photo of a phone screen, sent from a colleague's phone.",
        "records": [
            "Sat 24.10: check-in 09:01 from Maria Lopez's phone, a minute after her own check-in.",
            "The photo inspector saw a phone screen showing Owen's registration photo.",
            "GPS inside the store's zone. No check-out for Owen; Maria checked out at 18:05.",
            "Owen's own phone: last check-in on Friday 23.10.",
        ],
        "shown": {
            "ru": {"finding": "Фото отметки в субботу похоже на снимок экрана телефона и отправлено с телефона коллеги.",
                   "records": ["Сб 24.10: отметка в 09:01 с телефона Марии Лопес, через минуту после её собственной.",
                               "Инспектор фото увидел экран телефона с регистрационным фото Оуэна.",
                               "GPS в зоне магазина. Ухода у Оуэна нет; Мария ушла в 18:05.",
                               "Собственный телефон Оуэна: последняя отметка в пятницу 23.10."]},
            "uz": {"finding": "Shanba kungi belgi surati telefon ekranining surati kabi va hamkasbining telefonidan yuborilgan.",
                   "records": ["Sh 24.10: belgi 09:01 da Maria Lopezning telefonidan, uning o'z belgisidan bir daqiqa keyin.",
                               "Surat inspektori Owenning ro'yxatdagi surati ko'rsatilgan telefon ekranini ko'rdi.",
                               "GPS do'kon hududida. Owenda chiqish yo'q; Maria 18:05 da chiqqan.",
                               "Owenning o'z telefoni: oxirgi belgi juma 23.10 da."]},
        },
        "replies": [
            {"expect": "consistent", "text": {
                "en": "I didn't come in on Saturday. Maria checked me in herself.",
                "ru": "В субботу я не выходил. Мария отметила меня сама.",
                "uz": "Shanba kuni ishga chiqmadim. Maria meni o'zi belgilab qo'ygan.",
            }},
            {"expect": "contradicts", "text": {
                "en": "I checked in myself, from my own phone.",
                "ru": "Я отметился сам, со своего телефона.",
                "uz": "O'zim, o'z telefonimdan belgilandim.",
            }},
            {"expect": "cannot_tell", "text": {
                "en": "I don't remember Saturday, it was a busy week.",
                "ru": "Не помню субботу, неделя была тяжёлая.",
                "uz": "Shanbani eslay olmayman, hafta og'ir o'tdi.",
            }},
        ],
    },
    "cleaning-address": {
        "segment": "Cleaning",
        "place": "Client: 12 Oak Lane, apt 48",
        "employee": "Daniel Morgan",
        "finding": "Tuesday's check-in for the job at the client's flat was 6.3 km away from it.",
        "records": [
            "Tue 27.10: job at 12 Oak Lane, apt 48, 14:00–18:00.",
            "Check-in 14:02 at a point 6.3 km from the address, GPS accuracy 8 m; check-out 17:55 at the same point.",
            "That point is the client's second address, 5 Liberty Plaza, where the team worked twice in October.",
        ],
        "shown": {
            "ru": {"finding": "Во вторник отметка на заказе в квартире клиента была в 6,3 км от адреса.",
                   "records": ["Вт 27.10: заказ на 12 Oak Lane, кв. 48, 14:00–18:00.",
                               "Отметка в 14:02 в 6,3 км от адреса, точность GPS 8 м; уход в 17:55 в той же точке.",
                               "Эта точка — второй адрес клиента, 5 Liberty Plaza, где команда работала дважды в октябре."]},
            "uz": {"finding": "Seshanba kuni mijoz kvartirasidagi buyurtmada belgi manzildan 6,3 km uzoqda bo'lgan.",
                   "records": ["Se 27.10: 12 Oak Lane, 48-xonadondagi buyurtma, 14:00–18:00.",
                               "Belgi 14:02 da manzildan 6,3 km uzoqdagi nuqtada, GPS aniqligi 8 m; chiqish 17:55 da shu nuqtada.",
                               "Bu nuqta — mijozning ikkinchi manzili, 5 Liberty Plaza, jamoa oktyabrda u yerda ikki marta ishlagan."]},
        },
        "replies": [
            {"expect": "consistent", "text": {
                "en": "The client asked us to clean their office at 5 Liberty Plaza that day instead.",
                "ru": "Клиент попросил в тот день убрать вместо квартиры его офис на 5 Liberty Plaza.",
                "uz": "Mijoz o'sha kuni kvartira o'rniga 5 Liberty Plaza dagi ofisini tozalashni so'radi.",
            }},
            {"expect": "contradicts", "text": {
                "en": "I was at the flat on 12 Oak Lane all day.",
                "ru": "Я весь день был в квартире на 12 Oak Lane.",
                "uz": "Kun bo'yi 12 Oak Lane dagi kvartirada edim.",
            }},
            {"expect": "cannot_tell", "text": {
                "en": "I don't remember Tuesday, we had a lot of jobs that week.",
                "ru": "Не помню вторник, на той неделе было много заказов.",
                "uz": "Seshanbani eslay olmayman, o'sha hafta buyurtmalar ko'p edi.",
            }},
        ],
    },
}

# A company from one picture — the owner's own schedule, as they keep it.
ROSTER: dict[str, dict[str, str]] = {
    "excel": {"title": "An Excel schedule", "file": "roster-excel.png", "note": "Two stores and a café, eight people."},
    "chat": {"title": "A message in a chat", "file": "roster-chat.png", "note": "How owners really send it: shorthand, two languages, no table."},
}

# A photo of the shift — who is in it, against who checked in.
HEADCOUNT: dict[str, dict[str, Any]] = {
    "cafe": {"segment": "Café", "place": "Corner Café, morning shift", "file": "shift-cafe.jpg",
             "checked_in": ["Jake Reynolds", "Zara Hughes", "Frank Sullivan", "Laura Hill",
                            "Shawn Miller", "Sophie Martin", "Blake Nelson"]},
    "factory": {"segment": "Factory", "place": "Workshop 2, night shift", "file": "shift-factory.jpg",
                "checked_in": ["Luke Holmes", "Gina Price", "Seth Abbott", "Nina Richards", "Andy Kim",
                               "Dana Brooks", "Josh Thompson", "Maria Lopez", "Owen Edwards"]},
    "store": {"segment": "Store", "place": "Fresh Market, Riverside", "file": "shift-store.jpg",
              "checked_in": ["Andy Kim", "Dana Brooks", "Sophie Martin", "Owen Edwards"]},
    "cleaning": {"segment": "Cleaning", "place": "Client: office lobby, 5 Liberty Plaza", "file": "shift-cleaning.jpg",
                 "checked_in": ["Daniel Morgan", "Zara Hughes", "Frank Sullivan", "Gina Price",
                                "Jake Reynolds", "Nina Richards"]},
}

# The place behind a check-in — its earlier selfies against a new one whose GPS says "here".
PLACES: dict[str, dict[str, Any]] = {
    "cafe": {"segment": "Café", "place": "Corner Café, 15 Market Street",
             "references": ["place-cafe-1.jpg", "place-cafe-2.jpg"],
             "candidates": [
                 {"file": "place-cafe-new.jpg", "who": "Jake Reynolds", "truth": True},
                 {"file": "place-bedroom.jpg", "who": "Zara Hughes", "truth": False},
                 {"file": "place-store-1.jpg", "who": "Frank Sullivan", "truth": False},
             ]},
    "store": {"segment": "Store", "place": "Fresh Market, Riverside",
              "references": ["place-store-1.jpg", "place-store-2.jpg", "place-store-3.jpg"],
              "candidates": [
                  {"file": "place-store-new.jpg", "who": "Sophie Martin", "truth": True},
                  {"file": "place-kitchen.jpg", "who": "Owen Edwards", "truth": False},
                  {"file": "place-other-store.jpg", "who": "Dana Brooks", "truth": False},
              ]},
}
# What the phone said for every candidate: the scene is about photos that GPS does not catch.
PLACE_GPS = "GPS inside the place's zone, accuracy 4 m"
