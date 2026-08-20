"""Run the authenticated WSS economy endpoint as a separate process."""

from __future__ import annotations

import asyncio
import os
import ssl

if os.name == "nt":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from aiohttp import web
from dotenv import load_dotenv

from chudbot.websocketserver.websocket_api import create_web_app


def main() -> None:
    load_dotenv()
    cert = os.getenv("WEB_WS_TLS_CERT", "").strip()
    key = os.getenv("WEB_WS_TLS_KEY", "").strip()
    allow_insecure_dev = (
        os.getenv("CHUDBOT_ENVIRONMENT", "main").strip().lower() == "dev"
        and os.getenv("WEB_WS_ALLOW_INSECURE_DEV", "false").strip().lower()
        in {"1", "true", "yes"}
    )
    if (not cert or not key) and not allow_insecure_dev:
        raise RuntimeError("WEB_WS_TLS_CERT and WEB_WS_TLS_KEY are required for WSS")
    # The public server is deliberately store-less. All economy operations are
    # relayed over the single authenticated bot-backend connection.
    app = create_web_app(
        password="relay-disabled",
        backend_url=os.getenv("WEB_BACKEND_URL", ""),
        backend_secret=os.getenv("WEB_BACKEND_SECRET", ""),
    )
    tls = None
    if cert and key:
        tls = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        tls.load_cert_chain(cert, key)
    elif allow_insecure_dev:
        print("WARNING: running the development WebSocket server without TLS (ws://)")
    web.run_app(app, host=os.getenv("WEB_WS_HOST", "127.0.0.1"), port=int(os.getenv("WEB_WS_PORT", "8765")), ssl_context=tls)


if __name__ == "__main__":
    main()
