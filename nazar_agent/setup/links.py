"""A site's point from what the owner pastes: a Google, Yandex or 2GIS map link, or two numbers.

Parsed by code, never guessed by a model. Google writes latitude first
(``@41.28,69.20``, ``q=``, ``ll=``, the pin's ``!3d41.28!4d69.20``); Yandex and
2GIS write longitude first (``ll=69.20,41.28``, ``pt=``, ``whatshere[point]``,
``m=``). A pin is preferred over the map's centre.

A short link (``maps.app.goo.gl/…``, ``yandex.uz/maps/-/…``) is opened by
following its redirects one by one, only over HTTPS and only to Google's and
Yandex's own map hosts — the agent never fetches an address someone made up
(no SSRF), and reads no page bodies.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

import httpx

MAX_HOPS = 5
TIMEOUT_SECONDS = 5.0
NUMBER = r"-?\d{1,3}(?:\.\d+)?"
PAIR = re.compile(rf"({NUMBER})\s*[,;\s]\s*({NUMBER})")
# Two numbers inside a sentence ("…at 41.2268, 69.2190, shift…"): only with decimals, so "с 8, 17" is not a point.
IN_TEXT = re.compile(r"(?<![\d.])(-?\d{1,2}\.\d{3,})\s*[,;]?\s*(-?\d{1,3}\.\d{3,})(?![\d.])")
GOOGLE_PIN = re.compile(rf"!3d({NUMBER})!4d({NUMBER})")
GOOGLE_VIEW = re.compile(rf"@({NUMBER}),({NUMBER})")
GOOGLE_HOSTS = re.compile(r"^(?:www\.|maps\.)?google\.[a-z.]{2,6}$")
YANDEX_HOSTS = re.compile(r"^(?:www\.)?yandex\.(?:ru|uz|com|kz|by)$")
TWO_GIS_HOSTS = re.compile(r"^(?:www\.)?2gis\.[a-z]{2,3}$")
SHORT_HOSTS = frozenset({"maps.app.goo.gl", "goo.gl"})
CONSENT_HOSTS = frozenset({"consent.google.com"})


class LinkError(Exception):
    """The link gives no point; ``kind`` says why, for a message in the owner's language."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind  # not_a_map, no_point, unreachable


@dataclass(frozen=True, slots=True)
class Point:
    latitude: float
    longitude: float
    source: str  # google, yandex, 2gis, numbers

    def rounded(self) -> tuple[float, float]:
        return round(self.latitude, 6), round(self.longitude, 6)


def point_in(text: str) -> Point | None:
    """The point in a full map link or in two numbers, or None. No network."""
    text = text.strip()
    if not text.lower().startswith(("http://", "https://")):
        match = PAIR.fullmatch(text)
        return _point(match.group(1), match.group(2), "numbers") if match else None
    parts = urlsplit(text)
    host = (parts.hostname or "").lower()
    query = parse_qs(parts.query)
    whole = unquote(text)
    if GOOGLE_HOSTS.match(host):
        pin = GOOGLE_PIN.search(whole)
        if pin:
            return _point(pin.group(1), pin.group(2), "google")
        for key in ("q", "query", "ll", "center", "destination"):
            found = _pair(query.get(key, [""])[0].removeprefix("loc:"), latitude_first=True, source="google")
            if found:
                return found
        view = GOOGLE_VIEW.search(whole)
        return _point(view.group(1), view.group(2), "google") if view else None
    if YANDEX_HOSTS.match(host):
        for key in ("pt", "whatshere[point]", "ll"):
            found = _pair(query.get(key, [""])[0], latitude_first=False, source="yandex")
            if found:
                return found
        return None
    if TWO_GIS_HOSTS.match(host):
        return _pair(query.get("m", [""])[0].split("/")[0], latitude_first=False, source="2gis")
    return None


def point_in_text(text: str) -> Point | None:
    """Coordinates written inside a sentence, latitude first, as maps copy them."""
    match = IN_TEXT.search(text)
    return _point(match.group(1), match.group(2), "numbers") if match else None


def is_map_link(text: str) -> bool:
    host = (urlsplit(text.strip()).hostname or "").lower()
    return bool(
        host in SHORT_HOSTS or GOOGLE_HOSTS.match(host) or YANDEX_HOSTS.match(host) or TWO_GIS_HOSTS.match(host)
    )


async def resolve(text: str, client: httpx.AsyncClient | None = None) -> Point:
    """The point of a link, opening a short one by its redirects; LinkError if there is none."""
    found = point_in(text)
    if found:
        return found
    if not text.strip().lower().startswith(("http://", "https://")):
        raise LinkError("no_point")
    if not is_map_link(text):
        raise LinkError("not_a_map")
    own = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT_SECONDS, follow_redirects=False)
    try:
        return await _follow(text.strip(), client)
    finally:
        if own:
            await client.aclose()


async def _follow(url: str, client: httpx.AsyncClient) -> Point:
    for _ in range(MAX_HOPS):
        _check_allowed(url)
        try:
            # HEAD would do, but Google answers some short links only to GET; the body is not read.
            async with client.stream("GET", url, follow_redirects=False) as response:
                location = response.headers.get("location")
        except httpx.HTTPError as exc:
            raise LinkError("unreachable") from exc
        if not location:
            break
        url = urljoin(url, location)
        consent = urlsplit(url)
        if (consent.hostname or "").lower() in CONSENT_HOSTS:
            url = parse_qs(consent.query).get("continue", [url])[0]  # Google's cookie page: the map is in "continue"
        found = point_in(url)
        if found:
            return found
    raise LinkError("no_point")


def _check_allowed(url: str) -> None:
    """Only HTTPS to a map host by name: no other site, no address of the agent's own network."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or parts.username or parts.password or parts.port not in (None, 443):
        raise LinkError("not_a_map")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise LinkError("not_a_map")
    if not (host in SHORT_HOSTS or GOOGLE_HOSTS.match(host) or YANDEX_HOSTS.match(host)):
        raise LinkError("not_a_map")


def _pair(value: str, *, latitude_first: bool, source: str) -> Point | None:
    match = PAIR.search(unquote(value))
    if not match:
        return None
    first, second = match.group(1), match.group(2)
    return _point(first, second, source) if latitude_first else _point(second, first, source)


def _point(latitude: str, longitude: str, source: str) -> Point | None:
    lat, lng = float(latitude), float(longitude)
    if not (-90 <= lat <= 90 and -180 <= lng <= 180) or (lat == 0 and lng == 0):
        return None
    return Point(lat, lng, source)
