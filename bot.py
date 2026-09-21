import json
import logging
import os
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("olx-bot")

OLX_URL = os.getenv("OLX_URL", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
STATE_FILE = Path(os.getenv("STATE_FILE", "sent.json"))
MAX_CARDS = int(os.getenv("MAX_CARDS", "60"))
EXCLUDED_LOCATIONS = [
    "praga-południe",
    "praga południe",
    "białołęka",
    "bielany",
    "bemowo",
    "ursus",
]
MODE = os.getenv("MODE", "monitor").lower()

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"


def normalize_url(url: str) -> str:
    url = urljoin("https://www.olx.pl", url)
    parsed = urlparse(url)
    # Remove fragments and tracking query strings from listing URLs.
    return parsed._replace(query="", fragment="").geturl().rstrip("/")


def listing_id(url: str) -> str:
    normalized = normalize_url(url)
    match = re.search(r"ID([A-Za-z0-9]+)(?:\.html)?$", normalized, re.I)
    if match:
        return match.group(1)
    return normalized


def clean_text(value: str) -> str:
    return " ".join(value.split())


def parse_listing_card(card_html: str) -> dict:
    """Tiny parser used by unit tests; browser extraction is in scrape_olx()."""
    url_match = re.search(r'href=["\']([^"\']+)["\']', card_html, re.I)
    if not url_match:
        raise ValueError("No href in card")
    url = normalize_url(url_match.group(1))

    text = re.sub(r"<[^>]+>", " ", card_html)
    text = clean_text(text)
    return {
        "id": listing_id(url),
        "url": url,
        "text": text,
    }


def split_location_date(value: str) -> tuple[str, str]:
    value = clean_text(value)
    if " - " in value:
        location, posted_at = value.split(" - ", 1)
        return clean_text(location), clean_text(posted_at)
    return value, ""


def is_excluded_location(location: str) -> bool:
    normalized = clean_text(location).casefold()
    return any(excluded in normalized for excluded in EXCLUDED_LOCATIONS)



def extract_publication_data(detail_page) -> tuple[str, str]:
    """Return (published_at, modified_at) from an OLX listing detail page.

    OLX detail pages can expose exact "Data dodania" / "Data modyfikacji"
    values in the rendered text. We also check common metadata fields first.
    """
    published_at = ""
    modified_at = ""

    for selector in [
        'meta[property="article:published_time"]',
        'meta[itemprop="datePublished"]',
        'meta[name="datePublished"]',
    ]:
        try:
            value = detail_page.locator(selector).first.get_attribute("content") or ""
            if value:
                published_at = clean_text(value)
                break
        except Exception:
            pass

    for selector in [
        'meta[property="article:modified_time"]',
        'meta[itemprop="dateModified"]',
        'meta[name="dateModified"]',
    ]:
        try:
            value = detail_page.locator(selector).first.get_attribute("content") or ""
            if value:
                modified_at = clean_text(value)
                break
        except Exception:
            pass

    try:
        body_text = clean_text(detail_page.locator("body").inner_text(timeout=5_000))
    except Exception:
        body_text = ""

    if not published_at:
        match = re.search(r"Data dodania\s*:\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})", body_text, re.I)
        if match:
            published_at = match.group(1)

    if not modified_at:
        match = re.search(r"Data modyfikacji\s*:\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})", body_text, re.I)
        if match:
            modified_at = match.group(1)

    return published_at, modified_at


def enrich_publication_data(items: list[dict]) -> list[dict]:
    """Fetch exact publication dates only for items that are about to be sent."""
    if not items:
        return items

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            locale="pl-PL",
            timezone_id="Europe/Warsaw",
            viewport={"width": 1440, "height": 1200},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/140.0 Safari/537.36"
            ),
        )
        page = context.new_page()
        try:
            for item in items:
                try:
                    page.goto(item["url"], wait_until="domcontentloaded", timeout=45_000)
                    page.wait_for_timeout(700)
                    published_at, modified_at = extract_publication_data(page)
                    if published_at:
                        item["published_at"] = published_at
                    if modified_at:
                        item["modified_at"] = modified_at
                except Exception as exc:
                    logger.warning("Could not fetch publication date for %s: %s", item.get("id"), exc)
        finally:
            context.close()
            browser.close()

    return items


