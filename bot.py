import json
import logging
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

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
MAX_NEW_PER_RUN = int(os.getenv("MAX_NEW_PER_RUN", "10"))
DETAIL_PAGE_TIMEOUT_MS = int(os.getenv("DETAIL_PAGE_TIMEOUT_MS", "12000"))
BOOTSTRAP_SCAN_LIMIT = int(os.getenv("BOOTSTRAP_SCAN_LIMIT", "20"))
EXCLUDED_LOCATIONS = [
    "praga-południe",
    "praga południe",
    "białołęka",
    "bielany",
    "bemowo",
    "ursus",
]
MODE = os.getenv("MODE", "monitor").lower()
WARSAW_TZ = ZoneInfo("Europe/Warsaw")
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

POLISH_MONTHS = {
    "stycznia": 1, "lutego": 2, "marca": 3, "kwietnia": 4,
    "maja": 5, "czerwca": 6, "lipca": 7, "sierpnia": 8,
    "września": 9, "października": 10, "listopada": 11, "grudnia": 12,
}


def normalize_url(url: str) -> str:
    url = urljoin("https://www.olx.pl", url)
    parsed = urlparse(url)
    return parsed._replace(query="", fragment="").geturl().rstrip("/")


def listing_id(url: str) -> str:
    normalized = normalize_url(url)
    match = re.search(r"ID([A-Za-z0-9]+)(?:\.html)?$", normalized, re.I)
    return match.group(1) if match else normalized


def clean_text(value: str) -> str:
    return " ".join((value or "").split())


def split_location_date(value: str) -> tuple[str, str]:
    value = clean_text(value)
    if " - " in value:
        location, posted_at = value.split(" - ", 1)
        return clean_text(location), clean_text(posted_at)
    return value, ""


def is_excluded_location(location: str) -> bool:
    normalized = clean_text(location).casefold()
    return any(excluded in normalized for excluded in EXCLUDED_LOCATIONS)


def parse_relative_polish_date(value: str, now: datetime | None = None) -> datetime | None:
    """Parse relative OLX strings such as 'dzisiaj o 10:05' and 'wczoraj o 23:40'."""
    value = clean_text(value).casefold()
    now = now or datetime.now(WARSAW_TZ)

    m = re.search(r"\b(dzisiaj|wczoraj)\s+o\s+(\d{1,2}):(\d{2})\b", value)
    if not m:
        return None
    day_word, hour, minute = m.groups()
    date = now.date() if day_word == "dzisiaj" else (now - timedelta(days=1)).date()
    return datetime(date.year, date.month, date.day, int(hour), int(minute), tzinfo=WARSAW_TZ)


def parse_publication_timestamp(value: str) -> datetime | None:
    """Parse OLX publication/modified timestamps into Europe/Warsaw."""
    value = clean_text(value)
    if not value:
        return None

    relative = parse_relative_polish_date(value)
    if relative:
        return relative

    iso_candidate = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso_candidate)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=WARSAW_TZ)
        return dt.astimezone(WARSAW_TZ)
    except ValueError:
        pass

    m = re.fullmatch(r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})(?:\s+(\d{1,2}):(\d{2}))?", value)
    if m:
        day, month, year, hour, minute = m.groups()
        return datetime(int(year), int(month), int(day), int(hour or 0), int(minute or 0), tzinfo=WARSAW_TZ)

    m = re.fullmatch(
        r"(\d{1,2})\s+(stycznia|lutego|marca|kwietnia|maja|czerwca|lipca|sierpnia|września|października|listopada|grudnia)\s+(\d{4})(?:\s+(\d{1,2}):(\d{2}))?",
        value,
        re.I,
    )
    if m:
        day, month_name, year, hour, minute = m.groups()
        return datetime(
            int(year), POLISH_MONTHS[month_name.casefold()], int(day),
            int(hour or 0), int(minute or 0), tzinfo=WARSAW_TZ,
        )

    return None


def publication_sort_key(item: dict):
    dt = parse_publication_timestamp(item.get("published_at", ""))
    return dt or datetime.min.replace(tzinfo=WARSAW_TZ)


def _find_date_in_json(value, keys: tuple[str, ...]) -> str:
    if isinstance(value, dict):
        for key in keys:
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return clean_text(candidate)
        for child in value.values():
            found = _find_date_in_json(child, keys)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_date_in_json(child, keys)
            if found:
                return found
    return ""


