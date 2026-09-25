import json
import logging
import os
import re
from datetime import datetime
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
DETAIL_PAGE_TIMEOUT_MS = int(os.getenv("DETAIL_PAGE_TIMEOUT_MS", "7000"))
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
    "stycznia": 1,
    "lutego": 2,
    "marca": 3,
    "kwietnia": 4,
    "maja": 5,
    "czerwca": 6,
    "lipca": 7,
    "sierpnia": 8,
    "września": 9,
    "października": 10,
    "listopada": 11,
    "grudnia": 12,
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
    return " ".join(value.split())


def parse_listing_card(card_html: str) -> dict:
    url_match = re.search(r'href=["\']([^"\']+)["\']', card_html, re.I)
    if not url_match:
        raise ValueError("No href in card")
    url = normalize_url(url_match.group(1))
    text = clean_text(re.sub(r"<[^>]+>", " ", card_html))
    return {"id": listing_id(url), "url": url, "text": text}


def split_location_date(value: str) -> tuple[str, str]:
    value = clean_text(value)
    if " - " in value:
        location, posted_at = value.split(" - ", 1)
        return clean_text(location), clean_text(posted_at)
    return value, ""


def is_excluded_location(location: str) -> bool:
    normalized = clean_text(location).casefold()
    return any(excluded in normalized for excluded in EXCLUDED_LOCATIONS)


def parse_publication_timestamp(value: str) -> datetime | None:
    """Parse common OLX timestamps and return an aware datetime in Warsaw time."""
    value = clean_text(value)
    if not value:
        return None

    # ISO / JSON timestamps.
    iso_candidate = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso_candidate)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=WARSAW_TZ)
        return dt.astimezone(WARSAW_TZ)
    except ValueError:
        pass

    # Polish numeric dates: 22.09.2026 10:05 / 22-09-2026 10:05.
    match = re.fullmatch(r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})(?:\s+(\d{1,2}):(\d{2}))?", value)
    if match:
        day, month, year, hour, minute = match.groups()
        return datetime(
            int(year), int(month), int(day), int(hour or 0), int(minute or 0), tzinfo=WARSAW_TZ
        )

    # Polish long-form dates: 22 września 2026 10:05.
    match = re.fullmatch(
        r"(\d{1,2})\s+(stycznia|lutego|marca|kwietnia|maja|czerwca|lipca|sierpnia|września|października|listopada|grudnia)\s+(\d{4})(?:\s+(\d{1,2}):(\d{2}))?",
        value,
        re.I,
    )
    if match:
        day, month_name, year, hour, minute = match.groups()
        return datetime(
            int(year),
            POLISH_MONTHS[month_name.casefold()],
            int(day),
            int(hour or 0),
            int(minute or 0),
            tzinfo=WARSAW_TZ,
        )

    return None


def publication_sort_key(item: dict):
    dt = parse_publication_timestamp(item.get("published_at", ""))
    return dt or datetime.min.replace(tzinfo=WARSAW_TZ)


