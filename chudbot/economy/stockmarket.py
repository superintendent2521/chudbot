"""Player-driven, in-memory stock market with 4 listed companies.

The market is deliberately **player controlled**: there is no background
wall-clock ticker. Every price move is driven by a player trade acting against
a finite float (shares outstanding). Buying shares (or covering a short) draws
demand out of the float and pushes the price up; selling shares (or opening a
short) returns shares to the float and pushes the price down. An optional
``tick`` adds a tiny random-walk so prices keep breathing between trades.

Supported actions:

* ``buy`` / ``sell``      take or liquidate a long position.
* ``short`` / ``cover``   borrow and later return shares for a bearish view.
* ``view``                general market / "economy" overview.
* ``stock``               a detailed view of a single ticker.
* ``statistics``          per-player trading statistics.

All engine state lives in memory keyed by a player id, but the companion DB
facade (``load_stock_market`` / ``save_stock_market`` / ``execute_stock_trade``)
durably persists prices, player cash, statistics, and positions through the
PostgreSQL economy store.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field, replace
from typing import Any, Mapping, Optional, Sequence


## MARKET PARAMETERS
# The server only has ~30 active players, so floats are small and prices are
# coin-scale so a single player's order visibly moves the price. Player funds
# come from the real coin wallet (``economy_accounts.balance``); this constant
# is only the fallback for standalone/in-memory use and matches the coin start.
STARTING_BALANCE = 1_000.0
MIN_PRICE = 0.05
PRICE_ELASTICITY = 60.0       # % move when one full float turns over in one trade
MAX_TRADE_PRICE_MOVE = 0.25   # largest accepted single-trade price move
SHORT_MARGIN_RATE = 0.5       # fraction of notional required in cash before shorting
INDEX_BASE = 1_000.0          # arithmetic market-index base

PlayerId = int


@dataclass
class Stock:
    """A single listed company on the player-controlled market."""

    symbol: str
    name: str
    sector: str
    shares_outstanding: int
    price: float
    base_price: float = 0.0
    prev_close: float = 0.0
    open_price: float = 0.0
    session_high: float = 0.0
    session_low: float = 0.0
    volume: int = 0
    traded_value: float = 0.0
    history: list[float] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.base_price:
            self.base_price = self.price
        if not self.prev_close:
            self.prev_close = self.price
        if not self.open_price:
            self.open_price = self.price
        if not self.session_high:
            self.session_high = self.price
        if not self.session_low:
            self.session_low = self.price
        if not self.history:
            self.history = [self.price]

    @property
    def change(self) -> float:
        return self.price - self.prev_close

    @property
    def percent_change(self) -> float:
        if not self.prev_close:
            return 0.0
        return (self.change / self.prev_close) * 100.0


@dataclass
class LongPosition:
    """A player's open long (owned) shares in one ticker."""

    shares: int = 0
    avg_cost: float = 0.0


@dataclass
class ShortPosition:
    """A player's open short (borrowed) shares in one ticker."""

    quantity: int = 0
    avg_entry: float = 0.0


@dataclass
class PlayerAccount:
    """A player's cash, positions, and running statistics in one ticker market.

    ``cash`` mirrors the player's real coin wallet for the current session and
    ``coin_delta`` is the whole-coin change to apply to ``economy_accounts``
    once a persisted trade is confirmed.
    """

    cash: float = 0.0
    coin_delta: int = 0
    longs: dict[str, LongPosition] = field(default_factory=dict)
    shorts: dict[str, ShortPosition] = field(default_factory=dict)
    realized_pnl: float = 0.0
    trades: int = 0
    buys: int = 0
    sells: int = 0
    short_opens: int = 0
    covers: int = 0
    volume_bought: int = 0
    volume_sold: int = 0


@dataclass(frozen=True)
class TradeResult:
    """Outcome of a single order, ready for command-layer formatting."""

    accepted: bool
    symbol: str
    action: str
    quantity: int
    price: float
    amount: float
    cash: float
    equity: float
    reason: str = ""
