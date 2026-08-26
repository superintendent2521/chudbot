from types import SimpleNamespace

from chudbot.listeners.coal_board import COAL_THRESHOLD, _coal_reaction_count, _is_coal_emoji


def test_any_custom_coal_emoji_counts_toward_the_three_reaction_threshold():
    reactions = [
        SimpleNamespace(emoji=SimpleNamespace(name="coal", id=1), count=1),
        SimpleNamespace(emoji=SimpleNamespace(name="COAL", id=2), count=2),
    ]

    assert _is_coal_emoji(reactions[0].emoji)
    assert _coal_reaction_count(reactions) == COAL_THRESHOLD == 3