def is_after_watermark(item: dict, watermark: datetime, watermark_ids: set[str]) -> bool:
    dt = parse_publication_timestamp(item.get("published_at", ""))
    if not dt:
        return False
    return dt > watermark or (dt == watermark and item["id"] not in watermark_ids)


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
            value = detail_page.locator(selector).first.get_attribute("content", timeout=1000) or ""
            if value:
                published_at = clean_text(value)
                break
        except Exception:
            pass

    for selector in selectors["modified"]:
        try:
            value = detail_page.locator(selector).first.get_attribute("content", timeout=1000) or ""
            if value:
                modified_at = clean_text(value)
                break
        except Exception:
            pass

    # JSON-LD and embedded page state.
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

    try:
        html = detail_page.content()
    except Exception:
        html = ""

    if html:
        if not published_at:
            for pattern in (
                r'"(?:datePublished|dateCreated|createdAt|created_at)"\s*:\s*"([^"]+)"',
                r'\bData dodania\b[^<\n]{0,120}?((?:\d{1,2}[./-]\d{1,2}[./-]\d{4})(?:\s+\d{1,2}:\d{2})?)',
            ):
                match = re.search(pattern, html, re.I)
                if match:
                    published_at = clean_text(match.group(1))
                    break
        if not modified_at:
            match = re.search(r'"(?:dateModified|updatedAt|updated_at)"\s*:\s*"([^"]+)"', html, re.I)
            if match:
                modified_at = clean_text(match.group(1))

    if not published_at or not modified_at:
        try:
            body_text = clean_text(detail_page.locator("body").inner_text(timeout=1500))
        except Exception:
            body_text = ""
        if not published_at:
            patterns = (
                r"Data dodania\s*:?\s*(\d{1,2}[./-]\d{1,2}[./-]\d{4}(?:\s+\d{1,2}:\d{2})?)",
                r"Data dodania\s*:?\s*(\d{1,2}\s+(?:stycznia|lutego|marca|kwietnia|maja|czerwca|lipca|sierpnia|września|października|listopada|grudnia)\s+\d{4}(?:\s+\d{1,2}:\d{2})?)",
            )
            for pattern in patterns:
                match = re.search(pattern, body_text, re.I)
                if match:
                    published_at = clean_text(match.group(1))
                    break
        if not modified_at:
            match = re.search(
                r"Data modyfikacji\s*:?\s*(\d{1,2}[./-]\d{1,2}[./-]\d{4}(?:\s+\d{1,2}:\d{2})?)",
                body_text,
                re.I,
            )
            if match:
                modified_at = clean_text(match.group(1))

    # Normalize exact timestamps when possible, preserving the original text otherwise.
    for key, value in (("published_at", published_at), ("modified_at", modified_at)):
        parsed = parse_publication_timestamp(value)
        if parsed:
            if key == "published_at":
                published_at = parsed.isoformat(timespec="minutes")
            else:
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
                    page.goto(
                        item["url"],
                        wait_until="domcontentloaded",
                        timeout=DETAIL_PAGE_TIMEOUT_MS,
                    )
                    published_at, modified_at = extract_publication_data(page)
                    if published_at:
                        item["published_at"] = published_at
                    if modified_at:
                        item["modified_at"] = modified_at
                except PlaywrightTimeoutError:
                    logger.warning("Publication page timed out for %s; skipping exact date", item.get("id"))
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
            page.wait_for_timeout(2000)

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
                        page.wait_for_timeout(300)
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
            raise RuntimeError("OLX page load timed out")
        finally:
            context.close()
            browser.close()

    unique: dict[str, dict] = {}
    for item in listings:
        unique.setdefault(item["id"], item)
    return list(unique.values())


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"initialized": False, "last_published_at": None, "last_published_ids": [], "sent_ids": []}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return {
            "initialized": bool(data.get("initialized")),
            "last_published_at": data.get("last_published_at"),
            "last_published_ids": list(data.get("last_published_ids", [])),
            "sent_ids": list(data.get("sent_ids", [])),
        }
    except (json.JSONDecodeError, OSError):
        logger.warning("State file unreadable; starting fresh")
        return {"initialized": False, "last_published_at": None, "last_published_ids": [], "sent_ids": []}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def telegram_post(method: str, payload: dict) -> dict:
    response = requests.post(f"{TELEGRAM_API}/{method}", json=payload, timeout=30)
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API error: {data}")
    return data


def send_listing(item: dict) -> None:
    title = item.get("title") or "Новое объявление"
    price = item.get("price") or "Цена не указана"
    location = item.get("location") or "Локация не указана"
    published_at = item.get("published_at")
    modified_at = item.get("modified_at")
    posted_at = item.get("posted_at")
    size = item.get("size")

    lines = [f"🏠 {title}", f"💰 {price}", f"📍 {location}"]
    if published_at:
        parsed = parse_publication_timestamp(published_at)
        lines.append(
            f"📅 Dodano: {parsed.strftime('%d.%m.%Y %H:%M') if parsed else published_at}"
        )
    else:
        lines.append("📅 Dodano: nie udało się ustalić dokładnej daty")
    if modified_at:
        parsed = parse_publication_timestamp(modified_at)
        lines.append(
            f"🔄 Zmodyfikowano: {parsed.strftime('%d.%m.%Y %H:%M') if parsed else modified_at}"
        )
    elif posted_at:
        lines.append(f"🕒 {posted_at}")
    if size:
        lines.append(f"📐 {size}")

    keyboard = {"inline_keyboard": [[{"text": "🔗 Открыть объявление", "url": item["url"]}]]}
    text = "\n".join(lines)
    image_url = item.get("image_url") or ""
    if image_url.startswith("http"):
        try:
            telegram_post(
                "sendPhoto",
                {"chat_id": CHAT_ID, "photo": image_url, "caption": text, "reply_markup": keyboard},
            )
            return
        except Exception as exc:
            logger.warning("sendPhoto failed, falling back to text: %s", exc)

    telegram_post(
        "sendMessage",
        {"chat_id": CHAT_ID, "text": text, "disable_web_page_preview": False, "reply_markup": keyboard},
    )


