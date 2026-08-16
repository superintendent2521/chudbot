# Project structure

- `index.py` is the compatibility entry point (`python3 index.py`).
- `chudbot/app.py` wires configuration, stores, commands, and listeners together.
- `chudbot/commands/` contains Discord slash-command definitions.
- `chudbot/economy/` contains PostgreSQL economy storage, logging, and rewards.
- `chudbot/music/` contains Lavalink playback and session management.
- `chudbot/listeners/` contains Discord gateway event listeners.
- `chudbot/games/` contains dependency-free game rules and data.
- `chudbot/storage/` contains small JSON-backed stores.
- `data/` contains mutable bot configuration and state.
- `tests/` mirrors the feature packages and contains unit tests.

Imports use the full `chudbot.*` package path, so modules can be located from their
feature name and tests exercise the same import paths used in production.
