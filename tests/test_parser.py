from datetime import datetime

from bot import (
    is_excluded_location,
    parse_listing_card,
    parse_publication_timestamp,
    split_location_date,
    is_after_watermark,
)


def test_split_location_date():
    loc, dt = split_location_date("Warszawa, Ursus - Odświeżono dzisiaj o 23:15")
    assert loc == "Warszawa, Ursus"
    assert dt == "Odświeżono dzisiaj o 23:15"


def test_excluded_locations():
    assert is_excluded_location("Warszawa, Ursus")
    assert is_excluded_location("Warszawa, Bielany")
    assert is_excluded_location("Warszawa, Praga-Południe")
    assert is_excluded_location("Warszawa, Białołęka")
    assert is_excluded_location("Warszawa, Bemowo")
    assert not is_excluded_location("Warszawa, Mokotów")


def test_card_id():
    html = '<a href="/d/oferta/test-CID3-ID1abc12.html?search_reason=x"><h4>Test</h4></a>'
    parsed = parse_listing_card(html)
    assert parsed["id"] == "1abc12"
    assert parsed["url"] == "https://www.olx.pl/d/oferta/test-CID3-ID1abc12.html"


def test_parse_iso_timestamp():
    dt = parse_publication_timestamp("2026-09-25T08:03:00Z")
    assert dt is not None
    assert dt.strftime("%Y-%m-%d %H:%M") == "2026-09-25 10:03"


def test_parse_polish_numeric_timestamp():
    dt = parse_publication_timestamp("25.09.2026 10:05")
    assert dt == datetime(2026, 9, 25, 10, 5, tzinfo=dt.tzinfo)


def test_parse_polish_long_timestamp():
    dt = parse_publication_timestamp("25 września 2026 10:05")
    assert dt is not None
    assert dt.strftime("%Y-%m-%d %H:%M") == "2026-09-25 10:05"


def test_watermark_only_allows_newer_publications():
    watermark = parse_publication_timestamp("2026-09-25T10:05:00+02:00")
    assert watermark is not None
    assert not is_after_watermark({"id": "a", "published_at": "2026-09-25T10:04:00+02:00"}, watermark, set())
    assert is_after_watermark({"id": "b", "published_at": "2026-09-25T10:06:00+02:00"}, watermark, set())
    assert not is_after_watermark({"id": "c", "published_at": "2026-09-25T10:05:00+02:00"}, watermark, {"c"})
    assert is_after_watermark({"id": "d", "published_at": "2026-09-25T10:05:00+02:00"}, watermark, {"c"})
