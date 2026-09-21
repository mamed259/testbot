# OLX → Telegram monitor

Checks the configured OLX search page with Playwright and sends listings to Telegram.

## Current filters

The scraper skips these Warsaw districts:

- Praga-Południe
- Białołęka
- Bielany
- Bemowo

The Telegram message includes:

- title
- price
- location
- OLX date/status, e.g. `Odświeżono dzisiaj o 23:15`
- size when available
- listing image when available
- button to open the listing

## Modes

- `monitor`: normal mode. On the first run it stores the existing listings and sends only future new listings.
- `latest10`: manual mode. Sends the 10 newest listings that pass the location filter.

## GitHub Actions

Add repository secrets:

- `BOT_TOKEN`
- `CHAT_ID`
- `OLX_URL`

Run **Actions → OLX monitor → Run workflow**. Choose `latest10` to preview the newest filtered listings in Telegram, or `monitor` for normal monitoring.

Scheduled runs use `monitor` automatically.


Excluded districts currently include Praga-Południe, Białołęka, Bielany, Bemowo, and Ursus.
