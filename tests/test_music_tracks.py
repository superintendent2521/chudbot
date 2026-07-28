import sys
import types
import unittest


if "lavalink" not in sys.modules:
    lavalink = types.ModuleType("lavalink")
    lavalink.LoadResult = object
    lavalink.LoadType = types.SimpleNamespace(ERROR="error", EMPTY="empty")
    lavalink.errors = types.SimpleNamespace(ClientError=RuntimeError)
    sys.modules["lavalink"] = lavalink

from music_errors import MusicError
from music_tracks import TrackLoader


class TrackLoaderParsingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.loader = TrackLoader(runtime=None, sessions={})

    def test_search_terms_use_youtube_search(self) -> None:
        identifiers, is_youtube = self.loader._load_identifiers("artist song")
        self.assertEqual(identifiers, ["ytsearch:artist song"])
        self.assertFalse(is_youtube)

    def test_short_youtube_link_prefers_canonical_url(self) -> None:
        identifiers, is_youtube = self.loader._load_identifiers(
            "https://youtu.be/dQw4w9WgXcQ?feature=shared"
        )
        self.assertEqual(
            identifiers,
            [
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                "https://youtu.be/dQw4w9WgXcQ?feature=shared",
                "ytsearch:dQw4w9WgXcQ",
            ],
        )
        self.assertTrue(is_youtube)

    def test_playlist_link_is_preserved(self) -> None:
        identifiers, is_youtube = self.loader._load_identifiers(
            "https://www.youtube.com/playlist?list=PL123"
        )
        self.assertEqual(identifiers, ["https://www.youtube.com/playlist?list=PL123"])
        self.assertTrue(is_youtube)

    def test_empty_query_is_rejected(self) -> None:
        with self.assertRaisesRegex(MusicError, "search term or link"):
            self.loader._load_identifiers("  ")


if __name__ == "__main__":
    unittest.main()
