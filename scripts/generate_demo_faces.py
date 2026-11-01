"""Generates the faces of the demo companies' employees with Meta Muse Image.

Every demo employee is a fictional person: a name, an age and a look, and a
portrait made for exactly that person. Web and image search are switched off,
so nothing is taken from other people's photos. Each candidate is saved with
its prompt, and the chosen ones go into a provenance file (faces.csv) next to
the photos: model, date, prompt, sha256.

    python scripts/generate_demo_faces.py --out candidates/ --n 2
    python scripts/generate_demo_faces.py --out candidates/ --only mike_turner
    python scripts/generate_demo_faces.py --pick candidates/ --into demo-faces/ \
        --processing "downscaled to 720x720" mike_turner=b adam_novak=a ...
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


STROYKA = "Northline Builders"
SHOP = "Fresh Market"

PEOPLE = (
    Person("mike_turner", "Mike Turner", "man", 46, "foreman, short greying hair, trimmed black mustache, broad face, dark work jacket", STROYKA),
    Person("adam_novak", "Adam Novak", "man", 28, "short black hair, light stubble, grey hoodie", STROYKA),
    Person("ben_carter", "Ben Carter", "man", 34, "close-cropped hair, round face, short black beard, checked flannel shirt", STROYKA),
    Person("ian_foster", "Ian Foster", "man", 52, "balding, grey at the temples, tanned weathered skin, olive work jacket", STROYKA),
    Person("nora_sanders", "Nora Sanders", "woman", 38, "hair under a simple patterned headscarf, warm brown eyes, plain dark blouse", STROYKA),
    Person("sam_ellis", "Sam Ellis", "man", 24, "wavy black hair, slim face, clean-shaven, navy windbreaker", STROYKA),
    Person("tom_harris", "Tom Harris", "man", 31, "buzz cut, angular jaw, black t-shirt", STROYKA),
    Person("grace_ingram", "Grace Ingram", "woman", 44, "dark hair in a bun, round face, light knitted cardigan", STROYKA),
    Person("megan_young", "Megan Young", "woman", 27, "long dark hair in a ponytail, thin eyebrows, beige sweater", STROYKA),
    Person("fiona_carter", "Fiona Carter", "woman", 35, "headscarf tied at the back, oval face, dark green top", STROYKA),
    Person("daniel_morgan", "Daniel Morgan", "man", 49, "foreman, thick black mustache, short greying hair, brown jacket", STROYKA),
    Person("henry_ross", "Henry Ross", "man", 57, "grey hair, deep lines on the face, quilted work vest", STROYKA),
    Person("alex_baker", "Alex Baker", "man", 22, "youthful face, thin mustache, short hair, red and black flannel shirt", STROYKA),
    Person("zoe_nash", "Zoe Nash", "woman", 41, "short bob haircut, thin-rimmed glasses, grey work shirt", STROYKA),
    Person("kate_palmer", "Kate Palmer", "woman", 30, "long straight black hair, wide cheekbones, white blouse", STROYKA),
    Person("diana_evans", "Diana Evans", "woman", 48, "headscarf, smile lines, dark blue cardigan", STROYKA),
    Person("andy_kim", "Азиз Каримов", "man", 33, "neat short haircut, clean-shaven, light blue shirt", SHOP),
    Person("dana_brooks", "Дильноза Юсупова", "woman", 26, "long wavy dark hair, light makeup, pink blouse", SHOP),
    Person("josh_thompson", "Жасур Тошматов", "man", 29, "short neat beard, dark polo shirt", SHOP),
    Person("nina_richards", "Нилуфар Рахимова", "woman", 37, "hair pulled back, small earrings, grey blazer", SHOP),
    Person("seth_abbott", "Сардор Абдуллаев", "man", 42, "receding hairline, black mustache, white shirt", SHOP),
    Person("maria_lopez", "Мадина Исмаилова", "woman", 23, "straight bangs, round face, mint green top", SHOP),
    Person("blake_nelson", "Бехзод Назаров", "man", 38, "dark-rimmed glasses, short hair, navy sweater", SHOP),
    Person("gina_price", "Гульнора Саидова", "woman", 50, "short greying hair, burgundy cardigan", SHOP),
    Person("owen_edwards", "Отабек Эргашев", "man", 25, "curly black hair, clean-shaven, black shirt", SHOP),
    Person("zara_hughes", "Зарина Хакимова", "woman", 31, "light beige headscarf, oval face, beige top", SHOP),
    Person("shawn_miller", "Шахзод Умаров", "man", 27, "short undercut haircut, light stubble, green hoodie", SHOP),
    Person("sophie_martin", "Севара Мирзаева", "woman", 34, "shoulder-length dark hair, white shirt", SHOP),
    Person("luke_holmes", "Улугбек Холматов", "man", 55, "grey hair, wrinkles, brown vest over a shirt", SHOP),
    Person("laura_hill", "Лола Ахмедова", "woman", 45, "hair in a low bun, glasses, dark dress", SHOP),
    Person("frank_sullivan", "Фаррух Султанов", "man", 36, "thick eyebrows, full short beard, grey t-shirt", SHOP),
    Person("jake_reynolds", "Джавохир Раупов", "man", 21, "youthful, short side-parted hair, dark green apron over a white t-shirt", SHOP),
)

BY_SLUG = {person.slug: person for person in PEOPLE}


# Face structure, spread across each company. Without it the model draws one
# typical face per age and gender, and NAZAR's own face recognition took
# different people of the first set for the same person (similarity up to
# 0.71 against a match threshold of 0.45).
FACES = {
    "mike_turner": "a heavyset round full face, a broad flat nose, narrow eyes with epicanthic folds, dark tanned skin",
    "adam_novak": "an oval face, a wide upturned nose, round cheeks, close-set eyes, a cleft chin, fair skin with freckles",
    "ben_carter": "a square jaw, a thick neck, a wide nose, small eyes under a heavy brow, medium-brown skin",
    "ian_foster": "a gaunt thin face, hollow cheeks, a large hooked nose, deep wrinkles, very tanned skin",
    "nora_sanders": "a round soft face, a small upturned nose, narrow almond eyes with epicanthic folds, fair skin, rosy cheeks",
    "sam_ellis": "an oval face, large round eyes, a thin straight nose, full lips, olive skin",
    "tom_harris": "a triangular face with a wide forehead and a narrow pointed chin, a long thin nose, close-set light-brown eyes, pale skin, a small scar through the left eyebrow",
    "grace_ingram": "a wide face with full cheeks, a broad nose, thick eyebrows, a double chin, medium-brown skin",
    "megan_young": "a heart-shaped face, large dark eyes, a thin arched nose, pale skin, a mole above the lip",
    "fiona_carter": "a long face, a prominent aquiline nose, deep-set hazel eyes, thin lips, olive skin",
    "daniel_morgan": "a long rectangular face, a large straight nose, bushy eyebrows, light olive skin",
    "henry_ross": "a broad square face, a heavy jaw, a wide flat nose, puffy eyelids, a full grey beard",
    "alex_baker": "a heart-shaped face, large protruding ears, big round dark eyes, thick black eyebrows, olive skin",
    "zoe_nash": "a square face, a strong jaw, a wide mouth, small eyes, light skin with freckles",
    "kate_palmer": "a narrow oval face, East Asian features, a flat nose bridge, high cheekbones, very fair skin",
    "diana_evans": "a long narrow face, a pointed chin, a small straight nose, deep-set eyes, fair skin",
    "andy_kim": "a wide square face, a short neck, a flat broad nose, narrow eyes, medium-brown skin",
    "dana_brooks": "a long narrow face, large almond eyes, a long straight nose, very fair skin",
    "josh_thompson": "a round face, East Asian features, a small nose, wide cheekbones",
    "nina_richards": "an angular face, high cheekbones, a thin aquiline nose, deep-set eyes, olive skin",
    "seth_abbott": "a heavyset broad face, a double chin, thick lips, tanned skin",
    "maria_lopez": "a round face, East Asian features, a flat nose bridge, a small mouth, fair skin",
    "blake_nelson": "a long thin face, a large hooked nose, thick eyebrows, olive skin",
    "gina_price": "a round wide face, small eyes with epicanthic folds, deep smile lines, tanned skin",
    "owen_edwards": "a narrow face, large dark eyes, a long nose, olive skin",
    "zara_hughes": "a square jaw, wide-set eyes, a broad nose, dark tanned skin",
    "shawn_miller": "a round chubby face, small eyes, a button nose, fair skin",
    "sophie_martin": "a heart-shaped face, freckles, thin lips, light brown eyes, light skin",
    "luke_holmes": "a long face, East Asian features, a sparse grey goatee, deep wrinkles",
    "laura_hill": "a wide face with prominent cheekbones, thick eyebrows, full lips, olive skin",
    "frank_sullivan": "a square heavy face, a large nose, dark skin",
    "jake_reynolds": "a square face, a strong chin, a wide nose, dark tanned skin, a thin mustache",
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
