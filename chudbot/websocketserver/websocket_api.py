"""Authenticated WebSocket API for the web economy client."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import time
from collections import deque
from typing import Any

from aiohttp import WSMsgType, web

LOGGER = logging.getLogger("chuds.bot.web")
MAX_MESSAGE_BYTES = 64 * 1024
MAX_ID = 2**63 - 1
ECONOMY_STORE_KEY = web.AppKey("economy_store", Any)
WEB_PASSWORD_KEY = web.AppKey("web_password", str)
WEB_PASSWORD_HASH_KEY = web.AppKey("web_password_hash", str)
WEB_MAX_TRANSFER_KEY = web.AppKey("web_max_transfer", int)


def _password_matches(candidate: str, configured: str, configured_hash: str) -> bool:
    if configured_hash:
        try:
            algorithm, iterations, salt, expected = configured_hash.split("$", 3)
            if algorithm != "pbkdf2_sha256":
                return False
            derived = hashlib.pbkdf2_hmac("sha256", candidate.encode(), salt.encode(), int(iterations))
            return hmac.compare_digest(base64.urlsafe_b64encode(derived).decode().rstrip("="), expected)
        except (ValueError, TypeError):
            return False
    return bool(configured) and hmac.compare_digest(candidate, configured)


def _positive_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError(f"{key} must be an integer")
    value = int(value)
    if not 1 <= value <= MAX_ID:
        raise ValueError(f"{key} is out of range")
    return value


class EconomyWebSocket:
    def __init__(self, request: web.Request) -> None:
        self.request = request
        self.store = request.app[ECONOMY_STORE_KEY]
        self.password = request.app[WEB_PASSWORD_KEY]
        self.password_hash = request.app[WEB_PASSWORD_HASH_KEY]
        self.max_transfer = request.app[WEB_MAX_TRANSFER_KEY]
        self.recent_requests: deque[float] = deque()

    def _rate_limited(self) -> bool:
        now = time.monotonic()
        while self.recent_requests and now - self.recent_requests[0] >= 60:
            self.recent_requests.popleft()
        if len(self.recent_requests) >= 60:
            return True
        self.recent_requests.append(now)
        return False

    async def run(self) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(max_msg_size=MAX_MESSAGE_BYTES)
        await ws.prepare(self.request)
        try:
            try:
                message = await asyncio.wait_for(ws.receive(), timeout=15)
            except asyncio.TimeoutError:
                await ws.close(code=4001, message=b"authentication timeout")
                return ws
            if message.type != WSMsgType.TEXT:
                await ws.close(code=4002, message=b"authentication required")
                return ws
            try:
                payload = json.loads(message.data)
            except json.JSONDecodeError:
                await ws.send_json({"type": "error", "code": "invalid_json"})
                await ws.close(code=4002, message=b"authentication required")
                return ws
            if not isinstance(payload, dict) or payload.get("type") != "auth" or not isinstance(payload.get("password"), str) or not _password_matches(payload["password"], self.password, self.password_hash):
                await ws.send_json({"type": "error", "code": "unauthorized"})
                await ws.close(code=4003, message=b"unauthorized")
                return ws
            await ws.send_json({"type": "auth_ok", "protocol": 1})
            async for message in ws:
                if message.type in (WSMsgType.ERROR, WSMsgType.CLOSE, WSMsgType.CLOSED):
                    break
                if message.type != WSMsgType.TEXT:
                    await ws.send_json({"type": "error", "code": "text_required"})
                    continue
                if self._rate_limited():
                    await ws.send_json({"type": "error", "code": "rate_limited"})
                    continue
                try:
                    result = await self._dispatch(json.loads(message.data))
                except (ValueError, TypeError, json.JSONDecodeError) as error:
                    await ws.send_json({"type": "error", "code": "invalid_request", "message": str(error)})
                except Exception:
                    LOGGER.exception("Web economy request failed")
                    await ws.send_json({"type": "error", "code": "internal_error"})
                else:
                    await ws.send_json(result)
        finally:
            if not ws.closed:
                await ws.close()
        return ws

    async def _dispatch(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("request must be an object")
        operation = payload.get("type")
        guild_id = _positive_int(payload, "guild_id")
        user_id = _positive_int(payload, "user_id")
        if operation == "balance":
            balance = await self.store.peek_balance(guild_id, user_id)
            return {"type": "balance", "guild_id": guild_id, "user_id": user_id, "balance": balance or 0}
        if operation == "gift":
            recipient_id = _positive_int(payload, "recipient_id")
            amount = _positive_int(payload, "amount")
            if recipient_id == user_id:
                raise ValueError("giver and recipient must differ")
            if amount > self.max_transfer:
                raise ValueError("amount exceeds transfer limit")
            result = await self.store.gift(guild_id, user_id, recipient_id, amount)
            return {"type": "gift", "accepted": result.accepted, "amount": result.amount, "giver_balance": result.giver_balance, "recipient_balance": result.recipient_balance}
        if operation == "mint":
            amount = _positive_int(payload, "amount")
            if amount > self.max_transfer:
                raise ValueError("amount exceeds mint limit")
            balance = await self.store.mint(guild_id, user_id, amount)
            return {"type": "mint", "accepted": True, "amount": amount, "user_id": user_id, "balance": balance}
        raise ValueError("unknown operation")


async def health(_: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def websocket_handler(request: web.Request) -> web.WebSocketResponse:
    return await EconomyWebSocket(request).run()


def create_web_app(economy_store: Any, *, password: str | None = None, password_hash: str | None = None, max_transfer: int = 1_000_000) -> web.Application:
    password = password if password is not None else os.getenv("WEB_WS_PASSWORD", "")
    password_hash = password_hash if password_hash is not None else os.getenv("WEB_WS_PASSWORD_HASH", "")
    if bool(password) == bool(password_hash):
        raise ValueError("Set exactly one of WEB_WS_PASSWORD or WEB_WS_PASSWORD_HASH")
    if max_transfer < 1:
        raise ValueError("max_transfer must be positive")
    app = web.Application()
    app[ECONOMY_STORE_KEY] = economy_store
    app[WEB_PASSWORD_KEY] = password
    app[WEB_PASSWORD_HASH_KEY] = password_hash
    app[WEB_MAX_TRANSFER_KEY] = int(max_transfer)
    app.router.add_get("/health", health)
    app.router.add_get("/ws", websocket_handler)
    return app
