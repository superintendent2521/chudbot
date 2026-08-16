import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from chudbot.economy.responses import send_ping


class EconomyResponseTests(unittest.IsolatedAsyncioTestCase):
    async def test_prefixes_response_with_author_mention(self):
        ctx = SimpleNamespace(
            author=SimpleNamespace(id=42, mention="<@42>"),
            send=AsyncMock(return_value="message"),
        )

        result = await send_ping(ctx, "You earned 10 coins.", ephemeral=True)

        self.assertEqual(result, "message")
        ctx.send.assert_awaited_once_with(
            "<@42> You earned 10 coins.", ephemeral=True
        )

    async def test_does_not_duplicate_existing_author_mention(self):
        ctx = SimpleNamespace(
            author=SimpleNamespace(id=42, mention="<@42>"),
            send=AsyncMock(),
        )

        await send_ping(ctx, "<@42> Your balance is 10 coins.")

        ctx.send.assert_awaited_once_with("<@42> Your balance is 10 coins.")

    async def test_allows_author_when_other_mentions_are_suppressed(self):
        ctx = SimpleNamespace(
            author=SimpleNamespace(id=42, mention="<@42>"),
            send=AsyncMock(),
        )

        await send_ping(ctx, "Leaderboard", allowed_mentions={"parse": []})

        ctx.send.assert_awaited_once_with(
            "<@42> Leaderboard",
            allowed_mentions={"parse": [], "users": ["42"]},
        )

    async def test_autocomplete_choices_pass_through_without_content(self):
        ctx = SimpleNamespace(send=AsyncMock())
        choices = [{"name": "Item", "value": "item"}]

        await send_ping(ctx, choices=choices)

        ctx.send.assert_awaited_once_with(choices=choices)


if __name__ == "__main__":
    unittest.main()
