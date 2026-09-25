# OLX Telegram monitor v8

Monitors an OLX Poland rental search page and sends only newly observed listing IDs to Telegram.

## Behaviour

- First run: sends the freshest current cards (up to 10) and marks the whole current search snapshot as seen. This prevents yesterday/older cards from being sent later.
- Later runs: sends only IDs that were not present in the previous baseline.
- If more than 10 new listings appear between runs, only 10 are sent; the remaining new IDs are left unseen and will be sent in later runs.
- Excludes: Praga-Południe, Białołęka, Bielany, Bemowo, Ursus.
- Displays price, location, size, photo, link, and publication date when OLX exposes it. Relative OLX text such as `Dodane dzisiaj o 10:05` is parsed.
- The detail-page date lookup is best-effort and never makes the whole run fail.

## GitHub Actions secrets

- `OLX_URL`
- `BOT_TOKEN`
- `CHAT_ID`

## Manual modes

- `monitor`: normal operation.
- `bootstrap`: explicitly reset the current baseline and send the current freshest cards once.

The workflow is scheduled every 5 minutes.
