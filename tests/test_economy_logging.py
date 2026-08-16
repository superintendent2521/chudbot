import asyncio
import logging
import unittest
from unittest.mock import AsyncMock

from economy_logging import EconomyLogRecord, EconomyLogWriter


class _ActiveTask:
    @staticmethod
    def done() -> bool:
        return False


class EconomyLogWriterTests(unittest.TestCase):
    def test_full_queue_drops_log_instead_of_waiting(self) -> None:
        writer = EconomyLogWriter.__new__(EconomyLogWriter)
        writer._queue = asyncio.Queue(maxsize=1)
        writer._task = _ActiveTask()
        writer._stopping = False
        writer._buffered = 0
        writer.dropped = 0
        writer._logger = logging.getLogger("test.economy-log")
        record = EconomyLogRecord("work", 1, 2, 10, 260, 123)

        self.assertTrue(writer.enqueue(record))
        self.assertFalse(writer.enqueue(record))
        self.assertEqual(writer.dropped, 1)
        self.assertEqual(writer._queue.qsize(), 1)
        self.assertEqual(writer.queued, 1)

    def test_unavailable_writer_drops_log_instead_of_raising(self) -> None:
        writer = EconomyLogWriter.__new__(EconomyLogWriter)
        writer._queue = asyncio.Queue(maxsize=1)
        writer._task = None
        writer._stopping = False
        writer._buffered = 0
        writer.dropped = 0
        writer._logger = logging.getLogger("test.economy-log")

        queued = writer.enqueue(EconomyLogRecord("gift", 1, 2, -5, 245, 123))

        self.assertFalse(queued)
        self.assertEqual(writer.dropped, 1)


class EconomyLogBatchingTests(unittest.IsolatedAsyncioTestCase):
    async def test_sparse_logs_wait_for_flush_interval(self) -> None:
        writer = EconomyLogWriter.__new__(EconomyLogWriter)
        writer._queue = asyncio.Queue(maxsize=10)
        writer._batch_size = 100
        writer._flush_interval = 0.05
        writer._stopping = False
        writer._buffered = 0
        writer.dropped = 0
        writer._logger = logging.getLogger("test.economy-log")
        writer._write_batch = AsyncMock()
        writer._task = asyncio.create_task(writer._run())

        self.assertTrue(writer.enqueue(EconomyLogRecord("work", 1, 2, 10, 260, 123)))
        await asyncio.sleep(0.01)
        writer._write_batch.assert_not_awaited()
        self.assertEqual(writer.queued, 1)

        await asyncio.sleep(0.06)
        writer._write_batch.assert_awaited_once()
        writer._stopping = True
        await asyncio.wait_for(writer._task, timeout=1)

    async def test_full_batch_flushes_without_waiting_for_interval(self) -> None:
        writer = EconomyLogWriter.__new__(EconomyLogWriter)
        writer._queue = asyncio.Queue(maxsize=10)
        writer._batch_size = 2
        writer._flush_interval = 10
        writer._stopping = False
        writer._buffered = 0
        writer.dropped = 0
        writer._logger = logging.getLogger("test.economy-log")
        writer._write_batch = AsyncMock()
        writer._task = asyncio.create_task(writer._run())
        record = EconomyLogRecord("work", 1, 2, 10, 260, 123)

        writer.enqueue(record)
        writer.enqueue(record)
        await asyncio.sleep(0.02)

        writer._write_batch.assert_awaited_once()
        writer._stopping = True
        await asyncio.wait_for(writer._task, timeout=1)


if __name__ == "__main__":
    unittest.main()