def extract_publication_data(detail_page) -> tuple[str, str]:
    published_at = ""
    modified_at = ""

    # Metadata first.
    selectors = {
        "published": [
            'meta[property="article:published_time"]',
            'meta[itemprop="datePublished"]',
            'meta[name="datePublished"]',
        ],
        "modified": [
            'meta[property="article:modified_time"]',
            'meta[itemprop="dateModified"]',
            'meta[name="dateModified"]',
        ],
    }
    for selector in selectors["published"]:
        try:
            value = detail_page.locator(selector).first.get_attribute("content", timeout=700) or ""
            if value:
                published_at = clean_text(value)
                break
        except Exception:
            pass
    for selector in selectors["modified"]:
        try:
            value = detail_page.locator(selector).first.get_attribute("content", timeout=700) or ""
            if value:
                modified_at = clean_text(value)
                break
        except Exception:
            pass

    # JSON-LD.
    try:
        scripts = detail_page.locator('script[type="application/ld+json"]').all_inner_texts()
        for raw in scripts:
            try:
                data = json.loads(raw)
            except Exception:
                continue
            if not published_at:
                published_at = _find_date_in_json(data, ("datePublished", "dateCreated", "createdAt", "created_at"))
            if not modified_at:
                modified_at = _find_date_in_json(data, ("dateModified", "updatedAt", "updated_at"))
            if published_at and modified_at:
                break
    except Exception:
        pass

    # The OLX rendered text usually contains 'Dodane dzisiaj o 10:05'.
    try:
        body_text = clean_text(detail_page.locator("body").inner_text(timeout=2500))
    except Exception:
        body_text = ""

    if body_text:
        if not published_at:
            patterns = (
                r"\bDodane\s+(dzisiaj|wczoraj)\s+o\s+(\d{1,2}:\d{2})\b",
                r"\bDodane\s+(\d{1,2}[./-]\d{1,2}[./-]\d{4})(?:\s+(\d{1,2}:\d{2}))?\b",
                r"\bData dodania\s*:?\s*(\d{1,2}[./-]\d{1,2}[./-]\d{4}(?:\s+\d{1,2}:\d{2})?)\b",
                r"\bData dodania\s*:?\s*(\d{1,2}\s+(?:stycznia|lutego|marca|kwietnia|maja|czerwca|lipca|sierpnia|września|października|listopada|grudnia)\s+\d{4}(?:\s+\d{1,2}:\d{2})?)\b",
            )
            for pattern in patterns:
                m = re.search(pattern, body_text, re.I)
                if m:
                    if "dzisiaj" in m.group(0).casefold() or "wczoraj" in m.group(0).casefold():
                        published_at = clean_text(f"{m.group(1)} o {m.group(2)}")
                    else:
                        parts = [group for group in m.groups() if group]
                        published_at = clean_text(" ".join(parts))
                    break

        if not modified_at:
            patterns = (
                r"\bOdświeżono\s+(dzisiaj|wczoraj)\s+o\s+(\d{1,2}:\d{2})\b",
                r"\bData modyfikacji\s*:?\s*(\d{1,2}[./-]\d{1,2}[./-]\d{4}(?:\s+\d{1,2}:\d{2})?)\b",
            )
            for pattern in patterns:
                m = re.search(pattern, body_text, re.I)
                if m:
                    if "dzisiaj" in m.group(0).casefold() or "wczoraj" in m.group(0).casefold():
                        modified_at = clean_text(f"{m.group(1)} o {m.group(2)}")
                    else:
                        modified_at = clean_text(m.group(1))
                    break

    # Try raw HTML as a final fallback, without waiting for a full DOM state.
    try:
        html = detail_page.content()
    except Exception:
        html = ""
    if html and not published_at:
        for pattern in (
            r'"(?:datePublished|dateCreated|createdAt|created_at)"\s*:\s*"([^"]+)"',
            r'\bDodane\s+(dzisiaj|wczoraj)\s+o\s+(\d{1,2}:\d{2})\b',
            r'\bDodane\s+(\d{1,2}[./-]\d{1,2}[./-]\d{4})(?:\s+(\d{1,2}:\d{2}))?\b',
        ):
            m = re.search(pattern, html, re.I)
            if m:
                if len(m.groups()) == 2 and m.group(1).casefold() in {"dzisiaj", "wczoraj"}:
                    published_at = clean_text(f"{m.group(1)} o {m.group(2)}")
                else:
                    published_at = clean_text(" ".join(group for group in m.groups() if group))
                break

    parsed = parse_publication_timestamp(published_at)
    if parsed:
        published_at = parsed.isoformat(timespec="minutes")
    parsed = parse_publication_timestamp(modified_at)
    if parsed:
        modified_at = parsed.isoformat(timespec="minutes")

    return published_at, modified_at


