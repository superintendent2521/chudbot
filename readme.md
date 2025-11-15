# bot used for specialty discord server, logs voice chat joins and leaves, and can see if anyone is on a minecraft server

- Requires `discord-py-interactions`
- Supports slash command `/mcstatus`
- Mentions directed at the bot route messages through OpenRouter's `z-ai/glm-4.5-air:free` model with lightweight per-user memory

## Environment

Copy `.env.default` to `.env` and fill in:

- `BOT_TOKEN_MAIN` / `BOT_TOKEN_DEV`
- `LOG_CHANNEL_ID`
- `OPENROUTER_API_KEY` (needed for AI chat)
- Optional `OPENROUTER_SITE_URL` and `OPENROUTER_APP_NAME` to identify your bot to OpenRouter
