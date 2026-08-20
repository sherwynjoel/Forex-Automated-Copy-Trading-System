"""Account state tracking: balance, equity, open P&L.

Tracks each account's balance, equity, and open P&L driven by cTrader
trader updates and spot (price) events.
"""

import logging
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ctrader_open_api import Protobuf
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOASpotEvent, ProtoOASubscribeSpotsReq, ProtoOATraderReq,
)
from twisted.internet import defer

from copier.ctrader.client import CTraderClient
from copier.domain.models import Side, SymbolInfo

log = logging.getLogger(__name__)

# cTrader Open API encodes ProtoOASpotEvent bid/ask as fixed 1/100_000 units
# for EVERY symbol -- see on_spot(). Not derived from the symbol's `digits`,
# which is display precision only.
SPOT_PRICE_SCALE = 100_000.0


def unrealized_pnl_quote(
    side: Side, entry_price: float, volume: int, bid: float, ask: float
) -> float:
    """Calculate unrealized P&L in quote currency.

    Pure function: closing price is bid for BUY, ask for SELL.
    Units are volume / 100. Returns P&L in quote currency.

    Args:
        side: BUY or SELL
        entry_price: Entry price in quote currency
        volume: Volume in protocol units
        bid: Current bid in quote currency
        ask: Current ask in quote currency

    Returns:
        Unrealized P&L in quote currency
    """
    units = volume / 100
    if side == Side.BUY:
        # For BUY, we use bid to close; profit if bid > entry
        return (bid - entry_price) * units
    else:
        # For SELL, we use ask to close; profit if entry > ask
        return (entry_price - ask) * units


@dataclass(frozen=True)
class PositionSnapshot:
    """Snapshot of an open position at a point in time.

    This matches Task 17's (reconcile.py) interface for compatibility.
    """
    position_id: int
    symbol_id: int
    side: Side
    volume: int
    price: float  # entry price
    label: str


