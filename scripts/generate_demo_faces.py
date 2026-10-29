"""Generates the faces of the demo companies' employees with Meta Muse Image.

Every demo employee is a fictional person: a name, an age and a look, and a
portrait made for exactly that person. Web and image search are switched off,
so nothing is taken from other people's photos. Each candidate is saved with
its prompt, and the chosen ones go into a provenance file (faces.csv) next to
the photos: model, date, prompt, sha256.

    python scripts/generate_demo_faces.py --out candidates/ --n 2
    python scripts/generate_demo_faces.py --out candidates/ --only rustam_yuldashev
    python scripts/generate_demo_faces.py --pick candidates/ --into demo-faces/ \
        --processing "downscaled to 720x720" rustam_yuldashev=b azamat_normatov=a ...
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import csv
import hashlib
import json
import shutil
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

from nazar_agent.config import Settings

MODEL = "muse-image-1.0"
SIZE = "1024x1024"
RETRIES = 3
PARALLEL = 4

PROMPT = (
    "Photorealistic photo of a fictional {age}-year-old Uzbek {gender} with {face}; {look}. "
    "Taken with a smartphone for an employee profile: head and shoulders, facing "
    "the camera, eyes open, relaxed neutral expression, even indoor daylight, a "
    "plain light wall behind. An ordinary person, not a model and not a real or "
    "famous person. No text, no logos, no watermark."
)


@dataclass(frozen=True, slots=True)
class Person:
    slug: str
    name: str
    gender: str  # man | woman
    age: int
    look: str
    company: str


STROYKA = "Mustahkam Qurilish"
SHOP = "Bahor Market"

PEOPLE = (
    Person("rustam_yuldashev", "Rustam Yuldashev", "man", 46, "foreman, short greying hair, trimmed black mustache, broad face, dark work jacket", STROYKA),
    Person("azamat_normatov", "Azamat Normatov", "man", 28, "short black hair, light stubble, grey hoodie", STROYKA),
    Person("bobur_karimov", "Bobur Karimov", "man", 34, "close-cropped hair, round face, short black beard, checked flannel shirt", STROYKA),
    Person("ilhom_turaev", "Ilhom Turaev", "man", 52, "balding, grey at the temples, tanned weathered skin, olive work jacket", STROYKA),
    Person("nodira_safarova", "Nodira Safarova", "woman", 38, "hair under a simple patterned headscarf, warm brown eyes, plain dark blouse", STROYKA),
    Person("sherzod_alimov", "Sherzod Alimov", "man", 24, "wavy black hair, slim face, clean-shaven, navy windbreaker", STROYKA),
    Person("timur_hasanov", "Timur Hasanov", "man", 31, "buzz cut, angular jaw, black t-shirt", STROYKA),
    Person("gulchehra_ibragimova", "Gulchehra Ibragimova", "woman", 44, "dark hair in a bun, round face, light knitted cardigan", STROYKA),
    Person("mohira_yunusova", "Mohira Yunusova", "woman", 27, "long dark hair in a ponytail, thin eyebrows, beige sweater", STROYKA),
    Person("feruza_karimova", "Feruza Karimova", "woman", 35, "headscarf tied at the back, oval face, dark green top", STROYKA),
    Person("dilshod_mirzaev", "Dilshod Mirzaev", "man", 49, "foreman, thick black mustache, short greying hair, brown jacket", STROYKA),
    Person("hurshid_rahmonov", "Hurshid Rahmonov", "man", 57, "grey hair, deep lines on the face, quilted work vest", STROYKA),
    Person("anvar_bakirov", "Anvar Bakirov", "man", 22, "youthful face, thin mustache, short hair, red and black flannel shirt", STROYKA),
    Person("zuhra_nabieva", "Zuhra Nabieva", "woman", 41, "short bob haircut, thin-rimmed glasses, grey work shirt", STROYKA),
    Person("kamola_tashpulatova", "Kamola Tashpulatova", "woman", 30, "long straight black hair, wide cheekbones, white blouse", STROYKA),
    Person("dilfuza_ergasheva", "Dilfuza Ergasheva", "woman", 48, "headscarf, smile lines, dark blue cardigan", STROYKA),
    Person("aziz_karimov", "Азиз Каримов", "man", 33, "neat short haircut, clean-shaven, light blue shirt", SHOP),
    Person("dilnoza_yusupova", "Дильноза Юсупова", "woman", 26, "long wavy dark hair, light makeup, pink blouse", SHOP),
    Person("jasur_toshmatov", "Жасур Тошматов", "man", 29, "short neat beard, dark polo shirt", SHOP),
    Person("nilufar_rahimova", "Нилуфар Рахимова", "woman", 37, "hair pulled back, small earrings, grey blazer", SHOP),
    Person("sardor_abdullaev", "Сардор Абдуллаев", "man", 42, "receding hairline, black mustache, white shirt", SHOP),
    Person("madina_ismailova", "Мадина Исмаилова", "woman", 23, "straight bangs, round face, mint green top", SHOP),
    Person("behzod_nazarov", "Бехзод Назаров", "man", 38, "dark-rimmed glasses, short hair, navy sweater", SHOP),
    Person("gulnora_saidova", "Гульнора Саидова", "woman", 50, "short greying hair, burgundy cardigan", SHOP),
    Person("otabek_ergashev", "Отабек Эргашев", "man", 25, "curly black hair, clean-shaven, black shirt", SHOP),
    Person("zarina_hakimova", "Зарина Хакимова", "woman", 31, "light beige headscarf, oval face, beige top", SHOP),
    Person("shahzod_umarov", "Шахзод Умаров", "man", 27, "short undercut haircut, light stubble, green hoodie", SHOP),
    Person("sevara_mirzaeva", "Севара Мирзаева", "woman", 34, "shoulder-length dark hair, white shirt", SHOP),
    Person("ulugbek_holmatov", "Улугбек Холматов", "man", 55, "grey hair, wrinkles, brown vest over a shirt", SHOP),
    Person("lola_ahmedova", "Лола Ахмедова", "woman", 45, "hair in a low bun, glasses, dark dress", SHOP),
    Person("farrukh_sultanov", "Фаррух Султанов", "man", 36, "thick eyebrows, full short beard, grey t-shirt", SHOP),
    Person("javohir_raupov", "Джавохир Раупов", "man", 21, "youthful, short side-parted hair, dark green apron over a white t-shirt", SHOP),
)

BY_SLUG = {person.slug: person for person in PEOPLE}


# Face structure, spread across each company. Without it the model draws one
# typical face per age and gender, and NAZAR's own face recognition took
# different people of the first set for the same person (similarity up to
# 0.71 against a match threshold of 0.45).
FACES = {
    "rustam_yuldashev": "a heavyset round full face, a broad flat nose, narrow eyes with epicanthic folds, dark tanned skin",
    "azamat_normatov": "an oval face, a wide upturned nose, round cheeks, close-set eyes, a cleft chin, fair skin with freckles",
    "bobur_karimov": "a square jaw, a thick neck, a wide nose, small eyes under a heavy brow, medium-brown skin",
    "ilhom_turaev": "a gaunt thin face, hollow cheeks, a large hooked nose, deep wrinkles, very tanned skin",
    "nodira_safarova": "a round soft face, a small upturned nose, narrow almond eyes with epicanthic folds, fair skin, rosy cheeks",
    "sherzod_alimov": "an oval face, large round eyes, a thin straight nose, full lips, olive skin",
    "timur_hasanov": "a triangular face with a wide forehead and a narrow pointed chin, a long thin nose, close-set light-brown eyes, pale skin, a small scar through the left eyebrow",
    "gulchehra_ibragimova": "a wide face with full cheeks, a broad nose, thick eyebrows, a double chin, medium-brown skin",
    "mohira_yunusova": "a heart-shaped face, large dark eyes, a thin arched nose, pale skin, a mole above the lip",
    "feruza_karimova": "a long face, a prominent aquiline nose, deep-set hazel eyes, thin lips, olive skin",
    "dilshod_mirzaev": "a long rectangular face, a large straight nose, bushy eyebrows, light olive skin",
    "hurshid_rahmonov": "a broad square face, a heavy jaw, a wide flat nose, puffy eyelids, a full grey beard",
    "anvar_bakirov": "a heart-shaped face, large protruding ears, big round dark eyes, thick black eyebrows, olive skin",
    "zuhra_nabieva": "a square face, a strong jaw, a wide mouth, small eyes, light skin with freckles",
    "kamola_tashpulatova": "a narrow oval face, East Asian features, a flat nose bridge, high cheekbones, very fair skin",
    "dilfuza_ergasheva": "a long narrow face, a pointed chin, a small straight nose, deep-set eyes, fair skin",
    "aziz_karimov": "a wide square face, a short neck, a flat broad nose, narrow eyes, medium-brown skin",
    "dilnoza_yusupova": "a long narrow face, large almond eyes, a long straight nose, very fair skin",
    "jasur_toshmatov": "a round face, East Asian features, a small nose, wide cheekbones",
    "nilufar_rahimova": "an angular face, high cheekbones, a thin aquiline nose, deep-set eyes, olive skin",
    "sardor_abdullaev": "a heavyset broad face, a double chin, thick lips, tanned skin",
    "madina_ismailova": "a round face, East Asian features, a flat nose bridge, a small mouth, fair skin",
    "behzod_nazarov": "a long thin face, a large hooked nose, thick eyebrows, olive skin",
    "gulnora_saidova": "a round wide face, small eyes with epicanthic folds, deep smile lines, tanned skin",
    "otabek_ergashev": "a narrow face, large dark eyes, a long nose, olive skin",
    "zarina_hakimova": "a square jaw, wide-set eyes, a broad nose, dark tanned skin",
    "shahzod_umarov": "a round chubby face, small eyes, a button nose, fair skin",
    "sevara_mirzaeva": "a heart-shaped face, freckles, thin lips, light brown eyes, light skin",
    "ulugbek_holmatov": "a long face, East Asian features, a sparse grey goatee, deep wrinkles",
    "lola_ahmedova": "a wide face with prominent cheekbones, thick eyebrows, full lips, olive skin",
    "farrukh_sultanov": "a square heavy face, a large nose, dark skin",
    "javohir_raupov": "a square face, a strong chin, a wide nose, dark tanned skin, a thin mustache",
}


def prompt_of(person: Person) -> str:
    return PROMPT.format(age=person.age, gender=person.gender, face=FACES[person.slug], look=person.look)


async def generate(client: httpx.AsyncClient, base_url: str, person: Person, n: int) -> list[bytes]:
    body = {
        "model": MODEL,
        "prompt": prompt_of(person),
        "n": n,
        "size": SIZE,
        "output_format": "jpeg",
        "response_format": "b64_json",
        "tool_enablement": {"enable_image_search": False, "enable_web_search": False, "enable_shell": False},
    }
    for attempt in range(1, RETRIES + 1):
        response = await client.post(f"{base_url}/images/generations", json=body)
        if response.status_code == 503 and attempt < RETRIES:
            await asyncio.sleep(2**attempt)
            continue
        if response.status_code != 200:
            raise RuntimeError(f"{person.slug}: HTTP {response.status_code} {response.text[:300]}")
        return [base64.b64decode(item["b64_json"]) for item in response.json()["data"]]
    raise RuntimeError(f"{person.slug}: still 503 after {RETRIES} attempts")


async def generate_all(out: Path, people: list[Person], n: int) -> None:
    settings = Settings()
    out.mkdir(parents=True, exist_ok=True)
    headers = {"Authorization": f"Bearer {settings.model_api_key.get_secret_value()}"}
    parallel = asyncio.Semaphore(PARALLEL)

    async def one(client: httpx.AsyncClient, person: Person) -> None:
        async with parallel:
            images = await generate(client, settings.meta_base_url, person, n)
            generated_at = datetime.now(UTC).isoformat(timespec="seconds")
            for letter, image in zip("abcdefghij", images, strict=False):
                (out / f"{person.slug}_{letter}.jpg").write_bytes(image)
            (out / f"{person.slug}.json").write_text(
                json.dumps({"model": MODEL, "prompt": prompt_of(person), "generated_at": generated_at}, ensure_ascii=False),
                encoding="utf-8",
            )
            print(f"{person.slug}: {len(images)}", flush=True)

    async with httpx.AsyncClient(headers=headers, timeout=180) as client:
        await asyncio.gather(*(one(client, person) for person in people))


def pick(candidates: Path, into: Path, choices: list[str], processing: str) -> None:
    """Copies the chosen candidate of each person and writes faces.csv."""
    into.mkdir(parents=True, exist_ok=True)
    rows = []
    for choice in choices:
        slug, letter = choice.split("=")
        person = BY_SLUG[slug]
        meta = json.loads((candidates / f"{slug}.json").read_text(encoding="utf-8"))
        target = into / f"{slug}.jpg"
        shutil.copyfile(candidates / f"{slug}_{letter}.jpg", target)
        rows.append(
            {
                "file": target.name,
                "name": person.name,
                "company": person.company,
                "gender": person.gender,
                "age": person.age,
                "source": "Meta Model API, image and web search off",
                "model": meta["model"],
                "generated_at": meta["generated_at"],
                "prompt": meta["prompt"],
                "processing": processing,
                "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            }
        )
    with (into / "faces.csv").open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, help="directory for the candidates")
    parser.add_argument("--n", type=int, default=2, help="candidates per person, 1-10 ($0.01 each)")
    parser.add_argument("--only", nargs="*", default=None, help="slugs to (re)generate")
    parser.add_argument("--pick", type=Path, help="directory with the candidates to pick from")
    parser.add_argument("--into", type=Path, help="where the chosen photos and faces.csv go")
    parser.add_argument("--processing", default="none", help="what was done to the photos after generation")
    parser.add_argument("choices", nargs="*", help="slug=letter, one per person")
    args = parser.parse_args()

    if args.pick:
        pick(args.pick, args.into, args.choices, args.processing)
        return
    unknown = [slug for slug in args.only or [] if slug not in BY_SLUG]
    if unknown:
        sys.exit(f"unknown slugs: {', '.join(unknown)}")
    people = [BY_SLUG[slug] for slug in args.only] if args.only else list(PEOPLE)
    asyncio.run(generate_all(args.out, people, args.n))


if __name__ == "__main__":
    main()
