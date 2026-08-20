# Chudbot

Chudbot is a Discord bot with moderation, reaction-board, economy, spaceflight,
and Lavalink-powered music features.

## Setup

1. Create a virtual environment and install dependencies:

   ```bash
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```

2. Copy `.env.default` to `.env` and fill in the bot token and service settings.

3. Start the bot from the repository root:

   ```bash
   .venv/bin/python index.py
   ```

## Development environment

Install Docker Desktop, leave it running with the Linux engine enabled, and
run the complete local stack from PowerShell. The
launcher automatically creates `.venv` and installs `requirements.txt` the
first time:

```powershell
\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
.\scripts\start-dev.ps1
```

This starts the development PostgreSQL container, the bot with
`CHUDBOT_ENVIRONMENT=dev`, and the WSS server. Set `DEV_FRONTEND_COMMAND` in
`.env` (for example, `npm run dev`) to add frontend startup automatically, or
use `.\scripts\start-dev.ps1 -NoFrontend`. Use `-KeepDatabase` to leave
PostgreSQL running after the local processes stop.

The launcher writes bot and web-server output to `logs/dev-*.log`, which is
useful when a child process exits during startup.

For local development, `WEB_WS_ALLOW_INSECURE_DEV=true` permits `ws://` when
`WEB_WS_TLS_CERT` and `WEB_WS_TLS_KEY` are empty. Configure both certificate
paths in `.env` to use WSS locally; production always requires TLS.

## Development

Run the unit tests with:

```bash
python3 -m unittest discover -v
```

Application code lives in the `chudbot` package and is grouped by feature:

- `commands` — slash-command definitions
- `economy` — PostgreSQL storage, logging, and message rewards
- `games` — dependency-free game rules and data
- `listeners` — Discord gateway event handlers
- `music` — Lavalink playback and session management
- `storage` — small JSON-backed stores

Runtime JSON data lives in `data`. See [docs/structure.md](docs/structure.md) for
the full repository map.

The service policies are available at [docs/terms-of-service.md](docs/terms-of-service.md)
and [docs/privacy-policy.md](docs/privacy-policy.md). Replace the bracketed
operator, contact, jurisdiction, and retention placeholders before publishing.

## Web economy WebSocket

The optional web endpoint is started separately with `python -m chudbot.websocketserver.web_server`.
It serves `wss://HOST:PORT/ws` and requires TLS certificate/key paths plus exactly
one of `WEB_WS_PASSWORD` or the recommended PBKDF2 `WEB_WS_PASSWORD_HASH`.
Generate the latter with `python -m chudbot.websocketserver.websocket_password`, then put the
result in `.env` (which is ignored by git). The first WebSocket message must be
`{"type":"auth","password":"..."}`. After `auth_ok`, supported messages are:

```json
{"type":"balance","guild_id":123,"user_id":456}
{"type":"gift","guild_id":123,"user_id":456,"recipient_id":789,"amount":10}
{"type":"mint","guild_id":123,"user_id":456,"amount":100}
```

This moves the bot's virtual coins through the existing transaction-safe store;
`mint` creates virtual coins and is available to any client with the shared
WebSocket password. It is not a real-money payment API. Keep the endpoint behind a firewall or
reverse proxy, use a certificate trusted by the web host, and never expose it
without TLS.

## Crafting recipes

Crafting recipes are defined in `chudbot/economy/crafting.py`. Add a
`CraftingRecipe` to `RECIPES` with a stable key, its output item, and the
required inventory item keys; `/craft` discovers it automatically.
