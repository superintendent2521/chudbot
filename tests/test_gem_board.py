from types import SimpleNamespace

from chudbot.listeners.gem_board import _is_gem_board_message


def test_gem_board_messages_are_not_reposted_to_themselves():
    message = SimpleNamespace(channel=SimpleNamespace(id=123))

    assert _is_gem_board_message(message, 123)
    assert not _is_gem_board_message(message, 456)