class AccountStateTracker:
    """Tracks account balance, equity, and open P&L from spot and trader events."""

    def __init__(
        self,
        master_client: CTraderClient,
        repo: Any,  # Repo type (satisfies MappingState protocol)
        master_account_id: int,
        symbols_by_id: Mapping[int, SymbolInfo],
    ):
        """Initialize tracker.

        Args:
            master_client: CTraderClient for sending requests and receiving updates
            repo: Repository for mappings and state
            master_account_id: Master account ID
            symbols_by_id: Map of symbol_id to SymbolInfo
        """
        self._client = master_client
        self._repo = repo
        self._master_account_id = master_account_id
        self._symbols_by_id = symbols_by_id

        # State storage
        self._balances: dict[int, float | None] = {}  # account_id -> balance or None if unknown
        self._balance_known: dict[int, bool] = {}  # account_id -> whether balance has been refreshed
        self._positions: dict[int, list[PositionSnapshot]] = {}  # account_id -> positions
        self._spots: dict[int, tuple[float, float]] = {}  # symbol_id -> (bid, ask)
        # Symbol ids already subscribed on the CURRENT connection. Without
        # this memory, every resync tick re-sent the full subscription and
        # the broker rejected the repeats with ALREADY_SUBSCRIBED (one per
        # org per minute on prod). Reset when the connection drops:
        # broker-side subscriptions die with the socket.
        self._subscribed_spots: set[int] = set()

        # Wire up callbacks
        self._client.on_spot(self.on_spot)
        self._client.on_disconnected(self._subscribed_spots.clear)

    def refresh_balances(
        self,
        account_ids: list[int],
        clients_by_account: Mapping[int, CTraderClient] | None = None,
    ) -> defer.Deferred:
        """Refresh balances for given accounts via ProtoOATraderReq.

        Sends a ProtoOATraderReq for each account and stores balance scaled by
        moneyDigits (or 10**2 if absent). Tracks whether balance data was received.

        Args:
            account_ids: List of account IDs to refresh
            clients_by_account: Per-account client routing. An org can mix
                demo and live accounts (prod org 3 does), and a request for
                a live account written down the master's demo connection is
                rejected INVALID_REQUEST by the broker -- so each request
                must ride the client for ITS account's environment. Falls
                back to the master client for accounts not in the map.

        Returns:
            Deferred that fires when all trader requests complete
        """
        clients_by_account = clients_by_account or {}
        deferreds = []
        for account_id in account_ids:
            req = ProtoOATraderReq()
            req.ctidTraderAccountId = account_id
            d = clients_by_account.get(account_id, self._client).send(req)
            # CTraderClient.send() resolves with the raw ProtoMessage envelope
            # (payloadType/payload bytes); it must be unwrapped into a typed
            # ProtoOATraderRes before .trader is accessible.
            d.addCallback(Protobuf.extract)
            d.addCallback(self._process_trader_response, account_id)
            d.addErrback(self._handle_trader_error, account_id)
            deferreds.append(d)
        return defer.DeferredList(deferreds)

    def _process_trader_response(self, response: Any, account_id: int) -> None:
        """Process trader response and extract balance.

        Args:
            response: ProtoOATraderRes or response object
            account_id: Account ID being updated
        """
        if not hasattr(response, 'trader') or response.trader is None:
            log.warning("trader response missing trader field for account %s", account_id)
            self._balance_known[account_id] = False
            self._balances[account_id] = None
            return

        trader = response.trader
        # Extract moneyDigits (field 20 in ProtoOATrader)
        money_digits = getattr(trader, 'moneyDigits', 2)
        if money_digits == 0:
            money_digits = 2

        # Scale balance by 10**money_digits
        balance_scaled = trader.balance / (10 ** money_digits)
        self._balances[account_id] = balance_scaled
        self._balance_known[account_id] = True

    def on_trader_updated(self, evt: Any) -> None:
        """Apply a pushed ProtoOATraderUpdatedEvent immediately.

        The broker pushes this on every balance change (realized close,
        deposit, withdrawal); consuming it makes Overview's balance current
        the instant it happens instead of on the next 60s poll. The event
        carries a full ProtoOATrader, the same shape ProtoOATraderRes wraps,
        so the poll path's processor applies unchanged.
        """
        self._process_trader_response(evt, evt.ctidTraderAccountId)

    def _handle_trader_error(self, failure: Any, account_id: int) -> None:
        """Handle error during trader request.

        Args:
            failure: Twisted Failure object
            account_id: Account ID being updated
        """
        log.warning("trader request failed for account %s: %s", account_id, failure)
        self._balance_known[account_id] = False
        self._balances[account_id] = None

    def set_positions(
        self, account_id: int, positions: Sequence[PositionSnapshot]
    ) -> None:
        """Set open positions for an account (from reconcile).

        Args:
            account_id: Account ID
            positions: List of open position snapshots (symbol_id keyed)
        """
        self._positions[account_id] = list(positions)

    def ensure_spot_subscriptions(self) -> defer.Deferred:
        """Subscribe to spots for all symbols with open positions.

        Sends ProtoOASubscribeSpotsReq for every symbol with an open position
        across all accounts, on the master connection. Quotes are shared across
        accounts at the same broker.

        Returns:
            Deferred that fires when subscription completes
        """
        # Collect all unique symbol IDs with open positions (direct from position.symbol_id)
        symbol_ids = set()
        for positions in self._positions.values():
            for pos in positions:
                symbol_ids.add(pos.symbol_id)

        # Only the delta: this connection's existing subscriptions stay
        # live, and re-subscribing them is an ALREADY_SUBSCRIBED rejection.
        needed = symbol_ids - self._subscribed_spots
        if not needed:
            return defer.succeed(None)

        # Subscribe on master account
        req = ProtoOASubscribeSpotsReq()
        req.ctidTraderAccountId = self._master_account_id
        for sym_id in sorted(needed):
            req.symbolId.append(sym_id)

        d = self._client.send(req)

        def _mark_subscribed(response):
            # Only a delivered request marks the symbols; a failed send
            # leaves them unmarked so the next tick retries.
            self._subscribed_spots |= needed
            return response

        d.addCallback(_mark_subscribed)
        return d

    def on_spot(self, evt: ProtoOASpotEvent) -> None:
        """Handle spot event from the client.

        `ProtoOASpotEvent.bid`/`.ask` are FIXED 1/100_000 units for every
        symbol, regardless of that symbol's `digits` (N5). `digits` is
        display precision only -- it says how many decimals a platform shows
        a price to, not how the wire encodes it. The official OpenApiPy
        samples divide spot prices by 100000 unconditionally for the same
        reason.

        This used to divide by `10 ** digits`, which is right only for the
        5-digit majors and wrong by a factor of 100 for every JPY pair
        (digits=3): USDJPY at 110.50 arrives as 11_050_000 and was tracked
        as 11_050.0, so Overview's open P&L and equity for any account
        holding a JPY pair were off by 100x -- the exact numbers the rollout
        stages tell the operator to watch. No trade path reads `_spots`, so
        this was display-only, but it corrupted the display that gates the
        rollout.

        Args:
            evt: ProtoOASpotEvent with bid/ask in 1/100_000 protocol units.
        """
        # A spot event carries only the side(s) that changed: bid and ask
        # are optional fields, and live ticks routinely update one side at
        # a time. Reading an absent side yields protobuf's default 0, so
        # storing both unconditionally clobbered the retained side with 0 --
        # a BUY position's P&L then computed against bid=0 and displayed
        # -(entry x volume) as a huge bogus loss.
        prev_bid, prev_ask = self._spots.get(evt.symbolId, (None, None))
        bid = evt.bid / SPOT_PRICE_SCALE if evt.HasField('bid') else prev_bid
        ask = evt.ask / SPOT_PRICE_SCALE if evt.HasField('ask') else prev_ask
        self._spots[evt.symbolId] = (bid, ask)

    def snapshot(self) -> dict[int, dict[str, Any]]:
        """Return current state snapshot.

        Returns:
            Dict: {account_id: {"balance": float | None, "open_pnl": float,
                                "equity": float | None, "positions": [...]}}

            balance/equity are None if refresh_balances hasn't been called yet.
        """
        result = {}

        for account_id in set(self._balances.keys()) | set(self._positions.keys()):
            balance = self._balances.get(account_id)
            positions = self._positions.get(account_id, [])

            # Calculate open P&L
            open_pnl = 0.0
            positions_list = []

            for pos in positions:
                # Verify symbol_id is in our symbols_by_id
                if pos.symbol_id not in self._symbols_by_id:
                    # Log warning but surface the position with unknown P&L
                    log.warning(
                        "position %s references unknown symbol_id %s; "
                        "P&L cannot be calculated", pos.position_id, pos.symbol_id
                    )
                    positions_list.append({
                        "position_id": pos.position_id,
                        "symbol_id": pos.symbol_id,
                        "symbol": None,  # unknown symbol name
                        "side": pos.side.value,
                        "volume": pos.volume,
                        "entry_price": pos.price,
                        "pnl_quote": None,  # unknown due to missing symbol
                    })
                    continue

                # Get current bid/ask for this symbol. A side we have never
                # received stays None in _spots; fall back to the entry
                # price for that side (P&L 0), same as when no spot has
                # arrived at all.
                bid, ask = self._spots.get(pos.symbol_id, (None, None))
                if bid is None:
                    bid = pos.price
                if ask is None:
                    ask = pos.price

                # Calculate P&L for this position
                pnl = unrealized_pnl_quote(pos.side, pos.price, pos.volume, bid, ask)
                open_pnl += pnl

                # Add to positions list
                sym_name = self._symbols_by_id[pos.symbol_id].name
                positions_list.append({
                    "position_id": pos.position_id,
                    "symbol_id": pos.symbol_id,
                    "symbol": sym_name,
                    "side": pos.side.value,
                    "volume": pos.volume,
                    "entry_price": pos.price,
                    "pnl_quote": pnl,
                })

            # Compute equity: None if balance unknown, else balance + open_pnl
            equity = None if balance is None else (balance + open_pnl)

            result[account_id] = {
                "balance": balance,
                "open_pnl": open_pnl,
                "equity": equity,
                "positions": positions_list,
            }

        return result
