# bot used for specialty discord server, logs voice chat joins and leaves, and can see if anyone is on a minecraft server

- Requires `discord-py-interactions`
- Music support for YouTube & YouTube Music via `/play`, `/skip`, `/pause`, `/resume`, `/queue`, and `/stop` powered by Lavalink
- Slash command `/mcstatus` reports the Agartha server status
- `/ban` now requires a reason and writes a ban log to the configured channel
- `/faq`, `/faqset`, and `/faqremove` provide JSON-backed FAQ entries stored in `faq_entries.json`
- `#github` is treated as a webhook-only channel and non-webhook posts are removed
- Requires access to a running Lavalink node (remote is fine) – configure host, port, password, region, and SSL settings in `.env`

## Environment

Copy `.env.default` to `.env` and fill in:

- `BOT_TOKEN_MAIN` / `BOT_TOKEN_DEV`
- `LOG_CHANNEL_ID`
- Optional `GITHUB_WEBHOOK_BOT_ID` if your GitHub posts come from a bot user instead of a Discord webhook
- Optional `MUSIC_DJ_ROLE_ID` to block a specific role from using music commands (leave blank to allow anyone)
- `LAVALINK_HOST`, `LAVALINK_PORT`, `LAVALINK_PASSWORD` plus optional `LAVALINK_REGION`/`LAVALINK_SSL` so the bot can reach your Lavalink server
