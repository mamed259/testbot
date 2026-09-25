# Security

Never commit the Telegram bot token to git. Store it only in GitHub Actions Secrets.

If a token is ever exposed, revoke it in @BotFather and use the replacement token in `BOT_TOKEN`.
