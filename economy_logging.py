"""Best-effort, low-priority persistence for economy audit events."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class EconomyLogRecord:
    event_type: str
    guild_id: int
    user_id: int
    amount: int
    balance_after: Optional[int]
    occurred_at: int
    counterparty_id: Optional[int] = None
    counterparty_balance_after: Optional[int] = None
    details: Optional[dict[str, Any]] = None


class EconomyLogWriter:
    """Write audit records without blocking or borrowing economy connections.

    Producers only call ``enqueue``, which uses ``put_nowait``. A full queue or
    a failed audit write drops logging data; neither condition is allowed to
    delay or fail the economy operation that produced it.
    """

    def __init__(
        self,
        database_url: str,
        *,
        queue_size: int = 10_000,
        batch_size: int = 100,
        flush_interval: float = 10.0,
    ) -> None:
        from psycopg.rows import dict_row
        from psycopg_pool import AsyncConnectionPool

        self._pool = AsyncConnectionPool(
            conninfo=database_url,
            min_size=1,
            max_size=1,
            timeout=2,
            kwargs={
                "row_factory": dict_row,
                "prepare_threshold": 3,
                "application_name": "chudite-economy-log",
            },
            open=False,
            name="economy-log",
        )
        self._queue: asyncio.Queue[EconomyLogRecord] = asyncio.Queue(
            maxsize=max(1, int(queue_size))
        )
        self._batch_size = max(1, int(batch_size))
        self._flush_interval = max(0.1, float(flush_interval))
        self._task: Optional[asyncio.Task[None]] = None
        self._stopping = False
        self._buffered = 0
        self.dropped = 0
        self._logger = logging.getLogger("chuds.bot.economy-log")

    async def start(self) -> None:
        if self._task is not None:
            return
        await self._pool.open(wait=True, timeout=5)
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name="economy-log-writer")

    def enqueue(self, record: EconomyLogRecord) -> bool:
        """Queue a record immediately, returning false when it was dropped."""
        if self._task is None or self._task.done() or self._stopping:
            self.dropped += 1
            return False
        try:
            self._queue.put_nowait(record)
        except asyncio.QueueFull:
            self.dropped += 1
            if self.dropped == 1 or self.dropped % 1_000 == 0:
                self._logger.warning(
                    "Dropped %s economy log records because the queue is full",
                    self.dropped,
                )
            return False
        return True

    @property
    def queued(self) -> int:
        """Records waiting in the queue or current unflushed batch."""
        return self._queue.qsize() + self._buffered

    async def close(self) -> None:
        self._stopping = True
        task = self._task
        if task is not None:
            try:
                await asyncio.wait_for(task, timeout=2)
            except asyncio.TimeoutError:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            self._task = None
        await self._pool.close()

    async def _run(self) -> None:
        while not self._stopping or not self._queue.empty():
            try:
                first = await asyncio.wait_for(self._queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            batch = [first]
            self._buffered = 1
            deadline = asyncio.get_running_loop().time() + self._flush_interval
            while len(batch) < self._batch_size:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0 or self._stopping:
                    break
                try:
                    record = await asyncio.wait_for(
                        self._queue.get(), timeout=min(remaining, 0.25)
                    )
                except asyncio.TimeoutError:
                    continue
                batch.append(record)
                self._buffered = len(batch)
            try:
                await self._write_batch(batch)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.dropped += len(batch)
                self._logger.exception(
                    "Dropped %s economy log records after an audit database error",
                    len(batch),
                )
            finally:
                for _ in batch:
                    self._queue.task_done()
                self._buffered = 0

    async def _write_batch(self, batch: list[EconomyLogRecord]) -> None:
        placeholders = ", ".join(
            ["(%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)"] * len(batch)
        )
        parameters: list[Any] = []
        print(f"Writing batch of {len(batch)} economy log records")
        for record in batch:
            data = asdict(record)
            parameters.extend(
                (
                    data["event_type"],
                    data["guild_id"],
                    data["user_id"],
                    data["counterparty_id"],
                    data["amount"],
                    data["balance_after"],
                    data["counterparty_balance_after"],
                    data["occurred_at"],
                    json.dumps(data["details"] or {}, separators=(",", ":")),
                )
            )
        async with self._pool.connection(timeout=2) as connection:
            await connection.execute(
                f"""INSERT INTO economy_log
                    (event_type, guild_id, user_id, counterparty_id, amount,
                     balance_after, counterparty_balance_after, occurred_at, details)
                    VALUES {placeholders}""",
                tuple(parameters),
            )