def enrich_publication_data(items: list[dict]) -> list[dict]:
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
                    logger.info("Fetching publication date: %s", item.get("id"))
                    # Commit returns as soon as navigation is committed; we do not wait for every asset.
                    page.goto(item["url"], wait_until="commit", timeout=DETAIL_PAGE_TIMEOUT_MS)
                    page.wait_for_timeout(800)
                    published_at, modified_at = extract_publication_data(page)
                    if published_at:
                        item["published_at"] = published_at
                    if modified_at:
                        item["modified_at"] = modified_at
                except PlaywrightTimeoutError:
                    logger.warning("Publication page timed out for %s; continuing", item.get("id"))
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
            page.goto(OLX_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(1500)

            for selector in [
                'button[data-testid="cookies-policy-accept"]',
                'button:has-text("Zgadzam się")',
                'button:has-text("Akceptuję")',
                'button:has-text("Accept")',
            ]:
                try:
                    btn = page.locator(selector).first
                    if btn.is_visible(timeout=600):
                        btn.click(timeout=1200)
                        page.wait_for_timeout(250)
                        break
                except Exception:
                    pass

            cards = page.locator('div[data-cy="l-card"]')
            count = min(cards.count(), MAX_CARDS)
            logger.info("Found %s listing cards", count)

            for index in range(count):
                card = cards.nth(index)
                try:
                    href = ""
                    links = card.locator('a[href]')
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
                    for selector in ("h6", '[data-cy="ad-card-title"]', "h4"):
                        loc = card.locator(selector).first
                        if loc.count():
                            try:
                                value = clean_text(loc.inner_text())
                            except Exception:
                                value = ""
                            if value:
                                title = value
                                break
                    title = title or clean_text(card.inner_text()).split("\n")[0]

                    price = ""
                    for selector in ('[data-testid="ad-price"]', '[data-cy="ad-price"]'):
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
                            pass
                    if is_excluded_location(location):
                        logger.info("Skipping excluded location: %s", location)
                        continue

                    size = ""
                    size_loc = card.locator('span[data-nx-name="P5"]').first
                    if size_loc.count():
                        try:
                            size = clean_text(size_loc.inner_text())
                        except Exception:
                            pass

                    image_url = ""
                    img = card.locator("img").first
                    if img.count():
                        image_url = img.get_attribute("src") or img.get_attribute("data-src") or ""

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
            raise RuntimeError("OLX search page load timed out")
        finally:
            context.close()
            browser.close()

    unique: dict[str, dict] = {}
    for item in listings:
        unique.setdefault(item["id"], item)
    return list(unique.values())


def default_state() -> dict:
    return {
        "version": 2,
        "initialized": False,
        "seen_ids": [],
        "sent_ids": [],
    }


def load_state() -> dict:
    if not STATE_FILE.exists():
        return default_state()
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("State file unreadable; starting fresh")
        return default_state()

    # v7 state used a timestamp watermark. Migrate it by doing one fresh bootstrap.
    if data.get("version") != 2 or "seen_ids" not in data:
        logger.info("Migrating legacy state to v2 snapshot mode")
        return default_state()

    return {
        "version": 2,
        "initialized": bool(data.get("initialized")),
        "seen_ids": list(data.get("seen_ids", [])),
        "sent_ids": list(data.get("sent_ids", [])),
    }


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def telegram_post(method: str, payload: dict) -> dict:
    response = requests.post(f"{TELEGRAM_API}/{method}", json=payload, timeout=30)
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API error: {data}")
    return data


def format_publication(item: dict) -> str:
    published_at = item.get("published_at")
    if published_at:
        parsed = parse_publication_timestamp(published_at)
        if parsed:
            return parsed.strftime("%d.%m.%Y %H:%M")
        return published_at
    return item.get("posted_at") or "не удалось определить"


def send_listing(item: dict) -> None:
    lines = [
        f"🏠 {item.get('title') or 'Новое объявление'}",
        f"💰 {item.get('price') or 'Цена не указана'}",
        f"📍 {item.get('location') or 'Локация не указана'}",
        f"📅 Dodano: {format_publication(item)}",
    ]
    if item.get("modified_at"):
        parsed = parse_publication_timestamp(item["modified_at"])
        lines.append(f"🔄 Zmodyfikowano: {parsed.strftime('%d.%m.%Y %H:%M') if parsed else item['modified_at']}")
    if item.get("size"):
        lines.append(f"📐 {item['size']}")

    text = "\n".join(lines)
    keyboard = {"inline_keyboard": [[{"text": "🔗 Открыть объявление", "url": item["url"]}]]}
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


def bootstrap(listings: list[dict], state: dict) -> None:
    """Send today's freshest cards for the first run, then mark the whole snapshot as seen."""
    candidates = listings[:BOOTSTRAP_SCAN_LIMIT]
    enriched = enrich_publication_data(candidates)
    today = datetime.now(WARSAW_TZ).date()

    exact_today = [
        item for item in enriched
        if (dt := parse_publication_timestamp(item.get("published_at", ""))) and dt.date() == today
    ]

    # If exact publication dates are unavailable, use the top search cards that say 'dzisiaj'.
    if exact_today:
        to_send = sorted(exact_today, key=publication_sort_key)[-MAX_NEW_PER_RUN:]
    else:
        to_send = [item for item in candidates if "dzisiaj" in item.get("posted_at", "").casefold()][:MAX_NEW_PER_RUN]
        if not to_send:
            # The URL itself is sorted by created_at:desc, so use the current top cards rather than failing.
            to_send = candidates[:MAX_NEW_PER_RUN]
            logger.warning("Exact publication dates unavailable; using the top current search cards for bootstrap")

        # Keep any successfully extracted dates attached to the items we're about to send.
        by_id = {item["id"]: item for item in enriched}
        to_send = [by_id.get(item["id"], item) for item in to_send]

    to_send = list(reversed(to_send))
    sent_now = 0
    for item in to_send:
        send_listing(item)
        sent_now += 1

    # Bootstrap establishes a clean baseline: everything visible now is old.
    current_ids = [item["id"] for item in listings]
    state["seen_ids"] = sorted(set(state.get("seen_ids", [])) | set(current_ids))[-5000:]
    state["sent_ids"] = sorted(set(state.get("sent_ids", [])) | {item["id"] for item in to_send})[-2500:]
    state["initialized"] = True
    save_state(state)
    logger.info("Bootstrap complete: sent=%s, baseline=%s listings", sent_now, len(current_ids))


def monitor(listings: list[dict], state: dict) -> None:
    seen = set(state.get("seen_ids", []))
    new_items = [item for item in listings if item["id"] not in seen]

    if not new_items:
        logger.info("No new listings")
        return

    # Search is newest-first; send oldest of the new batch first so Telegram reads chronologically.
    new_items = list(reversed(new_items))
    batch = new_items[:MAX_NEW_PER_RUN]
    batch = enrich_publication_data(batch)

    for item in batch:
        send_listing(item)

    state["seen_ids"] = sorted(seen | {item["id"] for item in batch})[-5000:]
    state["sent_ids"] = sorted(set(state.get("sent_ids", [])) | {item["id"] for item in batch})[-2500:]
    save_state(state)
    logger.info("Done. New listings sent: %s; remaining new in next runs: %s", len(batch), max(0, len(new_items) - len(batch)))


def main() -> None:
    missing = [name for name, value in (("OLX_URL", OLX_URL), ("BOT_TOKEN", BOT_TOKEN), ("CHAT_ID", CHAT_ID)) if not value]
    if missing:
        raise RuntimeError("Missing environment variables: " + ", ".join(missing))

    logger.info("Starting OLX monitor in mode=%s", MODE)
    listings = scrape_olx()
    state = load_state()

    if MODE in {"bootstrap", "bootstrap_today", "latest10"}:
        # Explicit bootstrap always uses current top listings.
        state = default_state()
        bootstrap(listings, state)
    elif not state.get("initialized"):
        bootstrap(listings, state)
    else:
        monitor(listings, state)


if __name__ == "__main__":
    main()
