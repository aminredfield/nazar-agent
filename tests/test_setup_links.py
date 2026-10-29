"""A site's point from a map link or two numbers; short links opened only on map hosts."""

import httpx
import pytest

from nazar_agent.setup.links import LinkError, Point, is_map_link, point_in, resolve

OLMAZOR = (41.285612, 69.203411)


@pytest.mark.parametrize("link", [
    # Google: the pin (!3d!4d) wins over the map's centre (@).
    "https://www.google.com/maps/place/Olmazor/@41.2901,69.2101,17z/data=!3m1!4b1!4m6!3m5!1s0x0:0x0!8m2!3d41.285612!4d69.203411!16s",
    "https://www.google.com/maps?q=41.285612,69.203411",
    "https://maps.google.com/?ll=41.285612,69.203411&z=16",
    "https://www.google.com/maps/search/?api=1&query=41.285612%2C69.203411",
    "https://www.google.com/maps?q=loc:41.285612,69.203411",
    "https://www.google.uz/maps/@41.285612,69.203411,15z",
    # Yandex: longitude first; the pin (pt) wins over the centre (ll).
    "https://yandex.uz/maps/10335/tashkent/?ll=69.2101%2C41.2901&pt=69.203411%2C41.285612&z=16",
    "https://yandex.ru/maps/?whatshere%5Bpoint%5D=69.203411%2C41.285612&whatshere%5Bzoom%5D=17",
    "https://yandex.uz/maps/?ll=69.203411,41.285612&z=17",
    # 2GIS: longitude first too.
    "https://2gis.uz/tashkent/geo/70030076180461371?m=69.203411%2C41.285612%2F16",
    # Two numbers, as copied from a map.
    "41.285612, 69.203411",
    "41.285612 69.203411",
])
def test_the_point_of_a_full_link_or_two_numbers(link):
    assert point_in(link).rounded() == OLMAZOR


@pytest.mark.parametrize("text", [
    "https://www.google.com/maps/place/Olmazor",  # a name, no point
    "https://example.com/?ll=41.28,69.20",  # not a map
    "Ташкент, Чиланзар",
    "0, 0",
    "95.1, 69.2",  # no such latitude
])
def test_no_point_is_not_guessed(text):
    assert point_in(text) is None


def redirects(chain: dict[str, str], seen: list[str]) -> httpx.AsyncClient:
    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        target = chain.get(str(request.url))
        return httpx.Response(302, headers={"location": target}) if target else httpx.Response(200, text="<html>")

    return httpx.AsyncClient(transport=httpx.MockTransport(answer))


async def test_a_short_google_link_is_opened_through_the_cookie_page():
    seen: list[str] = []
    consent = "https://consent.google.com/ml?continue=https://www.google.com/maps/place/X/data%3D!3d41.285612!4d69.203411&gl=UZ"
    chain = {"https://maps.app.goo.gl/AbC123": consent}

    async with redirects(chain, seen) as client:
        found = await resolve("https://maps.app.goo.gl/AbC123", client)

    assert found == Point(41.285612, 69.203411, "google") and seen == ["https://maps.app.goo.gl/AbC123"]


async def test_a_short_yandex_link_follows_relative_redirects():
    seen: list[str] = []
    chain = {
        "https://yandex.uz/maps/-/CDqZ4R": "/maps/10335/tashkent/?ll=69.203411%2C41.285612&z=17",
    }

    async with redirects(chain, seen) as client:
        found = await resolve("https://yandex.uz/maps/-/CDqZ4R", client)

    assert found.rounded() == OLMAZOR and found.source == "yandex"


@pytest.mark.parametrize(("first_hop", "kind"), [
    ("https://evil.example/steal", "not_a_map"),  # a redirect off the map hosts is not followed
    ("http://www.google.com/maps/place/X", "not_a_map"),  # nor down to plain HTTP
    ("https://127.0.0.1/admin", "not_a_map"),
    ("https://www.google.com/maps/place/Just+a+name", "no_point"),
])
async def test_a_short_link_never_leaves_the_map_hosts(first_hop, kind):
    seen: list[str] = []
    async with redirects({"https://maps.app.goo.gl/X": first_hop}, seen) as client:
        with pytest.raises(LinkError) as caught:
            await resolve("https://maps.app.goo.gl/X", client)

    assert caught.value.kind == kind
    assert all("evil" not in url and "127.0.0.1" not in url and url.startswith("https") for url in seen)


async def test_other_links_and_loops_end_without_a_point():
    seen: list[str] = []
    loop = {"https://maps.app.goo.gl/A": "https://maps.app.goo.gl/B", "https://maps.app.goo.gl/B": "https://maps.app.goo.gl/A"}
    async with redirects(loop, seen) as client:
        with pytest.raises(LinkError, match="no_point"):
            await resolve("https://maps.app.goo.gl/A", client)
        with pytest.raises(LinkError, match="not_a_map"):
            await resolve("https://example.com/somewhere", client)

    assert len(seen) == 5 and not is_map_link("https://example.com/x") and is_map_link("https://maps.app.goo.gl/x")


async def test_a_network_failure_is_said_as_unreachable():
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    async with httpx.AsyncClient(transport=httpx.MockTransport(down)) as client:
        with pytest.raises(LinkError, match="unreachable"):
            await resolve("https://maps.app.goo.gl/X", client)


def test_coordinates_inside_a_sentence_are_found_and_hours_are_not():
    from nazar_agent.setup.links import point_in_text

    assert point_in_text("Open a site at 41.2268, 69.2190, shift 9 to 18").rounded() == (41.2268, 69.219)
    assert point_in_text("точка 41.285612 69.203411 рядом с метро").rounded() == (41.285612, 69.203411)
    assert point_in_text("смена с 8, 17 человек, 2.5 часа") is None
