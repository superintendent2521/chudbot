from types import SimpleNamespace

from chudbot.listeners.gem_board import _is_ignored_gem_message


def test_ignored_gem_channel_includes_threads():
    assert _is_ignored_gem_message(SimpleNamespace(channel=SimpleNamespace(id=10)), {10})
    assert _is_ignored_gem_message(SimpleNamespace(channel=SimpleNamespace(id=11, parent_id=10)), {10})
    assert not _is_ignored_gem_message(SimpleNamespace(channel=SimpleNamespace(id=12, parent_id=20)), {10})
