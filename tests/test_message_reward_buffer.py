import unittest

from chudbot.economy.reward_buffer import MessageRewardBuffer


class MessageRewardBufferTests(unittest.TestCase):
    def test_deposits_only_after_ten_messages_and_rounds_up(self) -> None:
        rewards = MessageRewardBuffer(10)
        for _ in range(9):
            self.assertIsNone(rewards.add(1, 2, 250))
        self.assertEqual(rewards.add(1, 2, 250), (3, 2_500))

    def test_users_and_guilds_have_separate_batches(self) -> None:
        rewards = MessageRewardBuffer(2)
        self.assertIsNone(rewards.add(1, 10, 100))
        self.assertIsNone(rewards.add(1, 20, 100))
        self.assertIsNone(rewards.add(2, 10, 100))
        self.assertEqual(rewards.add(1, 10, 100), (1, 200))


if __name__ == "__main__":
    unittest.main()
