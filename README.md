# OLX → Telegram monitor

This bot monitors an OLX Poland rental search page and sends listings to Telegram.

## Logic

1. `bootstrap_today` is a one-time manual preview. It scans recent OLX cards, reads the exact publication date from the detail page, and sends up to 10 listings published today.
2. The bot saves a publication watermark such as `2026-09-25T10:05+02:00` plus the IDs published at that exact minute.
3. Later `monitor` runs send only listings published **after** that watermark. Example: after the last sent listing at `10:05`, a later run sends `10:06`, `10:07`, etc., and ignores yesterday's listings even if OLX refreshes them.
4. `MAX_NEW_PER_RUN` defaults to 10. The oldest new item is sent first.
5. The excluded Warsaw districts are: Praga-Południe, Białołęka, Bielany, Bemowo, Ursus.

## Telegram message

Each listing includes title, price, location, publication date/time, optional modification time, area, photo, and a link button.

## GitHub Secrets

- `OLX_URL`
- `BOT_TOKEN`
- `CHAT_ID`

## Important first run

Because an older repository may already contain the legacy `sent_ids` state, use **Actions → OLX monitor → Run workflow → `bootstrap_today` once** after deploying this version. This ignores the old ID-only history and establishes the new publication-time watermark from today's listings.

After that, let the scheduled `monitor` runs take over.
