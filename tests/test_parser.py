import unittest

from bot import listing_id, normalize_url, parse_listing_card


class ParserTests(unittest.TestCase):
    def test_normalize_url_removes_query_and_fragment(self):
        url = normalize_url("/d/oferta/mieszkanie-warszawa-IDabc123.html?foo=bar#x")
        self.assertEqual(
            url,
            "https://www.olx.pl/d/oferta/mieszkanie-warszawa-IDabc123.html",
        )

    def test_listing_id_prefers_olx_id(self):
        url = "https://www.olx.pl/d/oferta/mieszkanie-warszawa-IDabc123.html"
        self.assertEqual(listing_id(url), "abc123")

    def test_parse_listing_card(self):
        html = '''
          <div data-cy="l-card">
            <a href="/d/oferta/mieszkanie-2-pokoje-IDxyz789.html?foo=bar">link</a>
            <h6>2 pokoje, Mokotów</h6>
          </div>
        '''
        item = parse_listing_card(html)
        self.assertEqual(item["id"], "xyz789")
        self.assertEqual(
            item["url"],
            "https://www.olx.pl/d/oferta/mieszkanie-2-pokoje-IDxyz789.html",
        )
        self.assertIn("Mokotów", item["text"])


if __name__ == "__main__":
    unittest.main()
