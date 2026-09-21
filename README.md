# OLX Telegram Monitor

Monitors an OLX Poland rental search page and sends only unseen listings to Telegram.

## Behavior
- First `monitor` run creates a baseline from current OLX cards and sends **nothing**.
- Later `monitor` runs send only listing IDs that were not seen before.
- Excludes: Praga-Południe, Białołęka, Bielany, Bemowo, Ursus.
- Includes title, price, location, area, OLX refresh time, and (when available on the detail page) exact `Data dodania` / `Data modyfikacji`.
- `latest10` is a safe manual preview: it only sends unseen listings and does not send already-known old listings. On an uninitialized state it initializes the baseline and sends nothing.

## GitHub Secrets
- `OLX_URL`
- `BOT_TOKEN`
- `CHAT_ID`

## Run locally
```bash
pip install -r requirements.txt
python -m playwright install --with-deps chromium
python bot.py
```