def _initial_stocks() -> list[Stock]:
    # Small floats and coin-scale prices so a ~30-player guild can trade
    # meaningful stakes and individual orders actually move each ticker.
    return [
        Stock("RKLB", "Rocket Lab", "Space",     6_000, 4.20),
        Stock("LMT", "Lockheed Martin", "Defense", 5_000, 8.75),
        Stock("SPCX", "SpaceX", "Space",     4_500, 7.00),
        Stock("GD", "General Dynamics", "Defense", 5_500, 5.50),
    ]


class StockMarket:
    """In-memory, player-controlled stock market over a fixed set of tickers."""

    def __init__(
        self,
        *,
        rng: Optional[random.Random] = None,
        starting_balance: float = STARTING_BALANCE,
    ) -> None:
        self._rng = rng or random.SystemRandom()
        self.starting_balance = float(starting_balance)
        self.stocks: dict[str, Stock] = {
            stock.symbol: stock for stock in _initial_stocks()
        }
        self._players: dict[PlayerId, PlayerAccount] = {}

    # ------------------------------------------------------------------ state
    def _account(self, player_id: PlayerId) -> PlayerAccount:
        account = self._players.get(player_id)
        if account is None:
            account = PlayerAccount(cash=self.starting_balance)
            self._players[player_id] = account
        return account

    def _resolve(self, symbol: str, player_id: PlayerId) -> tuple[Stock, PlayerAccount]:
        stock = self.stocks.get(symbol.upper())
        if stock is None:
            raise KeyError(f"unknown ticker {symbol!r}")
        return stock, self._account(player_id)

    def _held(self, symbol: str) -> int:
        return sum(
            account.longs[symbol].shares
            for account in self._players.values()
            if symbol in account.longs
        )

    def _shorted(self, symbol: str) -> int:
        return sum(
            account.shorts[symbol].quantity
            for account in self._players.values()
            if symbol in account.shorts
        )

    def float_available(self, symbol: str) -> int:
        """Shares left in the public float for the given ticker."""
        stock = self.stocks[symbol.upper()]
        return stock.shares_outstanding - self._held(
            stock.symbol
        ) - self._shorted(stock.symbol)

    # ------------------------------------------------------------ price engine
    def _move_price(self, stock: Stock, signed_shares: int) -> None:
        """Apply order-flow price impact for a signed number of shares.

        ``signed_shares > 0`` draws demand out of the float (buy/cover) and
        raises the price; ``< 0`` returns shares to the float (sell/short open)
        and lowers it.
        """
        if signed_shares == 0:
            return
        raw_move = (PRICE_ELASTICITY / 100.0) * (
            signed_shares / stock.shares_outstanding
        )
        move = max(min(raw_move, MAX_TRADE_PRICE_MOVE), -MAX_TRADE_PRICE_MOVE)
        old_price = stock.price
        stock.price = max(MIN_PRICE, old_price * (1.0 + move))
        if math.isclose(stock.price, old_price):
            stock.price = old_price
        stock.volume += abs(signed_shares)
        stock.traded_value += abs(signed_shares) * old_price
        stock.session_high = max(stock.session_high, stock.price)
        stock.session_low = min(stock.session_low, stock.price)
        stock.history.append(stock.price)

    def tick(self, sigma: float = 0.002, reversion: float = 0.01) -> None:
        """Nudge prices with a tiny random walk toward each base price.

        Optional so the market stays alive between trades; deterministic tests
        can simply never call it.
        """
        for stock in self.stocks.values():
            drift = self._rng.gauss(0.0, sigma)
            pull = reversion * (stock.base_price - stock.price) / stock.base_price
            stock.price = max(MIN_PRICE, stock.price * (1.0 + drift + pull))
            stock.session_high = max(stock.session_high, stock.price)
            stock.session_low = min(stock.session_low, stock.price)
            stock.history.append(stock.price)

    # ---------------------------------------------------------------- trading
    def buy(self, player_id: PlayerId, symbol: str, quantity: int) -> TradeResult:
        """Purchase an integer number of shares as a long position."""
        q = int(quantity)
        if q <= 0:
            return self._reject("buy", symbol, player_id, "quantity must be positive")
        stock, account = self._resolve(symbol, player_id)
        available = self.float_available(stock.symbol)
        if q > available:
            return self._reject(
                "buy", stock.symbol, player_id,
                f"only {available:,} shares are available in the float",
            )
        cost = q * stock.price
        if cost > account.cash:
            return self._reject(
                "buy", stock.symbol, player_id,
                f"insufficient funds ({cost:,.2f} needed)",
            )

        account.cash -= cost
        position = account.longs.setdefault(stock.symbol, LongPosition())
        new_total = position.shares + q
        position.avg_cost = (
            position.shares * position.avg_cost + cost
        ) / new_total
        position.shares = new_total
        account.trades += 1
        account.buys += 1
        account.volume_bought += q
        self._move_price(stock, q)
        return self._accept("buy", stock.symbol, player_id, q, stock.price, -cost)

    def sell(self, player_id: PlayerId, symbol: str, quantity: int) -> TradeResult:
        """Liquidate an integer number of owned (long) shares."""
        q = int(quantity)
        if q <= 0:
            return self._reject("sell", symbol, player_id, "quantity must be positive")
        stock, account = self._resolve(symbol, player_id)
        position = account.longs.get(stock.symbol)
        held = 0 if position is None else position.shares
        if q > held:
            return self._reject(
                "sell", stock.symbol, player_id,
                f"you only own {held:,} shares",
            )

        proceeds = q * stock.price
        cost_basis = q * position.avg_cost
        account.cash += proceeds
        account.realized_pnl += proceeds - cost_basis
        position.shares -= q
        if position.shares == 0:
            del account.longs[stock.symbol]
        account.trades += 1
        account.sells += 1
        account.volume_sold += q
        self._move_price(stock, -q)
        return self._accept("sell", stock.symbol, player_id, q, stock.price, proceeds)

    def short(self, player_id: PlayerId, symbol: str, quantity: int) -> TradeResult:
        """Borrow shares from the float, sell them, and keep the proceeds.

        Requires ``SHORT_MARGIN_RATE`` of the notional value available in cash
        as a light safeguard against a runaway margin call.
        """
        q = int(quantity)
        if q <= 0:
            return self._reject(
                "short", symbol, player_id, "quantity must be positive"
            )
        stock, account = self._resolve(symbol, player_id)
        available = self.float_available(stock.symbol)
        if q > available:
            return self._reject(
                "short", stock.symbol, player_id,
                f"only {available:,} shares can be borrowed",
            )
        notional = q * stock.price
        margin = SHORT_MARGIN_RATE * notional
        if account.cash < margin:
            return self._reject(
                "short", stock.symbol, player_id,
                f"insufficient margin ({margin:,.2f} required)",
            )

        account.cash += notional  # proceeds of the borrowed shares
        position = account.shorts.setdefault(stock.symbol, ShortPosition())
        new_qty = position.quantity + q
        position.avg_entry = (
            position.quantity * position.avg_entry + notional
        ) / new_qty
        position.quantity = new_qty
        account.trades += 1
        account.short_opens += 1
        self._move_price(stock, -q)
        return self._accept(
            "short", stock.symbol, player_id, q, stock.price, notional
        )

    def cover(self, player_id: PlayerId, symbol: str, quantity: int) -> TradeResult:
        """Buy back borrowed shares and close (part of) a short position."""
        q = int(quantity)
        if q <= 0:
            return self._reject(
                "cover", symbol, player_id, "quantity must be positive"
            )
        stock, account = self._resolve(symbol, player_id)
        position = account.shorts.get(stock.symbol)
        open_qty = 0 if position is None else position.quantity
        if q > open_qty:
            return self._reject(
                "cover", stock.symbol, player_id,
                f"you only have {open_qty:,} shares short",
            )

        cost = q * stock.price
        if account.cash < cost:
            return self._reject(
                "cover", stock.symbol, player_id,
                f"insufficient funds to buy back ({cost:,.2f} needed)",
            )

        account.cash -= cost
        account.realized_pnl += (position.avg_entry - stock.price) * q
        position.quantity -= q
        if position.quantity == 0:
            del account.shorts[stock.symbol]
        account.trades += 1
        account.covers += 1
        self._move_price(stock, q)
        return self._accept("cover", stock.symbol, player_id, q, stock.price, -cost)

    # ---------------------------------------------------------------- helpers
    def _reject(
        self, action: str, symbol: str, player_id: PlayerId, reason: str
    ) -> TradeResult:
        stock, account = self._resolve(symbol, player_id)
        return TradeResult(
            accepted=False,
            symbol=stock.symbol,
            action=action,
            quantity=0,
            price=stock.price,
            amount=0.0,
            cash=account.cash,
            equity=self.equity(player_id),
            reason=reason,
        )

    def _accept(
        self,
        action: str,
        symbol: str,
        player_id: PlayerId,
        quantity: int,
        price: float,
        amount: float,
    ) -> TradeResult:
        account = self._players[player_id]
        return TradeResult(
            accepted=True,
            symbol=symbol,
            action=action,
            quantity=quantity,
            price=price,
            amount=amount,
            cash=account.cash,
            equity=self.equity(player_id),
        )

    # ------------------------------------------------------------- valuation
    def unrealized_pnl(self, player_id: PlayerId) -> float:
        """Mark-to-market gain or loss across all open positions."""
        account = self._account(player_id)
        total = 0.0
        for symbol, position in account.longs.items():
            total += (self.stocks[symbol].price - position.avg_cost) * position.shares
        for symbol, position in account.shorts.items():
            total += (
                position.avg_entry - self.stocks[symbol].price
            ) * position.quantity
        return total

    def equity(self, player_id: PlayerId) -> float:
        """Net liquidation value: cash + long value - short liability."""
        account = self._account(player_id)
        long_value = 0.0
        for symbol, position in account.longs.items():
            long_value += self.stocks[symbol].price * position.shares
        short_value = sum(
            self.stocks[symbol].price * position.quantity
            for symbol, position in account.shorts.items()
        )
        return account.cash + long_value - short_value

    def index(self) -> float:
        """Equal-weighted market index, rebased to ``INDEX_BASE``."""
        if not self.stocks:
            return INDEX_BASE
        average_pct = (
            sum(s.percent_change for s in self.stocks.values()) / len(self.stocks)
        )
        return INDEX_BASE * (1.0 + average_pct / 100.0)

    # ---------------------------------------------------------------- views
    def view(self) -> str:
        """General market overview ("the economy") across all tickers."""
        index = self.index()
        rows = "\n".join(
            f"• {s.symbol}  {s.name:<18} {self._money(s.price):>12}  "
            f"{self._signed_pct(s.percent_change)}  vol {s.volume:,}"
            for s in self._sorted_stocks()
        )
        total_value = sum(s.traded_value for s in self.stocks.values())
        return (
            f"📈 **Market Overview** — mood: {self._sentiment()}\n"
            f"Index: {self._money(index)} ({self._signed(index - INDEX_BASE)})\n\n"
            f"```py\n{rows}\n```\n"
            f"Total traded value: {self._money(total_value)}"
        )

    def stock(self, symbol: str, player_id: Optional[PlayerId] = None) -> str:
        """Detailed view of a single ticker, optionally with the player's stake."""
        stock = self.stocks[symbol.upper()]
        lines = [
            f"📊 **{stock.symbol}** — {stock.name} ({stock.sector})",
            f"Price: {self._money(stock.price)}  "
            f"({self._signed_pct(stock.percent_change)})",
            f"Open {self._money(stock.open_price)}    "
            f"High {self._money(stock.session_high)}    "
            f"Low {self._money(stock.session_low)}",
            f"Volume: {stock.volume:,}    "
            f"Traded: {self._money(stock.traded_value)}",
            f"Float available: {self.float_available(stock.symbol):,} / "
            f"{stock.shares_outstanding:,}",
        ]
        if player_id is not None:
            lines.append(self._position_line(player_id, stock.symbol))
        return "\n".join(lines)

    def statistics(self, player_id: PlayerId) -> str:
        """Per-player trading statistics and current position summary."""
        account = self._account(player_id)
        net_worth = self.equity(player_id)
        gain = net_worth - self.starting_balance
        gain_pct = (gain / self.starting_balance) * 100.0 if self.starting_balance else 0.0

        position_lines = []
        for symbol, position in account.longs.items():
            price = self.stocks[symbol].price
            position_lines.append(
                f"  🟢 {symbol}: {position.shares:,} @ {self._money(position.avg_cost)} "
                f"(now {self._money(price)})"
            )
        for symbol, position in account.shorts.items():
            price = self.stocks[symbol].price
            position_lines.append(
                f"  🔴 {symbol}: short {position.quantity:,} @ "
                f"{self._money(position.avg_entry)} (now {self._money(price)})"
            )
        positions = "\n".join(position_lines) or "  (no open positions)"

        return (
            f"👤 **Stats** — player {player_id}\n"
            f"Net worth: {self._money(net_worth)} "
            f"({self._signed_pct(gain_pct)} vs start)\n"
            f"Cash: {self._money(account.cash)}    "
            f"Unrealized: {self._signed_pnl(self.unrealized_pnl(player_id))}    "
            f"Realized: {self._signed_pnl(account.realized_pnl)}\n"
            f"Trades: {account.trades:,}  "
            f"(buy {account.buys:,} · sell {account.sells:,} · "
            f"short {account.short_opens:,} · cover {account.covers:,})\n"
            f"Buy volume {account.volume_bought:,} · Sell volume "
            f"{account.volume_sold:,}\n"
            f"**Positions**\n{positions}"
        )

    def _position_line(self, player_id: PlayerId, symbol: str) -> str:
        account = self._account(player_id)
        long_pos = account.longs.get(symbol)
        short_pos = account.shorts.get(symbol)
        pieces = []
        if long_pos is not None:
            price = self.stocks[symbol].price
            pieces.append(
                f"Long {long_pos.shares:,} @ {self._money(long_pos.avg_cost)} "
                f"(now {self._money(price)})"
            )
        if short_pos is not None:
            price = self.stocks[symbol].price
            pieces.append(
                f"Short {short_pos.quantity:,} @ {self._money(short_pos.avg_entry)} "
                f"(now {self._money(price)})"
            )
        if not pieces:
            return "Position: none"
        return "Position: " + " · ".join(pieces)

    # -------------------------------------------------------------- persistence
    def serialize_market(self) -> list[dict]:
        """Rows for ``economy_stock_market`` (one per ticker)."""
        columns = (
            "symbol", "name", "sector", "shares_outstanding", "base_price",
            "price", "prev_close", "open_price", "session_high",
            "session_low", "volume", "traded_value",
        )
        return [
            {column: getattr(stock, column) for column in columns}
            for stock in self.stocks.values()
        ]

    def serialize_accounts(self) -> list[dict]:
        """Rows for ``economy_stock_accounts`` (one per known player).

        Ledger money is the player's real coin wallet, so no ``cash`` is stored
        here; only statistics and the pending whole-coin delta appear.
        """
        rows: list[dict] = []
        for user_id, account in self._players.items():
            rows.append({
                "user_id": user_id,
                # Kept for standalone/legacy snapshots. The live store reads
                # the authoritative value from economy_accounts.balance.
                "cash": account.cash,
                "coin_delta": account.coin_delta,
                "realized_pnl": account.realized_pnl,
                "trades": account.trades,
                "buys": account.buys,
                "sells": account.sells,
                "short_opens": account.short_opens,
                "covers": account.covers,
                "volume_bought": account.volume_bought,
                "volume_sold": account.volume_sold,
            })
        return rows

    def serialize_positions(self) -> list[dict]:
        """Rows for ``economy_stock_positions`` (one per ticker a player holds).

        A single row carries both the long and short stance on a ticker because
        the table's primary key is ``(guild_id, user_id, symbol)``.
        """
        rows: list[dict] = []
        for user_id, account in self._players.items():
            for symbol in set(account.longs) | set(account.shorts):
                long_pos = account.longs.get(symbol)
                short_pos = account.shorts.get(symbol)
                rows.append({
                    "user_id": user_id,
                    "symbol": symbol,
                    "long_qty": long_pos.shares if long_pos else 0,
                    "long_avg_cost": long_pos.avg_cost if long_pos else 0.0,
                    "short_qty": short_pos.quantity if short_pos else 0,
                    "short_avg_entry": short_pos.avg_entry if short_pos else 0.0,
                })
        return rows

    def hydrate(
        self,
        market_rows: Sequence[Mapping[str, Any]],
        account_rows: Sequence[Mapping[str, Any]],
        position_rows: Sequence[Mapping[str, Any]],
        coin_rows: Optional[Sequence[Mapping[str, Any]]] = None,
    ) -> None:
        """(Re)load in-memory state from rows returned by the database.

        ``market_rows`` fully replace the ticker table; ``account_rows`` and
        ``position_rows`` rebuild each player's statistics and stakes. Player
        ``cash`` mirrors the real coin wallet and is taken from ``coin_rows``
        (each ``{'user_id': ..., 'balance': ...}``); players without a balance
        start at zero and ``execute_stock_trade`` refreshes them before trading.
        """
        defaults = {stock.symbol: stock for stock in _initial_stocks()}
        for row in market_rows:
            symbol = str(row["symbol"])
            fallback = defaults.get(symbol)
            self.stocks[symbol] = Stock(
                symbol=symbol,
                name=str(row.get("name") or (fallback.name if fallback else symbol)),
                sector=str(row.get("sector") or (fallback.sector if fallback else "")),
                shares_outstanding=int(row["shares_outstanding"]),
                price=float(row["price"]),
                base_price=float(row["base_price"]),
                prev_close=float(row["prev_close"]),
                open_price=float(row["open_price"]),
                session_high=float(row["session_high"]),
                session_low=float(row["session_low"]),
                volume=int(row["volume"]),
                traded_value=float(row["traded_value"]),
                history=[float(row["price"])],
            )
        self._players = {}
        balance_by_user = {
            int(row["user_id"]): float(row["balance"])
            for row in (coin_rows or ())
        }
        for row in account_rows:
            user_id = int(row["user_id"])
            legacy_cash = row.get("cash")
            self._players[user_id] = PlayerAccount(
                cash=balance_by_user.get(
                    user_id, float(legacy_cash) if legacy_cash is not None else 0.0
                ),
                coin_delta=int(row.get("coin_delta") or 0),
                realized_pnl=float(row.get("realized_pnl", 0.0)),
                trades=int(row.get("trades", 0)),
                buys=int(row.get("buys", 0)),
                sells=int(row.get("sells", 0)),
                short_opens=int(row.get("short_opens", 0)),
                covers=int(row.get("covers", 0)),
                volume_bought=int(row.get("volume_bought", 0)),
                volume_sold=int(row.get("volume_sold", 0)),
            )
        for row in position_rows:
            user_id = int(row["user_id"])
            symbol = str(row["symbol"])
            account = self._account(user_id)
            long_qty = int(row.get("long_qty") or 0)
            if long_qty > 0:
                account.longs[symbol] = LongPosition(
                    long_qty, float(row.get("long_avg_cost") or 0.0)
                )
            short_qty = int(row.get("short_qty") or 0)
            if short_qty > 0:
                account.shorts[symbol] = ShortPosition(
                    short_qty, float(row.get("short_avg_entry") or 0.0)
                )

    # ------------------------------------------------------------ formatters
    def _sorted_stocks(self) -> list[Stock]:
        return sorted(self.stocks.values(), key=lambda s: s.symbol)

    def _sentiment(self) -> str:
        average = sum(s.percent_change for s in self.stocks.values()) / len(
            self.stocks
        )
        if average >= 1.0:
            return "strongly bullish"
        if average > 0.0:
            return "mildly bullish"
        if average <= -1.0:
            return "strongly bearish"
        if average < 0.0:
            return "mildly bearish"
        return "flat"

    @staticmethod
    def _money(value: float) -> str:
        return f"${value:,.2f}"

    @staticmethod
    def _signed_pct(value: float) -> str:
        return f"{value:+.2f}%"

    @staticmethod
    def _signed(value: float) -> str:
        return f"{value:+,.2f}"

    @staticmethod
    def _signed_pnl(value: float) -> str:
        return f"{value:+,.2f}"


