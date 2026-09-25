from datetime import datetime
from zoneinfo import ZoneInfo

from bot import (
    is_excluded_location,
    parse_publication_timestamp,
    split_location_date,
)

WARSAW = ZoneInfo("Europe/Warsaw")


def test_excluded_locations():
    assert is_excluded_location("Warszawa, Ursus")
    assert is_excluded_location("Warszawa, Praga-Południe")
    assert is_excluded_location("Warszawa, Białołęka")
    assert is_excluded_location("Warszawa, Bielany")
    assert is_excluded_location("Warszawa, Bemowo")
    assert not is_excluded_location("Warszawa, Mokotów")


def test_split_location_date():
    location, posted = split_location_date("Warszawa, Ursus - Odświeżono dzisiaj o 23:15")
    assert location == "Warszawa, Ursus"
    assert posted == "Odświeżono dzisiaj o 23:15"


def test_parse_relative_today():
    # Parser uses current Warsaw date; check time portion only via fixed-like assertion on hour.
    dt = parse_publication_timestamp("dzisiaj o 10:05")
    assert dt is not None
    assert dt.hour == 10 and dt.minute == 5
    assert dt.tzinfo == WARSAW


def test_parse_relative_yesterday():
    dt = parse_publication_timestamp("wczoraj o 23:40")
    assert dt is not None
    assert dt.hour == 23 and dt.minute == 40
    assert dt.tzinfo == WARSAW


def test_parse_iso():
    dt = parse_publication_timestamp("2026-09-25T10:05:00+02:00")
    assert dt == datetime(2026, 9, 25, 10, 5, tzinfo=WARSAW)


def test_parse_long_polish_date():
    dt = parse_publication_timestamp("22 września 2026 10:05")
    assert dt == datetime(2026, 9, 22, 10, 5, tzinfo=WARSAW)
