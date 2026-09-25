# Security

Never commit `BOT_TOKEN` or `CHAT_ID` values to the repository. Keep them in GitHub Actions Secrets.

If a Telegram bot token is ever exposed publicly, revoke it in BotFather and generate a new token.
