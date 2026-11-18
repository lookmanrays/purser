from datetime import datetime, timedelta
import unittest

from notify.service import Delivery, NotificationQueue


class NotificationQueueTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2001, 1, 1, 12, 0)
        self.queue = NotificationQueue(self.start)

    def test_single_attempt_delivers_at_the_current_time(self):
        self.assertEqual(self.queue.enqueue("inbox", ["a", "b"]), 2)
        self.assertEqual(
            self.queue.advance(self.start),
            (Delivery("a", "inbox", self.start), Delivery("b", "inbox", self.start)),
        )
        self.assertEqual(self.queue.advance(self.start + timedelta(days=1)), ())

    def test_cancel_only_selected_channel(self):
        self.queue.enqueue("push", ["a"])
        self.queue.enqueue("inbox", ["b"])
        self.queue.cancel("push")
        self.assertEqual(
            self.queue.advance(self.start), (Delivery("b", "inbox", self.start),)
        )

    def test_delivery_uses_current_queue_clock(self):
        later = self.start + timedelta(minutes=10)
        self.assertEqual(self.queue.advance(later), ())
        self.queue.enqueue("inbox", ["a"])
        self.assertEqual(self.queue.advance(later), (Delivery("a", "inbox", later),))

    def test_empty_batch(self):
        self.assertEqual(self.queue.enqueue("inbox", []), 0)
        self.assertEqual(self.queue.advance(self.start), ())

    def test_rejects_backwards_time(self):
        with self.assertRaises(ValueError):
            self.queue.advance(self.start - timedelta(seconds=1))

    def test_rejects_invalid_batch(self):
        for channel, attempts, base in [("", 1, 1), ("inbox", 0, 1), ("inbox", 1, 0)]:
            with self.subTest(channel=channel, attempts=attempts, base=base):
                with self.assertRaises(ValueError):
                    self.queue.enqueue(channel, ["a"], attempts=attempts, base=base)
