import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch


if "interactions" not in sys.modules:
    interactions = types.ModuleType("interactions")
    setattr(interactions, "Client", object)
    setattr(interactions, "listen", lambda _event: lambda callback: callback)
    interactions_api = types.ModuleType("interactions.api")
    interactions_events = types.ModuleType("interactions.api.events")
    interactions_discord = types.ModuleType("interactions.api.events.discord")
    setattr(interactions_discord, "MessageCreate", object)
    sys.modules["interactions"] = interactions
    sys.modules["interactions.api"] = interactions_api
    sys.modules["interactions.api.events"] = interactions_events
    sys.modules["interactions.api.events.discord"] = interactions_discord

from fixupx_link_listener import (
    _extract_status_id,
    _status_has_video,
    create_fixupx_listener,
)


class FakeClientSession:
    def __init__(self, **_kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        pass


class FixupXStatusParsingTests(unittest.TestCase):
    def test_extracts_status_id_from_x_url(self) -> None:
        self.assertEqual(
            _extract_status_id("https://x.com/example/status/1234567890123456789?s=20"),
            "1234567890123456789",
        )

    def test_rejects_non_status_x_url(self) -> None:
        self.assertIsNone(_extract_status_id("https://x.com/example"))


class FixupXVideoDetectionTests(unittest.TestCase):
    def test_text_only_status_is_not_video(self) -> None:
        self.assertFalse(_status_has_video({"media": {}}))

    def test_photo_status_is_not_video(self) -> None:
        self.assertFalse(
            _status_has_video({"media": {"photos": [{"type": "photo", "url": "image.jpg"}]}})
        )

    def test_native_video_is_detected(self) -> None:
        self.assertTrue(
            _status_has_video({"media": {"videos": [{"type": "video", "url": "clip.mp4"}]}})
        )

    def test_animated_gif_is_detected(self) -> None:
        self.assertTrue(
            _status_has_video({"media": {"all": [{"type": "gif", "url": "animation.mp4"}]}})
        )

    def test_external_video_is_detected(self) -> None:
        self.assertTrue(
            _status_has_video({"media": {"external": {"type": "video", "url": "video.example"}}})
        )

    def test_video_in_quote_is_detected(self) -> None:
        self.assertTrue(
            _status_has_video(
                {
                    "media": {"photos": [{"type": "photo"}]},
                    "quote": {"media": {"videos": [{"type": "video"}]}},
                }
            )
        )


class FixupXListenerTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def make_event():
        message = SimpleNamespace(
            id=42,
            author=SimpleNamespace(bot=False),
            content="watch https://x.com/example/status/1234567890123456789",
            reply=AsyncMock(),
        )
        return SimpleNamespace(message=message), message

    async def test_does_not_reply_when_status_has_no_video(self) -> None:
        event, message = self.make_event()
        listener = create_fixupx_listener(Mock())[0]

        with (
            patch("fixupx_link_listener.aiohttp.ClientSession", FakeClientSession),
            patch("fixupx_link_listener._tweet_has_video", AsyncMock(return_value=False)),
        ):
            await listener(event)

        message.reply.assert_not_awaited()

    async def test_replies_when_status_has_video(self) -> None:
        event, message = self.make_event()
        listener = create_fixupx_listener(Mock())[0]

        with (
            patch("fixupx_link_listener.aiohttp.ClientSession", FakeClientSession),
            patch("fixupx_link_listener._tweet_has_video", AsyncMock(return_value=True)),
        ):
            await listener(event)

        message.reply.assert_awaited_once_with(
            "https://fixupx.com/example/status/1234567890123456789"
        )


if __name__ == "__main__":
    unittest.main()
