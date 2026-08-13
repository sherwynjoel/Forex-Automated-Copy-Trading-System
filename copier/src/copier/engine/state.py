"""Account state tracking: balance, equity, open P&L.

Tracks each account's balance, equity, and open P&L driven by cTrader
trader updates and spot (price) events.
"""

import logging
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOASpotEvent, ProtoOASubscribeSpotsReq, ProtoOATraderReq,
)
from twisted.internet import defer

from copier.ctrader.client import CTraderClient
from copier.domain.models import Side, SymbolInfo

log = logging.getLogger(__name__)


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

        # Wire up callbacks
        self._client.on_spot(self.on_spot)

    def refresh_balances(self, account_ids: list[int]) -> defer.Deferred:
        """Refresh balances for given accounts via ProtoOATraderReq.

        Sends a ProtoOATraderReq for each account and stores balance scaled by
        moneyDigits (or 10**2 if absent). Tracks whether balance data was received.

        Args:
            account_ids: List of account IDs to refresh

        Returns:
            Deferred that fires when all trader requests complete
        """
        deferreds = []
        for account_id in account_ids:
            req = ProtoOATraderReq()
            req.ctidTraderAccountId = account_id
            d = self._client.send(req)
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

        if not symbol_ids:
            # No positions, return immediate success
            return defer.succeed(None)

        # Subscribe on master account
        req = ProtoOASubscribeSpotsReq()
        req.ctidTraderAccountId = self._master_account_id
        for sym_id in sorted(symbol_ids):
            req.symbolId.append(sym_id)

        return self._client.send(req)

    def on_spot(self, evt: ProtoOASpotEvent) -> None:
        """Handle spot event from the client.

        Updates bid/ask for the symbol. Spot values are in protocol format
        and need to be scaled by the symbol's digits field (÷ 10**digits).
        Falls back to ÷ 10**5 if symbol/digits is unavailable.

        Args:
            evt: ProtoOASpotEvent with bid/ask in protocol format (scaled by 10**digits)
        """
        # Determine scaling factor from symbol digits
        digits = 5  # default/fallback
        if evt.symbolId in self._symbols_by_id:
            digits = self._symbols_by_id[evt.symbolId].digits

        scale_factor = 10 ** digits
        bid = evt.bid / scale_factor
        ask = evt.ask / scale_factor
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

                # Get current bid/ask for this symbol
                bid, ask = self._spots.get(pos.symbol_id, (pos.price, pos.price))

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
