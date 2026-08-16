import unittest
from random import Random

from chudbot.games.blackjack import hand_value, new_game, play_dealer, profit


class BlackjackTests(unittest.TestCase):
    def test_new_game_uses_one_standard_deck(self) -> None:
        deck, player, dealer = new_game(Random(1))
        all_cards = deck + player + dealer
        self.assertEqual(len(all_cards), 52)
        self.assertEqual(len(set(all_cards)), 52)

    def test_aces_change_from_eleven_to_one_to_prevent_bust(self) -> None:
        self.assertEqual(hand_value(["A♠", "9♥"]), 20)
        self.assertEqual(hand_value(["A♠", "9♥", "5♦"]), 15)
        self.assertEqual(hand_value(["A♠", "A♥", "9♦"]), 21)

    def test_natural_blackjack_pays_three_to_two(self) -> None:
        winnings, _ = profit(["A♠", "K♥"], ["10♦", "9♣"], 100)
        self.assertEqual(winnings, 150)

    def test_dealer_bust_pays_even_money(self) -> None:
        winnings, _ = profit(["10♠", "8♥"], ["10♦", "6♣", "K♠"], 100)
        self.assertEqual(winnings, 100)

    def test_equal_hands_push(self) -> None:
        winnings, _ = profit(["10♠", "8♥"], ["J♦", "8♣"], 100)
        self.assertEqual(winnings, 0)

    def test_dealer_natural_beats_non_natural_twenty_one(self) -> None:
        winnings, _ = profit(
            ["7♠", "7♥", "7♦"],
            ["A♣", "K♠"],
            100,
        )
        self.assertEqual(winnings, -100)

    def test_dealer_draws_until_seventeen(self) -> None:
        deck = ["10♠", "2♣"]
        dealer = ["10♦", "5♥"]
        play_dealer(deck, dealer)
        self.assertEqual(hand_value(dealer), 17)


if __name__ == "__main__":
    unittest.main()
