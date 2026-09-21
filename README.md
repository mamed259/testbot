# OLX → Telegram monitor

Checks the configured OLX search page with Playwright and sends new listings to Telegram.

## Modes

- `monitor`: normal mode. On the first run it stores the existing listings and sends only future new listings.
- `latest10`: manual mode. Sends the 10 newest listings currently visible on OLX, including the listing image when available.

## GitHub Actions

Add repository secrets:

- `BOT_TOKEN`
- `CHAT_ID`
- `OLX_URL`

Run **Actions → OLX monitor → Run workflow**. Choose `latest10` to preview the newest 10 listings in Telegram, or `monitor` for normal monitoring.

Scheduled runs use `monitor` automatically.
