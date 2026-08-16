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

## Crafting recipes

Crafting recipes are defined in `chudbot/economy/crafting.py`. Add a
`CraftingRecipe` to `RECIPES` with a stable key, its output item, and the
required inventory item keys; `/craft` discovers it automatically.
