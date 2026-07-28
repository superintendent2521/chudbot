import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock


if "interactions" not in sys.modules:
    interactions = types.ModuleType("interactions")
    interactions.Client = object
    sys.modules["interactions"] = interactions

if "lavalink" not in sys.modules:
    lavalink = types.ModuleType("lavalink")
    lavalink.LoadResult = object
    lavalink.LoadType = types.SimpleNamespace(ERROR="error", EMPTY="empty")
    lavalink.errors = types.SimpleNamespace(ClientError=RuntimeError)
    sys.modules["lavalink"] = lavalink
else:
    lavalink = sys.modules["lavalink"]

if "lavalink.filters" not in sys.modules:
    filters = types.ModuleType("lavalink.filters")

    class Filter:
        pass

    filters.Filter = Filter
    lavalink.filters = filters
    sys.modules["lavalink.filters"] = filters

from music_manager import MusicManager


class MusicManagerNormalizationTests(unittest.IsolatedAsyncioTestCase):
    def make_runtime(self) -> SimpleNamespace:
        return SimpleNamespace(
            audio_normalization=True,
            normalization_max_amplitude=0.75,
            _normalization_unavailable=False,
            logger=Mock(),
        )

    async def test_normalization_is_only_applied_once(self) -> None:
        runtime = self.make_runtime()
        manager = MusicManager(runtime)
        stored = {}
        player = SimpleNamespace(
            guild_id=123,
            filters={},
            fetch=lambda key, default=None: stored.get(key, default),
            store=lambda key, value: stored.__setitem__(key, value),
            set_filter=AsyncMock(),
        )

        await manager.ensure_audio_normalization(player)
        await manager.ensure_audio_normalization(player)

        player.set_filter.assert_awaited_once()
        self.assertTrue(stored["audio_normalization_applied"])

    async def test_unsupported_filter_disables_future_attempts(self) -> None:
        runtime = self.make_runtime()
        manager = MusicManager(runtime)
        player = SimpleNamespace(
            guild_id=123,
            filters={},
            fetch=lambda key, default=None: default,
            store=Mock(),
            set_filter=AsyncMock(side_effect=RuntimeError("unsupported")),
        )

        await manager.ensure_audio_normalization(player)
        await manager.ensure_audio_normalization(player)

        self.assertTrue(runtime._normalization_unavailable)
        player.set_filter.assert_awaited_once()
        player.store.assert_not_called()


if __name__ == "__main__":
    unittest.main()
