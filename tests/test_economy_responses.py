import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from chudbot.economy.responses import defer_ping, send_ping


class EconomyResponseTests(unittest.IsolatedAsyncioTestCase):
    async def test_defers_interaction_with_requested_visibility(self):
        ctx = SimpleNamespace(
            command_name="work",
            defer=AsyncMock(),
        )

        await defer_ping(ctx, ephemeral=True)

        ctx.defer.assert_awaited_once_with(ephemeral=True)

    async def test_component_defer_can_edit_original_response(self):
        ctx = SimpleNamespace(
            command_name="sellitem",
            defer=AsyncMock(),
        )

        await defer_ping(ctx, edit_origin=True)

        ctx.defer.assert_awaited_once_with(ephemeral=False, edit_origin=True)

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

    async def test_does_not_try_to_change_public_defer_to_ephemeral(self):
        ctx = SimpleNamespace(
            author=SimpleNamespace(id=42, mention="<@42>"),
            deferred=True,
            ephemeral=False,
            send=AsyncMock(),
        )

        await send_ping(ctx, "Try again later.", ephemeral=True)

        ctx.send.assert_awaited_once_with("<@42> Try again later.")

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
