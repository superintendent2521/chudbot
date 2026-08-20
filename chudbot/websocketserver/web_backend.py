"""Private WebSocket economy backend owned by the bot-side service."""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
import ssl
import time
from collections import deque
from typing import Any

from aiohttp import WSMsgType, web
from dotenv import load_dotenv

from chudbot.economy.store import DEFAULT_POSTGRES_URL, PostgresEconomyStore
from chudbot.websocketserver.websocket_api import (
    ECONOMY_STORE_KEY,
    WEB_MAX_TRANSFER_KEY,
    EconomyWebSocket,
)

LOGGER = logging.getLogger("chuds.bot.web_backend")
BACKEND_SECRET_KEY = web.AppKey("backend_secret", str)


class BotBackendWebSocket:
    def __init__(self, request: web.Request) -> None:
        self.request = request
        self.store = request.app[ECONOMY_STORE_KEY]
        self.secret = request.app[BACKEND_SECRET_KEY]
        self.sessions: dict[tuple[int, int], EconomyWebSocket] = {}
        self.rate_limits: dict[tuple[int, int], deque[float]] = {}
        self.locks: dict[tuple[int, int], asyncio.Lock] = {}

    async def run(self) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(max_msg_size=64 * 1024)
        await ws.prepare(self.request)
        try:
            hello = await asyncio.wait_for(ws.receive(), timeout=15)
            if hello.type != WSMsgType.TEXT:
                await ws.close(code=4002, message=b"internal authentication required")
                return ws
            payload = _json(hello.data)
            if payload.get("type") != "internal_auth" or not hmac.compare_digest(
                str(payload.get("secret", "")), self.secret
            ):
                await ws.close(code=4003, message=b"internal authentication failed")
                return ws
            await ws.send_json({"type": "internal_auth_ok"})
            async for message in ws:
                if message.type in (WSMsgType.ERROR, WSMsgType.CLOSE, WSMsgType.CLOSED):
                    break
                if message.type != WSMsgType.TEXT:
                    continue
                payload = _json(message.data)
                request_id = payload.get("request_id")
                try:
                    if payload.get("type") == "auth":
                        result = await self._authenticate(payload)
                    elif payload.get("type") == "request":
                        result = await self._request(payload)
                    else:
                        raise ValueError("unknown internal operation")
                except (ValueError, TypeError) as error:
                    result = {"type": "error", "code": "invalid_request", "message": str(error)}
                except Exception:
                    LOGGER.exception("Internal web backend request failed")
                    result = {"type": "error", "code": "internal_error"}
                await ws.send_json({"request_id": request_id, "result": result})
        finally:
            if not ws.closed:
                await ws.close()
        return ws

    async def _authenticate(self, payload: dict[str, Any]) -> dict[str, Any]:
        code = payload.get("code")
        if not isinstance(code, str):
            raise ValueError("registration code required")
        identity = await self.store.web_link_for_code(code)
        if identity is None:
            await self.store.web_register_code(code)
            return {"type": "registration_pending", "message": "Run /register with this code in Discord."}
        return {"type": "auth_ok", "guild_id": int(identity["guild_id"]), "user_id": int(identity["user_id"])}

    async def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        identity = payload.get("identity")
        request_payload = payload.get("payload")
        if not isinstance(identity, dict) or not isinstance(request_payload, dict):
            raise ValueError("identity and payload are required")
        guild_id = int(identity["guild_id"])
        user_id = int(identity["user_id"])
        key = (guild_id, user_id)
        now = time.monotonic()
        requests = self.rate_limits.setdefault(key, deque())
        while requests and now - requests[0] >= 60:
            requests.popleft()
        if len(requests) >= 60:
            return {"type": "error", "code": "rate_limited"}
        requests.append(now)
        session = self.sessions.get(key)
        if session is None:
            session = EconomyWebSocket(self.request)
            self.sessions[key] = session
        session.identity = {"guild_id": guild_id, "user_id": user_id}
        lock = self.locks.setdefault(key, asyncio.Lock())
        async with lock:
            return await session._dispatch(request_payload)


def _json(raw: str) -> dict[str, Any]:
    import json

    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("message must be an object")
    return payload


async def health(_: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


def create_backend_app(store: PostgresEconomyStore, secret: str) -> web.Application:
    if not secret:
        raise ValueError("WEB_BACKEND_SECRET is required")
    app = web.Application()
    app[ECONOMY_STORE_KEY] = store
    app[WEB_MAX_TRANSFER_KEY] = 1_000_000
    app[BACKEND_SECRET_KEY] = secret
    app.router.add_get("/health", health)
    app.router.add_get("/internal-ws", lambda request: BotBackendWebSocket(request).run())
    return app


def main() -> None:
    load_dotenv()
    cert = os.getenv("WEB_BACKEND_TLS_CERT", "").strip()
    key = os.getenv("WEB_BACKEND_TLS_KEY", "").strip()
    allow_insecure_dev = (
        os.getenv("CHUDBOT_ENVIRONMENT", "main").strip().lower() == "dev"
        and os.getenv("WEB_BACKEND_ALLOW_INSECURE_DEV", "false").strip().lower()
        in {"1", "true", "yes"}
    )
    if (not cert or not key) and not allow_insecure_dev:
        raise RuntimeError("WEB_BACKEND_TLS_CERT and WEB_BACKEND_TLS_KEY are required")
    store = PostgresEconomyStore(os.getenv("ECONOMY_DATABASE_URL") or os.getenv("DATABASE_URL") or DEFAULT_POSTGRES_URL)
    app = create_backend_app(store, os.getenv("WEB_BACKEND_SECRET", ""))

    async def startup(_: web.Application) -> None:
        await store.open()

    async def cleanup(_: web.Application) -> None:
        await store.close()

    app.on_startup.append(startup)
    app.on_cleanup.append(cleanup)
    tls = None
    if cert and key:
        tls = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        tls.load_cert_chain(cert, key)
    elif allow_insecure_dev:
        LOGGER.warning("running the internal web backend without TLS in development")
    web.run_app(app, host=os.getenv("WEB_BACKEND_HOST", "0.0.0.0"), port=int(os.getenv("WEB_BACKEND_PORT", "8766")), ssl_context=tls)


if __name__ == "__main__":
    main()