# ------------------------------------------------------------------ DB facade
def run_stock_trade(
    market: StockMarket,
    player_id: PlayerId,
    action: str,
    symbol: str,
    quantity: int,
) -> TradeResult:
    """Dispatch a trade request to the matching engine method."""
    handler = {
        "buy": market.buy,
        "sell": market.sell,
        "short": market.short,
        "cover": market.cover,
    }.get(action)
    if handler is None:
        raise ValueError(f"unknown trade action {action!r}")
    return handler(player_id, symbol, quantity)


async def load_stock_market(
    store: Any,
    guild_id: int,
    *,
    starting_balance: float = STARTING_BALANCE,
) -> StockMarket:
    """Hydrate a fresh :class:`StockMarket` from the persisted DB snapshot.

    ``store.load_stock_market(guild_id)`` returns
    ``(market_rows, account_rows, position_rows, coin_rows)``; a fresh guild
    with no rows yet simply starts from the tuned defaults. Each player's
    ``cash`` is set from their real coin balance in ``coin_rows``.
    """
    market = StockMarket(starting_balance=starting_balance)
    loaded = await store.load_stock_market(guild_id)
    if len(loaded) == 3:
        market_rows, account_rows, position_rows = loaded
        coin_rows = ()
    else:
        market_rows, account_rows, position_rows, coin_rows = loaded
    market.hydrate(market_rows, account_rows, position_rows, coin_rows)
    return market


