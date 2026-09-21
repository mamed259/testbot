# OLX → Telegram monitor

This project checks an OLX Poland search page every 5 minutes and sends only new listings to a Telegram chat.

## Your OLX filter

The default URL for your current setup is:

```text
https://www.olx.pl/nieruchomosci/mieszkania/wynajem/warszawa/?search%5Bdist%5D=5&search%5Bfilter_float_price%3Ato%5D=2900&search%5Border%5D=created_at%3Adesc&view=grid
```

## What it does

- Opens the OLX search page with Playwright/Chromium.
- Reads the newest listing cards.
- Uses the OLX listing ID/URL for deduplication.
- On the first run, it creates a baseline and does not spam you with existing ads.
- Later runs send only listings that were not sent before.
- Telegram messages contain title, price, and an **Open listing** button.
- When an image URL is available, the bot tries to send the image too and falls back to a normal message if Telegram rejects the image.
- State is kept in `sent.json` and committed back to the repository by GitHub Actions.

## GitHub setup

1. Create a new GitHub repository.
2. Upload all files from this project.
3. Go to **Settings → Secrets and variables → Actions → New repository secret**.
4. Create these three secrets:

```text
OLX_URL
BOT_TOKEN
CHAT_ID
```

For `OLX_URL`, use your OLX search URL.

For `BOT_TOKEN`, use the **new** token from BotFather. Never commit it to the repository.

For `CHAT_ID`, use the chat ID that successfully received your `Hello` test.

5. Open **Actions → OLX monitor → Run workflow** once manually.

The first run creates the baseline. From the next check onward, new OLX listings are sent to Telegram.

## Important notes

GitHub Actions supports scheduled workflows down to a 5-minute interval, but scheduled runs can be delayed during periods of high GitHub Actions load. The workflow is therefore approximately every 5 minutes, not a hard real-time guarantee.

The workflow uses the current GitHub-maintained `actions/checkout` and `actions/setup-python` major versions.

## Local test

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

export OLX_URL='https://www.olx.pl/nieruchomosci/mieszkania/wynajem/warszawa/?search%5Bdist%5D=5&search%5Bfilter_float_price%3Ato%5D=2900&search%5Border%5D=created_at%3Adesc&view=grid'
export BOT_TOKEN='YOUR_NEW_TOKEN'
export CHAT_ID='YOUR_CHAT_ID'
python bot.py
```

To send currently visible listings during the first run instead of creating a baseline:

```bash
export SEND_EXISTING_ON_FIRST_RUN=true
```
# testbot
