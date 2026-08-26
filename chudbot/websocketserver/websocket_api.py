"""Authenticated WebSocket API for the web economy client."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import random
import ssl
import time
import uuid
from collections import deque
from typing import Any

from aiohttp import ClientSession, WSMsgType, web

from chudbot.economy.store import PostgresEconomyStore
from chudbot.games.spaceflight_dumpster import (
    EQUIPMENT_BY_KEY,
    LOOT_BY_KEY,
    hazard_chance,
    locations_for_equipment,
    lose_half,
    resolve_equipment,
    roll_loot,
)
from chudbot.economy.crafting import CRAFTED_ITEMS_BY_KEY

LOGGER = logging.getLogger("chuds.bot.web")
MAX_MESSAGE_BYTES = 64 * 1024
MAX_ID = 2**63 - 1
ECONOMY_STORE_KEY = web.AppKey("economy_store", PostgresEconomyStore)
WEB_PASSWORD_KEY = web.AppKey("web_password", str)
WEB_PASSWORD_HASH_KEY = web.AppKey("web_password_hash", str)
WEB_MAX_TRANSFER_KEY = web.AppKey("web_max_transfer", int)
WEB_BRIDGE_KEY = web.AppKey("web_bridge", object)
WEB_UI_DIR = os.path.dirname(__file__)
SALVAGE_MAX_ROUNDS = 3
SALVAGE_MIN_EQUIPMENT_USES = 2
SALVAGE_MAX_EQUIPMENT_USES = 4


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
        self.salvage: dict[str, Any] | None = None

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
        if operation == "salvage_options":
            inventory = await self.store.inventory(guild_id, user_id)
            quantities = {entry.item_key: entry.quantity for entry in inventory}
            access_card = EQUIPMENT_BY_KEY["access_card"]
            access_available = quantities.get("access_card", 0) > 0
            if not access_available:
                access = await self.store.equipment_availability(guild_id, user_id, "access_card")
                access_available = access.uses_remaining > 0
            location_rule = access_card if access_available else None
            return {
                "type": "salvage_options",
                "locations": [
                    {"key": location.key, "name": location.name, "description": location.description, "emoji": location.emoji}
                    for location in locations_for_equipment(location_rule)
                ],
                "equipment": [
                    {
                        "key": rule.item_key,
                        "name": self._item_descriptor(rule.item_key).name,
                        "emoji": self._item_descriptor(rule.item_key).emoji,
                        "description": rule.description,
                        "available": quantities.get(rule.item_key, 0),
                    }
                    for rule in EQUIPMENT_BY_KEY.values()
                ],
            }
        if operation == "salvage_start":
            if self.salvage is not None:
                raise ValueError("a salvage run is already active")
            location_key = payload.get("location")
            if not isinstance(location_key, str):
                raise ValueError("location is required")
            equipment_key = payload.get("equipment")
            equipment_rule = None
            if equipment_key:
                if not isinstance(equipment_key, str):
                    raise ValueError("equipment must be a string")
                equipment_rule = resolve_equipment(equipment_key)
                if equipment_rule is None:
                    raise ValueError("unknown equipment")
                availability = await self.store.equipment_availability(guild_id, user_id, equipment_rule.item_key)
                if availability.inventory_quantity < 1 and availability.uses_remaining < 1:
                    raise ValueError("that equipment is not available")
            started = await self.store.start_activity(guild_id, user_id, "dumpster")
            if not started.started:
                return {"type": "salvage_cooldown", "retry_after": started.retry_after}
            run_locations = locations_for_equipment(
                EQUIPMENT_BY_KEY["access_card"] if equipment_rule and equipment_rule.item_key == "access_card" else None
            )
            location = next((item for item in run_locations if item.key == location_key), None)
            if location is None:
                raise ValueError("that salvage location is locked or invalid")
            equipment_use = None
            if equipment_rule is not None:
                equipment_use = await self.store.use_inventory_equipment(
                    guild_id, user_id, equipment_rule.item_key,
                    random.randint(SALVAGE_MIN_EQUIPMENT_USES, SALVAGE_MAX_EQUIPMENT_USES),
                    source="web_salvage_equipment",
                )
                if equipment_use.status != "used":
                    raise ValueError("that equipment is no longer available")
            self.salvage = {
                "guild_id": guild_id,
                "user_id": user_id,
                "location": location,
                "equipment": equipment_rule,
                "haul": {},
                "round": 0,
                "max_rounds": 5 + (equipment_rule.extra_rounds if equipment_rule else 0),
                "fuel": 6 + (equipment_rule.extra_fuel if equipment_rule else 0),
                "hull": 3,
                "combo": 0,
                "scanned": False,
            }
            return self._salvage_state("salvage_started", balance=started.balance)
        if operation == "salvage_action":
            if self.salvage is None:
                raise ValueError("no salvage run is active")
            action = payload.get("action")
            if action not in {"scan", "mine", "deep", "leave"}:
                raise ValueError("action must be scan, mine, deep, or leave")
            run = self.salvage
            if action == "scan":
                if run["fuel"] < 1:
                    raise ValueError("the ship has no fuel left")
                run["fuel"] -= 1
                run["scanned"] = True
                risk = hazard_chance(
                    run["location"], deep=False,
                    hazard_reduction=run["equipment"].hazard_reduction if run["equipment"] else 0.0,
                ) * 100
                return {
                    **self._salvage_state("salvage_scan"),
                    "scan": {"risk_percent": round(risk, 1), "rare_bonus": 0.25},
                    "message": f"Sensors estimate {risk:.1f}% hull-risk. The next mining action gets a rare-loot bonus.",
                }
            if action == "leave":
                return await self._finish_salvage("salvage_left")
            location = run["location"]
            equipment = run["equipment"]
            fuel_cost = 2 if action == "deep" else 1
            if run["fuel"] < fuel_cost:
                return await self._finish_salvage("salvage_out_of_fuel")
            run["fuel"] -= fuel_cost
            scanned = run["scanned"]
            run["scanned"] = False
            if random.random() < hazard_chance(
                location, deep=action == "deep",
                hazard_reduction=equipment.hazard_reduction if equipment else 0.0,
            ) * (0.55 if scanned else 1.0):
                kept, lost = lose_half(run["haul"], rng=random)
                run["haul"] = kept
                run["hull"] -= 1
                run["combo"] = 0
                if run["hull"] <= 0:
                    result = await self._finish_salvage("salvage_destroyed")
                else:
                    result = self._salvage_state("salvage_hazard_progress")
                result["lost"] = self._items(lost)
                result["hazard"] = True
                result["message"] = "A micrometeorite storm damaged the ship. You lost part of the haul, but escaped." if run["hull"] > 0 else "The ship was critically damaged. The remaining haul was recovered, but the expedition is over."
                return result
            found = roll_loot(
                location, deep=action == "deep", rng=random,
                rarity_bonus=(equipment.rarity_bonus if equipment else 0.0) + (0.25 if scanned else 0.0) + (run["combo"] * 0.10),
            )
            for item in found:
                run["haul"][item.key] = run["haul"].get(item.key, 0) + 1
            run["round"] += 1
            run["combo"] = run["combo"] + 1 if action == "deep" else 0
            if run["round"] >= run["max_rounds"] or run["fuel"] <= 0:
                result = await self._finish_salvage("salvage_complete")
            else:
                result = self._salvage_state("salvage_progress")
            result["found"] = self._items(found)
            return result
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

    @staticmethod
    def _item_descriptor(item_key: str) -> Any:
        return LOOT_BY_KEY.get(item_key) or CRAFTED_ITEMS_BY_KEY[item_key]

    @staticmethod
    def _items(items: Any) -> list[dict[str, Any]]:
        if isinstance(items, dict):
            pairs = items.items()
        else:
            pairs = ((item.key, 1) for item in items)
        return [
            {"key": key, "name": EconomyWebSocket._item_descriptor(key).name, "emoji": EconomyWebSocket._item_descriptor(key).emoji, "quantity": quantity}
            for key, quantity in pairs
        ]

    def _salvage_state(self, message_type: str, *, balance: int | None = None) -> dict[str, Any]:
        assert self.salvage is not None
        run = self.salvage
        result = {
            "type": message_type,
            "round": run["round"],
            "max_rounds": run["max_rounds"],
            "location": {"key": run["location"].key, "name": run["location"].name, "emoji": run["location"].emoji},
            "haul": self._items(run["haul"]),
            "fuel": run["fuel"],
            "hull": run["hull"],
            "combo": run["combo"],
            "scanned": run["scanned"],
        }
        if balance is not None:
            result["balance"] = balance
        return result

    async def _finish_salvage(self, message_type: str) -> dict[str, Any]:
        assert self.salvage is not None
        run = self.salvage
        haul = dict(run["haul"])
        if haul:
            await self.store.add_inventory_items(
                run["guild_id"], run["user_id"], haul, source="web_salvage"
            )
        balance = await self.store.peek_balance(run["guild_id"], run["user_id"])
        result = self._salvage_state(message_type, balance=balance or 0)
        result["saved"] = self._items(haul)
        self.salvage = None
        return result


class WebBackendBridge:
    """One authenticated upstream WebSocket shared by all browser clients."""

    def __init__(self, url: str, secret: str, ca_file: str = "") -> None:
        self.url = url
        self.secret = secret
        self.ca_file = ca_file
        self.session: ClientSession | None = None
        self.socket: Any = None
        self.reader_task: asyncio.Task[None] | None = None
        self.pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self.send_lock = asyncio.Lock()

    async def _connect(self) -> None:
        if self.socket is not None and not self.socket.closed:
            return
        if not self.url or not self.secret:
            raise RuntimeError("WEB_BACKEND_URL and WEB_BACKEND_SECRET are required")
        if self.session is None:
            self.session = ClientSession()
        ssl_context = None
        if self.url.startswith("wss://") and self.ca_file:
            ssl_context = ssl.create_default_context(cafile=self.ca_file)
        self.socket = await self.session.ws_connect(self.url, heartbeat=30, ssl=ssl_context)
        await self.socket.send_json({"type": "internal_auth", "secret": self.secret})
        response = await self.socket.receive()
        if response.type != WSMsgType.TEXT or json.loads(response.data).get("type") != "internal_auth_ok":
            await self.socket.close()
            raise RuntimeError("web backend authentication failed")
        self.reader_task = asyncio.create_task(self._read_loop())

    async def _read_loop(self) -> None:
        try:
            async for message in self.socket:
                if message.type != WSMsgType.TEXT:
                    continue
                payload = json.loads(message.data)
                request_id = payload.get("request_id")
                future = self.pending.pop(request_id, None)
                if future and not future.done():
                    future.set_result(payload)
        except Exception as error:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(error)
            self.pending.clear()
        finally:
            self.socket = None

    async def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        async with self.send_lock:
            await self._connect()
            request_id = uuid.uuid4().hex
            future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
            self.pending[request_id] = future
            await self.socket.send_json({"request_id": request_id, **payload})
        return await future

    async def close(self) -> None:
        if self.socket is not None:
            await self.socket.close()
        if self.reader_task is not None:
            await self.reader_task
        if self.session is not None:
            await self.session.close()


class PublicWebSocket:
    """Public browser endpoint. It has no economy store and only relays requests."""

    def __init__(self, request: web.Request) -> None:
        self.request = request
        self.bridge: WebBackendBridge = request.app[WEB_BRIDGE_KEY]
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
            message = await asyncio.wait_for(ws.receive(), timeout=15)
            if message.type != WSMsgType.TEXT:
                await ws.close(code=4002, message=b"authentication required")
                return ws
            payload = json.loads(message.data)
            if not isinstance(payload, dict) or payload.get("type") != "auth" or not isinstance(payload.get("code"), str):
                await ws.send_json({"type": "error", "code": "unauthorized"})
                await ws.close(code=4003, message=b"registration code required")
                return ws
            auth_frame = await self.bridge.request({"type": "auth", "code": payload["code"]})
            auth = auth_frame.get("result", auth_frame)
            if auth.get("type") == "registration_pending":
                await ws.send_json(auth)
                await ws.close(code=4003, message=b"registration pending")
                return ws
            if auth.get("type") != "auth_ok":
                await ws.send_json({"type": "error", "code": "unauthorized"})
                await ws.close(code=4003, message=b"unauthorized")
                return ws
            self.identity = {"guild_id": int(auth["guild_id"]), "user_id": int(auth["user_id"])}
            await ws.send_json({"type": "auth_ok", "protocol": 3, **self.identity})
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
                    request_payload = json.loads(message.data)
                    if not isinstance(request_payload, dict):
                        raise ValueError("request must be an object")
                    result = await self.bridge.request({
                        "type": "request",
                        "identity": self.identity,
                        "payload": request_payload,
                    })
                    await ws.send_json(result.get("result", result))
                except Exception:
                    LOGGER.exception("WebSocket relay request failed")
                    await ws.send_json({"type": "error", "code": "internal_error"})
        finally:
            if not ws.closed:
                await ws.close()
        return ws


async def health(_: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def websocket_handler(request: web.Request) -> web.WebSocketResponse:
    return await PublicWebSocket(request).run()


async def index(_: web.Request) -> web.FileResponse:
    return web.FileResponse(os.path.join(WEB_UI_DIR, "web_index.html"), headers={"Cache-Control": "no-store"})


async def asset_css(_: web.Request) -> web.FileResponse:
    return web.FileResponse(os.path.join(WEB_UI_DIR, "web_style.css"), headers={"Cache-Control": "no-store"})


async def asset_js(_: web.Request) -> web.FileResponse:
    return web.FileResponse(os.path.join(WEB_UI_DIR, "web_app.js"), headers={"Cache-Control": "no-store"})


def create_web_app(economy_store: Any = None, *, password: str | None = None, password_hash: str | None = None, max_transfer: int = 1_000_000, backend_url: str | None = None, backend_secret: str | None = None, backend_ca_file: str | None = None) -> web.Application:
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
    app[WEB_BRIDGE_KEY] = WebBackendBridge(
        backend_url if backend_url is not None else os.getenv("WEB_BACKEND_URL", ""),
        backend_secret if backend_secret is not None else os.getenv("WEB_BACKEND_SECRET", ""),
        backend_ca_file if backend_ca_file is not None else os.getenv("WEB_BACKEND_CA_FILE", ""),
    )
    app.router.add_get("/health", health)
    app.router.add_get("/ws", websocket_handler)
    app.router.add_get("/", index)
    app.router.add_get("/assets/app.css", asset_css)
    app.router.add_get("/assets/app.js", asset_js)
    async def cleanup(_: web.Application) -> None:
        await app[WEB_BRIDGE_KEY].close()
    app.on_cleanup.append(cleanup)
    return app
