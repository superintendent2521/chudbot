import base64
import hashlib
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from chudbot.websocketserver.websocket_api import _password_matches, create_web_app


class WebSocketApiTests(unittest.IsolatedAsyncioTestCase):
    def test_password_hash_verification(self) -> None:
        salt = "test-salt"
        digest = hashlib.pbkdf2_hmac("sha256", b"secret", salt.encode(), 1_000)
        encoded = base64.urlsafe_b64encode(digest).decode().rstrip("=")
        password_hash = f"pbkdf2_sha256$1000${salt}${encoded}"
        self.assertTrue(_password_matches("secret", "", password_hash))
        self.assertFalse(_password_matches("wrong", "", password_hash))

    def test_app_requires_one_password_source(self) -> None:
        store = object()
        with self.assertRaises(ValueError):
            create_web_app(store, password="one", password_hash="two")
        with self.assertRaises(ValueError):
            create_web_app(store, password="", password_hash="")

    async def test_dispatches_balance_and_validates_gift(self) -> None:
        from chudbot.websocketserver.websocket_api import EconomyWebSocket

        store = SimpleNamespace(
            peek_balance=AsyncMock(return_value=123),
            gift=AsyncMock(),
            mint=AsyncMock(return_value=223),
        )
        app = create_web_app(store, password="secret")
        request = SimpleNamespace(
            app=app,
        )
        api = EconomyWebSocket(request)
        result = await api._dispatch({"type": "balance", "guild_id": 1, "user_id": 2})
        self.assertEqual(result["balance"], 123)
        minted = await api._dispatch({"type": "mint", "guild_id": 1, "user_id": 2, "amount": 100})
        self.assertEqual(minted["balance"], 223)
        with self.assertRaises(ValueError):
            await api._dispatch({"type": "gift", "guild_id": 1, "user_id": 2, "recipient_id": 2, "amount": 1})


if __name__ == "__main__":
    unittest.main()