def scrape_olx() -> list[dict]:
    listings: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            locale="pl-PL",
            timezone_id="Europe/Warsaw",
            viewport={"width": 1440, "height": 1200},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/140.0 Safari/537.36"
            ),
        )
        page = context.new_page()

        try:
            logger.info("Opening OLX search page")
            page.goto(OLX_URL, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(3_000)

            # Cookie dialogs vary. Dismiss a few common variants without failing the run.
            for selector in [
                'button[data-testid="cookies-policy-accept"]',
                'button:has-text("Zgadzam się")',
                'button:has-text("Akceptuję")',
                'button:has-text("Accept")',
            ]:
                try:
                    btn = page.locator(selector).first
                    if btn.is_visible(timeout=800):
                        btn.click(timeout=1_500)
                        page.wait_for_timeout(500)
                        break
                except Exception:
                    pass

            cards = page.locator('div[data-cy="l-card"]')
            count = min(cards.count(), MAX_CARDS)
            logger.info("Found %s listing cards", count)

            for index in range(count):
                card = cards.nth(index)
                try:
                    links = card.locator('a[href]')
                    href = ""
                    for j in range(min(links.count(), 8)):
                        candidate = links.nth(j).get_attribute("href") or ""
                        if "/d/oferta/" in candidate:
                            href = candidate
                            break
                    if not href and links.count():
                        href = links.first.get_attribute("href") or ""
                    if not href:
                        continue

                    url = normalize_url(href)
                    if "/d/oferta/" not in url:
                        continue

                    title = ""
                    for selector in [
                        "h6",
                        '[data-cy="ad-card-title"]',
                        "h4",
                    ]:
                        loc = card.locator(selector).first
                        if loc.count():
                            try:
                                value = clean_text(loc.inner_text())
                            except Exception:
                                value = ""
                            if value:
                                title = value
                                break

                    if not title:
                        title = clean_text(card.inner_text()).split("\n")[0]

                    price = ""
                    for selector in [
                        '[data-testid="ad-price"]',
                        '[data-cy="ad-price"]',
                    ]:
                        loc = card.locator(selector).first
                        if loc.count():
                            try:
                                price = clean_text(loc.inner_text())
                            except Exception:
                                price = ""
                            if price:
                                break

                    location = ""
                    posted_at = ""
                    location_date = card.locator('[data-testid="location-date"]').first
                    if location_date.count():
                        try:
                            location, posted_at = split_location_date(location_date.inner_text())
                        except Exception:
                            location_date_text = ""
                    
                    # Do not send unwanted Warsaw districts.
                    if is_excluded_location(location):
                        logger.info("Skipping excluded location: %s", location)
                        continue

                    size = ""
                    size_loc = card.locator('span[data-nx-name="P5"]').first
                    if size_loc.count():
                        try:
                            size = clean_text(size_loc.inner_text())
                        except Exception:
                            size = ""

                    image_url = ""
                    img = card.locator("img").first
                    if img.count():
                        image_url = (
                            img.get_attribute("src")
                            or img.get_attribute("data-src")
                            or ""
                        )

                    listings.append(
                        {
                            "id": listing_id(url),
                            "title": title[:300],
                            "price": price[:100],
                            "url": url,
                            "image_url": image_url,
                            "location": location[:200],
                            "posted_at": posted_at[:200],
                            "size": size[:50],
                        }
                    )
                except Exception as exc:
                    logger.warning("Could not parse card %s: %s", index, exc)

        except PlaywrightTimeoutError:
            raise RuntimeError("OLX page load timed out")
        finally:
            context.close()
            browser.close()

    # Keep the first occurrence of each listing id.
    unique: dict[str, dict] = {}
    for item in listings:
        unique.setdefault(item["id"], item)
    return list(unique.values())


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"initialized": False, "sent_ids": []}

    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return {
            "initialized": bool(data.get("initialized")),
            "sent_ids": list(data.get("sent_ids", [])),
        }
    except (json.JSONDecodeError, OSError):
        logger.warning("State file unreadable; starting fresh")
        return {"initialized": False, "sent_ids": []}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def telegram_post(method: str, payload: dict) -> dict:
    response = requests.post(
        f"{TELEGRAM_API}/{method}",
        json=payload,
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API error: {data}")
    return data


def send_listing(item: dict) -> None:
    title = item.get("title") or "Новое объявление"
    price = item.get("price") or "Цена не указана"
    location = item.get("location") or "Локация не указана"
    posted_at = item.get("posted_at") or "Время не указано"
    published_at = item.get("published_at")
    modified_at = item.get("modified_at")
    size = item.get("size")

    lines = [
        f"🏠 {title}",
        f"💰 {price}",
        f"📍 {location}",
    ]
    if published_at:
        lines.append(f"📅 Dodano: {published_at}")
    if modified_at:
        lines.append(f"🔄 Zmodyfikowano: {modified_at}")
    elif posted_at:
        lines.append(f"🕒 {posted_at}")
    if size:
        lines.append(f"📐 {size}")
    text = "\n".join(lines)

    keyboard = {
        "inline_keyboard": [
            [{"text": "🔗 Открыть объявление", "url": item["url"]}]
        ]
    }

    # Send image when OLX exposes a usable image URL; otherwise send text.
    image_url = item.get("image_url") or ""
    if image_url.startswith("http"):
        try:
            telegram_post(
                "sendPhoto",
                {
                    "chat_id": CHAT_ID,
                    "photo": image_url,
                    "caption": text,
                    "reply_markup": keyboard,
                },
            )
            return
        except Exception as exc:
            logger.warning("sendPhoto failed, falling back to text: %s", exc)

    telegram_post(
        "sendMessage",
        {
            "chat_id": CHAT_ID,
            "text": text,
            "disable_web_page_preview": False,
            "reply_markup": keyboard,
        },
    )


def main() -> None:
    missing = [
        name
        for name, value in (("OLX_URL", OLX_URL), ("BOT_TOKEN", BOT_TOKEN), ("CHAT_ID", CHAT_ID))
        if not value
    ]
    if missing:
        raise RuntimeError("Missing required environment variables: " + ", ".join(missing))

    logger.info("Starting OLX monitor")
    state = load_state()
    sent_ids = set(state["sent_ids"])

    listings = scrape_olx()
    current_ids = {item["id"] for item in listings}

    if MODE == "latest10":
        # Safe preview mode: never send already-known/old IDs and never run as a
        # replacement for the initialization step. This prevents a manual test
        # from spamming old listings.
        if not state["initialized"]:
            state["initialized"] = True
            state["sent_ids"] = sorted(current_ids)
            save_state(state)
            logger.info("Latest10 preview skipped on uninitialized state; state initialized with current listings")
            return

        candidates = [item for item in listings if item["id"] not in sent_ids]
        latest = candidates[:10]
        logger.info("Latest10 mode: %s unseen listings", len(latest))
        latest = enrich_publication_data(latest)
        for item in reversed(latest):
            send_listing(item)
            sent_ids.add(item["id"])

        state["initialized"] = True
        state["sent_ids"] = sorted(set([*sent_ids, *current_ids]))[-2500:]
        save_state(state)
        return

    if MODE != "monitor":
        raise RuntimeError(f"Unknown MODE: {MODE}")

    # First monitor run establishes a baseline and sends nothing. This is the
    # key rule that prevents existing OLX listings from arriving as "new".
    if not state["initialized"]:
        state["initialized"] = True
        state["sent_ids"] = sorted(current_ids)
        save_state(state)
        logger.info(
            "First run: initialized with %s existing listings; nothing sent",
            len(current_ids),
        )
        return

    new_items = [item for item in listings if item["id"] not in sent_ids]

    # OLX is sorted newest-first, so Telegram receives the oldest new item first.
    new_items = enrich_publication_data(new_items)
    for item in reversed(new_items):
        send_listing(item)
        sent_ids.add(item["id"])
        logger.info("Sent listing %s", item["id"])

    merged = list(dict.fromkeys([*sent_ids, *current_ids]))
    state["initialized"] = True
    state["sent_ids"] = merged[-2500:]
    save_state(state)

    logger.info("Done. New listings sent: %s", len(new_items))


if __name__ == "__main__":
    main()
