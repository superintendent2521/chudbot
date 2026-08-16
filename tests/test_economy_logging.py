import asyncio
import logging
import unittest

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
        writer.dropped = 0
        writer._logger = logging.getLogger("test.economy-log")
        record = EconomyLogRecord("work", 1, 2, 10, 260, 123)

        self.assertTrue(writer.enqueue(record))
        self.assertFalse(writer.enqueue(record))
        self.assertEqual(writer.dropped, 1)
        self.assertEqual(writer._queue.qsize(), 1)

    def test_unavailable_writer_drops_log_instead_of_raising(self) -> None:
        writer = EconomyLogWriter.__new__(EconomyLogWriter)
        writer._queue = asyncio.Queue(maxsize=1)
        writer._task = None
        writer._stopping = False
        writer.dropped = 0
        writer._logger = logging.getLogger("test.economy-log")

        queued = writer.enqueue(EconomyLogRecord("gift", 1, 2, -5, 245, 123))

        self.assertFalse(queued)
        self.assertEqual(writer.dropped, 1)


if __name__ == "__main__":
    unittest.main()
