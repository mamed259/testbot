from bot import is_excluded_location, parse_listing_card, split_location_date


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
