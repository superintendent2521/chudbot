"""In-memory batching for tiny per-message economy rewards."""

from __future__ import annotations


class MessageRewardBuffer:
    def __init__(self, batch_size: int = 10) -> None:
        self.batch_size = max(1, int(batch_size))
        self._pending: dict[tuple[int, int], tuple[int, int]] = {}

    def add(self, guild_id: int, user_id: int, milli_coins: int) -> tuple[int, int] | None:
        key = (guild_id, user_id)
        count, total = self._pending.get(key, (0, 0))
        count += 1
        total += max(0, int(milli_coins))
        if count < self.batch_size:
            self._pending[key] = (count, total)
            return None
        self._pending.pop(key, None)
        return (total + 999) // 1_000, total

    def restore(self, guild_id: int, user_id: int, count: int, milli_coins: int) -> None:
        key = (guild_id, user_id)
        current_count, current_total = self._pending.get(key, (0, 0))
        self._pending[key] = (current_count + count, current_total + milli_coins)
