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

from chudbot.economy.store import PostgresEconomyStore

LOGGER = logging.getLogger("chuds.bot.web")
MAX_MESSAGE_BYTES = 64 * 1024
MAX_ID = 2**63 - 1
ECONOMY_STORE_KEY = web.AppKey("economy_store", PostgresEconomyStore)
WEB_PASSWORD_KEY = web.AppKey("web_password", str)
WEB_PASSWORD_HASH_KEY = web.AppKey("web_password_hash", str)
WEB_MAX_TRANSFER_KEY = web.AppKey("web_max_transfer", int)
WEB_UI_DIR = os.path.dirname(__file__)


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
    if value is None or isinstance(value, (bool, float)):
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
        self.identity: dict[str, int] | None = None

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
            authorized = False
            if isinstance(payload, dict) and payload.get("type") == "auth" and isinstance(payload.get("password"), str):
                authorized = _password_matches(payload["password"], self.password, self.password_hash)
            if isinstance(payload, dict) and payload.get("type") == "auth" and isinstance(payload.get("code"), str):
                identity = await self.store.web_link_for_code(payload["code"])
                if identity:
                    self.identity = identity
                    authorized = True
                else:
                    await self.store.web_register_code(payload["code"])
                    await ws.send_json({"type": "registration_pending", "message": "Run /register with this code in Discord."})
            if not authorized:
                await ws.send_json({"type": "error", "code": "unauthorized"})
                await ws.close(code=4003, message=b"unauthorized")
                return ws
            auth_result = {"type": "auth_ok", "protocol": 2}
            if self.identity:
                auth_result["guild_id"] = self.identity["guild_id"]
                auth_result["user_id"] = self.identity["user_id"]
            await ws.send_json(auth_result)
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
        guild_id = self.identity["guild_id"] if self.identity else _positive_int(payload, "guild_id")
        user_id = self.identity["user_id"] if self.identity else _positive_int(payload, "user_id")
        if operation == "balance":
            balance = await self.store.peek_balance(guild_id, user_id)
            return {"type": "balance", "guild_id": guild_id, "user_id": user_id, "balance": balance or 0}
        if operation == "profile":
            return {"type": "profile", **await self.store.web_profile(guild_id, user_id)}
        if operation == "recent_activity":
            return {"type": "recent_activity", "items": await self.store.web_recent_activity(guild_id, user_id, 5)}
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
        if operation == "leaderboard":
            limit = _positive_int(payload, "limit")
            if limit > 25:
                raise ValueError("limit exceeds maximum of 25")
            leaderboard = await self.store.leaderboard(guild_id, limit)
            return {"type": "leaderboard", "guild_id": guild_id, "leaderboard": [{"user_id": user_id, "balance": balance} for user_id, balance in leaderboard]}
        raise ValueError("unknown operation")


async def health(_: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def websocket_handler(request: web.Request) -> web.WebSocketResponse:
    return await EconomyWebSocket(request).run()


async def index(_: web.Request) -> web.FileResponse:
    return web.FileResponse(os.path.join(WEB_UI_DIR, "web_index.html"))


async def asset_css(_: web.Request) -> web.FileResponse:
    return web.FileResponse(os.path.join(WEB_UI_DIR, "web_style.css"))


async def asset_js(_: web.Request) -> web.FileResponse:
    return web.FileResponse(os.path.join(WEB_UI_DIR, "web_app.js"))


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
    app.router.add_get("/", index)
    app.router.add_get("/assets/app.css", asset_css)
    app.router.add_get("/assets/app.js", asset_js)
    return app
