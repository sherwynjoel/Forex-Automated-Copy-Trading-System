"""Account state tracking: balance, equity, open P&L.

Tracks each account's balance, equity, and open P&L driven by cTrader
trader updates and spot (price) events.
"""

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOASpotEvent, ProtoOASubscribeSpotsReq, ProtoOATraderReq,
)
from twisted.internet import defer

from copier.ctrader.client import CTraderClient
from copier.domain.models import Side, SymbolInfo


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
    """Snapshot of an open position at a point in time."""
    position_id: int
    symbol: str
    side: Side
    volume: int
    entry_price: float


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
        self._balances: dict[int, float] = {}  # account_id -> balance
        self._positions: dict[int, list[PositionSnapshot]] = {}  # account_id -> positions
        self._spots: dict[int, tuple[float, float]] = {}  # symbol_id -> (bid, ask)

        # Wire up callbacks
        self._client.on_spot(self.on_spot)

    def refresh_balances(self, account_ids: list[int]) -> defer.Deferred:
        """Refresh balances for given accounts via ProtoOATraderReq.

        Sends a ProtoOATraderReq for each account and stores balance / 100.0
        (balance is in cents in the response).

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
            deferreds.append(d)
        return defer.DeferredList(deferreds)

    def _process_trader_response(self, response: Any, account_id: int) -> None:
        """Process trader response and extract balance.

        Args:
            response: ProtoOATraderRes or response object
            account_id: Account ID being updated
        """
        # response is a ProtoMessage, need to extract the trader
        # The balance is in cents, so divide by 100
        if hasattr(response, 'trader'):
            self._balances[account_id] = response.trader.balance / 100.0

    def set_positions(
        self, account_id: int, positions: Sequence[PositionSnapshot]
    ) -> None:
        """Set open positions for an account (from reconcile).

        Args:
            account_id: Account ID
            positions: List of open position snapshots
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
        # Collect all unique symbol IDs with open positions
        symbol_ids = set()
        for positions in self._positions.values():
            for pos in positions:
                # Find symbol_id for this symbol name
                for sym_id, sym_info in self._symbols_by_id.items():
                    if sym_info.name == pos.symbol:
                        symbol_ids.add(sym_id)
                        break

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
        and need to be scaled ÷ 100000.

        Args:
            evt: ProtoOASpotEvent with bid/ask in protocol format (scaled by 100000)
        """
        # Scale spot values from protocol format (int scaled by 100000)
        bid = evt.bid / 100000.0
        ask = evt.ask / 100000.0
        self._spots[evt.symbolId] = (bid, ask)

    def snapshot(self) -> dict[int, dict[str, Any]]:
        """Return current state snapshot.

        Returns:
            Dict: {account_id: {"balance": float, "open_pnl": float, "equity": float,
                                "positions": [...]}}
        """
        result = {}

        for account_id in set(self._balances.keys()) | set(self._positions.keys()):
            balance = self._balances.get(account_id, 0.0)
            positions = self._positions.get(account_id, [])

            # Calculate open P&L
            open_pnl = 0.0
            positions_list = []

            for pos in positions:
                # Find symbol_id for this symbol
                symbol_id = None
                for sym_id, sym_info in self._symbols_by_id.items():
                    if sym_info.name == pos.symbol:
                        symbol_id = sym_id
                        break

                if symbol_id is None:
                    continue

                # Get current bid/ask for this symbol
                bid, ask = self._spots.get(symbol_id, (pos.entry_price, pos.entry_price))

                # Calculate P&L for this position
                pnl = unrealized_pnl_quote(pos.side, pos.entry_price, pos.volume, bid, ask)
                open_pnl += pnl

                # Add to positions list
                positions_list.append({
                    "position_id": pos.position_id,
                    "symbol": pos.symbol,
                    "side": pos.side.value,
                    "volume": pos.volume,
                    "entry_price": pos.entry_price,
                    "pnl_quote": pnl,
                })

            equity = balance + open_pnl

            result[account_id] = {
                "balance": balance,
                "open_pnl": open_pnl,
                "equity": equity,
                "positions": positions_list,
            }

        return result
