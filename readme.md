# bot used for specialty discord server, logs voice chat joins and leaves, and can see if anyone is on a minecraft server

- Requires `discord-py-interactions`
- Music support for YouTube & YouTube Music via `/play`, `/skip`, `/pause`, `/resume`, `/queue`, and `/stop` powered by Lavalink
- Slash command `/mcstatus` reports the Agartha server status
- Mentions directed at the bot route messages through OpenRouter's `z-ai/glm-4.5-air:free` model with lightweight per-user memory
- Requires access to a running Lavalink node (remote is fine) – configure host, port, password, region, and SSL settings in `.env`

## Environment

Copy `.env.default` to `.env` and fill in:

- `BOT_TOKEN_MAIN` / `BOT_TOKEN_DEV`
- `LOG_CHANNEL_ID`
- Optional `MUSIC_DJ_ROLE_ID` to block a specific role from using music commands (leave blank to allow anyone)
- `LAVALINK_HOST`, `LAVALINK_PORT`, `LAVALINK_PASSWORD` plus optional `LAVALINK_REGION`/`LAVALINK_SSL` so the bot can reach your Lavalink server
- `OPENROUTER_API_KEY` (needed for AI chat)
- Optional `OPENROUTER_SITE_URL` and `OPENROUTER_APP_NAME` to identify your bot to OpenRouter