async def save_stock_market(
    store: Any,
    market: StockMarket,
    guild_id: int,
    *,
    now: Optional[int] = None,
) -> None:
    """Persist the full market snapshot through ``store``."""
    await store.save_stock_market(
        guild_id,
        market.serialize_market(),
        market.serialize_accounts(),
        market.serialize_positions(),
        now=now,
    )


async def execute_stock_trade(
    store: Any,
    market: StockMarket,
    guild_id: int,
    player_id: PlayerId,
    action: str,
    symbol: str,
    quantity: int,
    *,
    now: Optional[int] = None,
) -> tuple[TradeResult, StockMarket]:
    """Run one order against a hydrated market, settling through the coin wallet.

    The player's real ``economy_accounts.balance`` is read first and mirrored
    into the engine's ``cash``; if the order is accepted, the resulting whole-coin
    delta is recorded on the account and persisted, and a ``TradeResult`` with the
    coin-precise balance is returned. Rejected orders leave the engine untouched
    and are never persisted.
    """
    account = market._account(player_id)
    balance_reader = getattr(store, "balance", None)
    coins = int(await balance_reader(guild_id, player_id)) if balance_reader else int(
        market._account(player_id).cash
    )
    account.cash = float(coins)
    start_coins = coins

    result = run_stock_trade(market, player_id, action, symbol, quantity)
    if result.accepted:
        account = market._account(player_id)
        delta = int(round(account.cash - start_coins))
        account.coin_delta = delta
        account.cash = float(start_coins + delta)  # keep the coin ledger integer
        await save_stock_market(store, market, guild_id, now=now)
        # The store has applied this one-shot wallet adjustment. Do not carry
        # it into a later snapshot and apply it twice.
        account.coin_delta = 0
        result = replace(
            result,
            cash=account.cash,
            equity=market.equity(player_id),
        )
        log_stock_trade = getattr(store, "log_stock_trade", None)
        if log_stock_trade is not None:
            log_stock_trade(
                guild_id,
                player_id,
                action=result.action,
                symbol=result.symbol,
                quantity=result.quantity,
                price=result.price,
                amount=delta,
                balance_after=int(result.cash),
                occurred_at=now,
            )
    return result, market
