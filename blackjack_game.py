"""Dependency-free blackjack rules used by the economy command."""

from __future__ import annotations

from typing import Any, Sequence


RANKS = ("2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A")
SUITS = ("♠", "♥", "♦", "♣")


def hand_value(hand: Sequence[str]) -> int:
    value = 0
    aces = 0
    for card in hand:
        rank = card[:-1]
        if rank == "A":
            value += 11
            aces += 1
        elif rank in {"J", "Q", "K"}:
            value += 10
        else:
            value += int(rank)
    while value > 21 and aces:
        value -= 10
        aces -= 1
    return value


def new_game(rng: Any) -> tuple[list[str], list[str], list[str]]:
    deck = [f"{rank}{suit}" for suit in SUITS for rank in RANKS]
    rng.shuffle(deck)
    player_hand = [deck.pop(), deck.pop()]
    dealer_hand = [deck.pop(), deck.pop()]
    return deck, player_hand, dealer_hand


def play_dealer(deck: list[str], dealer_hand: list[str]) -> None:
    while hand_value(dealer_hand) < 17:
        dealer_hand.append(deck.pop())


def profit(
    player_hand: Sequence[str],
    dealer_hand: Sequence[str],
    amount: int,
) -> tuple[int, str]:
    player_value = hand_value(player_hand)
    dealer_value = hand_value(dealer_hand)
    player_blackjack = len(player_hand) == 2 and player_value == 21
    dealer_blackjack = len(dealer_hand) == 2 and dealer_value == 21

    if player_blackjack and dealer_blackjack:
        return 0, "**Push—both have blackjack.**"
    if player_blackjack:
        return amount * 3 // 2, "**Blackjack!**"
    if dealer_blackjack:
        return -amount, "Dealer has blackjack."
    if player_value > 21:
        return -amount, "You busted."
    if dealer_value > 21:
        return amount, "**Dealer busted—you win!**"
    if player_value > dealer_value:
        return amount, "**You win!**"
    if player_value < dealer_value:
        return -amount, "Dealer wins."
    return 0, "**Push.**"
