"""The staged situations the Scenarios page plays, one set per new ability, across the kinds of
companies NAZAR serves: cafés, stores, cleaning teams, factories, offices.

Staged on purpose, and the page says so: the records are written for the scene, not taken
from the demo company. The photos were drawn by Muse Image (static/scenarios/photos.csv);
no real person is in them.
"""

from __future__ import annotations

from typing import Any

# The employee explains — a card with an open question, the employee's own answer.
EXPLAIN: dict[str, dict[str, Any]] = {
    "cafe-late": {
        "segment": "Café",
        "place": "Café Lola, Amir Temur 15",
        "employee": "Laylo Usmonova",
        "finding": "Late three times this week; on Thursday the first check-in was at 10:41 for a 10:00 shift.",
        "records": [
            "Shift: 10:00–22:00, Wednesday to Sunday.",
            "Thu 29.10: check-in 10:41 inside the café's zone, live selfie; check-out 22:02.",
            "Thu 29.10: her phone first joined the café's Wi-Fi at 10:39.",
            "Wed 28.10: check-in 10:18. Fri 30.10: check-in 10:12.",
        ],
        "replies": [
            {"lang": "en", "text": "I was there from ten, the app just did not work in the morning.", "expect": "contradicts"},
            {"lang": "uz", "text": "Avtobus kech qoldi, soat 10:40 da yetib keldim. Kechirasiz.", "expect": "consistent"},
            {"lang": "ru", "text": "В четверг я была на больничном, это не я отмечалась.", "expect": "contradicts"},
        ],
    },
    "store-screen": {
        "segment": "Store",
        "place": "Bahor Market, Yunusobod",
        "employee": "Otabek Ergashev",
        "finding": "Saturday's check-in photo looks like a photo of a phone screen, sent from a colleague's phone.",
        "records": [
            "Sat 24.10: check-in 09:01 from Madina Ismailova's phone, a minute after her own check-in.",
            "The photo inspector saw a phone screen showing Otabek's registration photo.",
            "GPS inside the store's zone. No check-out for Otabek; Madina checked out at 18:05.",
            "Otabek's own phone: last check-in on Friday 23.10.",
        ],
        "replies": [
            {"lang": "en", "text": "My phone broke on Saturday. Madina checked me in from hers, but I was in the store all day.",
             "expect": "cannot_tell"},
            {"lang": "ru", "text": "Я отметился сам, со своего телефона.", "expect": "contradicts"},
            {"lang": "uz", "text": "Shanba kuni ishga kelmaganman, Madina o'zi belgilab qo'ygan.", "expect": "consistent"},
        ],
    },
    "cleaning-address": {
        "segment": "Cleaning",
        "place": "Client: Yunusobod 12, flat 48",
        "employee": "Dilshod Mirzaev",
        "finding": "Tuesday's check-in for the job at the client's flat was 6.3 km away from it.",
        "records": [
            "Tue 27.10: job at Yunusobod 12, flat 48, 14:00–18:00.",
            "Check-in 14:02 at a point 6.3 km from the address, GPS accuracy 8 m; check-out 17:55 at the same point.",
            "That point is the client's second address, Mustaqillik 5, where the team worked twice in October.",
        ],
        "replies": [
            {"lang": "en", "text": "The client asked us to clean their office at Mustaqillik 5 that day instead.", "expect": "consistent"},
            {"lang": "ru", "text": "Я весь день был на квартире у клиента.", "expect": "contradicts"},
            {"lang": "uz", "text": "GPS noto'g'ri ko'rsatdi, men manzilda edim.", "expect": "contradicts"},
        ],
    },
}

# A company from one picture — the owner's own schedule, as they keep it.
ROSTER: dict[str, dict[str, str]] = {
    "excel": {"title": "An Excel schedule", "file": "roster-excel.png", "note": "Two stores and a café, eight people."},
    "chat": {"title": "A message in a chat", "file": "roster-chat.png", "note": "How owners really send it: mixed languages, no table."},
}

# A photo of the shift — who is in it, against who checked in.
HEADCOUNT: dict[str, dict[str, Any]] = {
    "cafe": {"segment": "Café", "place": "Café Lola, morning shift", "file": "shift-cafe.jpg",
             "checked_in": ["Javohir Raupov", "Zarina Hakimova", "Farrukh Sultanov", "Lola Ahmedova",
                            "Shahzod Umarov", "Sevara Mirzaeva", "Behzod Nazarov"]},
    "factory": {"segment": "Factory", "place": "Workshop 2, night shift", "file": "shift-factory.jpg",
                "checked_in": ["Ulugbek Holmatov", "Gulnora Saidova", "Sardor Abdullaev", "Nilufar Rahimova", "Aziz Karimov",
                               "Dilnoza Yusupova", "Jasur Toshmatov", "Madina Ismailova", "Otabek Ergashev"]},
    "store": {"segment": "Store", "place": "Bahor Market, Chilonzor", "file": "shift-store.jpg",
              "checked_in": ["Aziz Karimov", "Dilnoza Yusupova", "Sevara Mirzaeva", "Otabek Ergashev"]},
    "cleaning": {"segment": "Cleaning", "place": "Client: office lobby, Mustaqillik 5", "file": "shift-cleaning.jpg",
                 "checked_in": ["Dilshod Mirzaev", "Zarina Hakimova", "Farrukh Sultanov", "Gulnora Saidova",
                                "Javohir Raupov", "Nilufar Rahimova"]},
}

# The place behind a check-in — its earlier selfies against a new one whose GPS says "here".
PLACES: dict[str, dict[str, Any]] = {
    "cafe": {"segment": "Café", "place": "Café Lola, Amir Temur 15",
             "references": ["place-cafe-1.jpg", "place-cafe-2.jpg"],
             "candidates": [
                 {"file": "place-cafe-new.jpg", "who": "Javohir Raupov", "truth": True},
                 {"file": "place-bedroom.jpg", "who": "Zarina Hakimova", "truth": False},
                 {"file": "place-store-1.jpg", "who": "Farrukh Sultanov", "truth": False},
             ]},
    "store": {"segment": "Store", "place": "Bahor Market, Chilonzor",
              "references": ["place-store-1.jpg", "place-store-2.jpg", "place-store-3.jpg"],
              "candidates": [
                  {"file": "place-store-new.jpg", "who": "Sevara Mirzaeva", "truth": True},
                  {"file": "place-kitchen.jpg", "who": "Otabek Ergashev", "truth": False},
                  {"file": "place-other-store.jpg", "who": "Dilnoza Yusupova", "truth": False},
              ]},
}
# What the phone said for every candidate: the scene is about photos that GPS does not catch.
PLACE_GPS = "GPS inside the place's zone, accuracy 4 m"
