"""Run one dedicated Discord music bot with a tiny private control API."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from interactions import Client, Intents, listen
from interactions.api.events import WebsocketReady

from chudbot.music.runtime import MusicRuntime
from chudbot.music.worker_service import MusicWorkerService


logger = logging.getLogger("chuds.music-worker")


def _runtime() -> MusicRuntime:
    return MusicRuntime(
        logger=logger,
        lavalink_host=os.environ["LAVALINK_HOST"],
        lavalink_port=int(os.getenv("LAVALINK_PORT", "2333")),
        lavalink_password=os.environ["LAVALINK_PASSWORD"],
        lavalink_region=os.getenv("LAVALINK_REGION", "global"),
        lavalink_ssl=os.getenv("LAVALINK_SSL", "false").lower() in {"1", "true", "yes"},
        music_available=True,
        music_dj_role_id=None,
        idle_timeout=int(os.getenv("MUSIC_IDLE_TIMEOUT", "90")),
        voice_connect_timeout=int(os.getenv("VOICE_CONNECT_TIMEOUT", "15")),
        default_player_volume=int(os.getenv("DEFAULT_PLAYER_VOLUME", "50")),
        audio_normalization=os.getenv("AUDIO_NORMALIZATION", "true").lower() in {"1", "true", "yes"},
        normalization_max_amplitude=float(os.getenv("NORMALIZATION_MAX_AMPLITUDE", "0.75")),
    )


class _Handler(BaseHTTPRequestHandler):
    service: MusicWorkerService
    secret: str
    loop: asyncio.AbstractEventLoop

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/health":
            self.send_error(404)
            return
        self._send(200, {"ok": True})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/command" or self.headers.get("Authorization") != f"Bearer {self.secret}":
            self.send_error(401)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            request = json.loads(self.rfile.read(length))
            loop = self.loop or getattr(self.service.client, "loop", None)
            future = asyncio.run_coroutine_threadsafe(self.service.handle(request), loop)
            result = future.result(timeout=35)
            self._send(409 if result.get("busy") else 200, result)
        except Exception as error:
            self._send(500, {"handled": True, "ok": False, "message": str(error)})

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        return


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    bot = Client(token=os.environ["MUSIC_WORKER_TOKEN"], intents=Intents.GUILD_VOICE_STATES)
    runtime = _runtime()
    for listener in runtime.create_gateway_listeners():
        bot.add_listener(listener)

    loop = asyncio.get_event_loop()
    _Handler.service = MusicWorkerService(runtime, bot)
    _Handler.secret = os.environ["MUSIC_WORKER_SECRET"]
    _Handler.loop = loop

    @listen(WebsocketReady)
    async def on_worker_ready(_event: WebsocketReady) -> None:
        _Handler.loop = asyncio.get_running_loop()
        logger.info("Music worker control API bound to the Discord event loop")

    bot.add_listener(on_worker_ready)
    server = ThreadingHTTPServer((os.getenv("MUSIC_WORKER_HOST", "127.0.0.1"), int(os.getenv("MUSIC_WORKER_PORT", "8787"))), _Handler)
    logger.info("Music worker control API listening on %s", server.server_address)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    bot.start()


if __name__ == "__main__":
    main()