def bootstrap_today(listings: list[dict], state: dict) -> None:
    today = datetime.now(WARSAW_TZ).date()
    candidates = listings[:BOOTSTRAP_SCAN_LIMIT]
    candidates = enrich_publication_data(candidates)
    today_items = [
        item for item in candidates
        if (dt := parse_publication_timestamp(item.get("published_at", ""))) and dt.date() == today
    ]
    today_items.sort(key=publication_sort_key)
    today_items = today_items[-MAX_NEW_PER_RUN:]

    if not today_items:
        raise RuntimeError(
            "Could not find today's listings with an exact publication date. "
            "Check OLX detail-page date extraction before continuing."
        )

    sent_successfully: list[dict] = []
    for item in today_items:
        send_listing(item)
        sent_successfully.append(item)

    latest = sent_successfully[-1]
    latest_dt = parse_publication_timestamp(latest["published_at"])
    # Treat every listing found at the watermark minute as already present.
    # This prevents a same-minute old listing (outside the 10-message preview)
    # from being delivered on the next monitor run.
    same_time_ids = [
        item["id"] for item in candidates
        if parse_publication_timestamp(item.get("published_at", "")) == latest_dt
    ]
    state.update(
        {
            "initialized": True,
            "last_published_at": latest_dt.isoformat(timespec="minutes"),
            "last_published_ids": sorted(same_time_ids),
        }
    )
    state["sent_ids"] = sorted(set(state.get("sent_ids", [])) | {item["id"] for item in sent_successfully})[-2500:]
    save_state(state)
    logger.info("Bootstrap complete: sent %s today's listings; watermark=%s", len(sent_successfully), state["last_published_at"])


def monitor(listings: list[dict], state: dict) -> None:
    watermark_text = state.get("last_published_at")
    if not watermark_text:
        logger.info("No publication watermark found; bootstrapping today's listings")
        bootstrap_today(listings, state)
        return

    watermark = parse_publication_timestamp(watermark_text)
    if not watermark:
        raise RuntimeError(f"Invalid last_published_at in state: {watermark_text}")
    watermark_ids = set(state.get("last_published_ids", []))

    # Only candidates newer than the last publication timestamp are considered.
    candidates = listings[:BOOTSTRAP_SCAN_LIMIT]
    candidates = enrich_publication_data(candidates)
    new_items = []
    for item in candidates:
        if is_after_watermark(item, watermark, watermark_ids):
            new_items.append(item)

    new_items.sort(key=publication_sort_key)
    if not new_items:
        logger.info("No listings published after %s", watermark.isoformat(timespec="minutes"))
        return

    new_items = new_items[:MAX_NEW_PER_RUN]
    last_sent_dt = watermark
    last_sent_ids = watermark_ids.copy()

    for item in new_items:
        send_listing(item)
        dt = parse_publication_timestamp(item["published_at"])
        if dt > last_sent_dt:
            last_sent_dt = dt
            last_sent_ids = {item["id"]}
        elif dt == last_sent_dt:
            last_sent_ids.add(item["id"])
        state["sent_ids"] = sorted(set(state.get("sent_ids", [])) | {item["id"]})[-2500:]

    state.update(
        {
            "initialized": True,
            "last_published_at": last_sent_dt.isoformat(timespec="minutes"),
            "last_published_ids": sorted(last_sent_ids),
        }
    )
    save_state(state)
    logger.info("Done. New listings sent: %s; watermark=%s", len(new_items), state["last_published_at"])


def main() -> None:
    missing = [
        name for name, value in (("OLX_URL", OLX_URL), ("BOT_TOKEN", BOT_TOKEN), ("CHAT_ID", CHAT_ID)) if not value
    ]
    if missing:
        raise RuntimeError("Missing required environment variables: " + ", ".join(missing))

    logger.info("Starting OLX monitor in mode=%s", MODE)
    state = load_state()
    listings = scrape_olx()

    if MODE == "bootstrap_today":
        bootstrap_today(listings, state)
        return
    if MODE == "monitor":
        monitor(listings, state)
        return
    raise RuntimeError(f"Unknown MODE: {MODE}")


if __name__ == "__main__":
    main()
