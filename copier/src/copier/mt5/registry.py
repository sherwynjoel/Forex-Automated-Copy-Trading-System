"""In-memory state of every MT5 terminal, fed by its reports.

The terminal is the only thing that knows its own book; it reports it on
every poll. This registry keeps the last report per account -- positions,
orders, balance, equity, the symbol map from the hello -- and serves it to
everything that would otherwise ask a cTrader client: the reconciler (as
PositionSnapshot/OrderSnapshot), get_state (as the accounts block), the
lane's operator actions and queries. Positions are persisted through the
same repo calls the cTrader resync uses, and only when the book actually
changed, so a 250 ms poll does not become four upserts a second.

After a copier restart the registry is empty until the next report; the
symbol map and the hedging flag come back from Postgres on first use.
"""

import logging
from dataclasses import dataclass, field

from copier.domain.models import Side, SymbolInfo
from copier.engine.reconcile import OrderSnapshot, PositionSnapshot
from copier.mt5.protocol import HelloReport, HelloSymbol, ReportPosition, SyncReport
from copier.mt5.symbols import symbol_infos_from_hello

log = logging.getLogger(__name__)

OFFLINE_AFTER_S = 15.0


@dataclass
class _AccountState:
    org_id: int
    hedging: bool | None = None
    hedging_loaded: bool = False
    hello_symbols: list[HelloSymbol] = field(default_factory=list)
    symbols: dict[str, SymbolInfo] | None = None     # by broker name; None = not loaded yet
    report: SyncReport | None = None
    last_seen: float | None = None
    persisted_signature: tuple | None = None


class MT5Registry:
    def __init__(self, repo, clock=None):
        self._repo = repo
        self._clock = clock
        self._accounts: dict[int, _AccountState] = {}

    def _state(self, account_id: int, org_id: int | None = None) -> _AccountState:
        state = self._accounts.get(account_id)
        if state is None:
            state = _AccountState(org_id=org_id if org_id is not None else 0)
            self._accounts[account_id] = state
        if org_id is not None:
            state.org_id = org_id
        return state

    # ---------- symbols ----------

    def symbols_by_name(self, account_id: int) -> dict[str, SymbolInfo]:
        """The account's symbols keyed by BROKER name, from the last hello or
        (after a restart) the symbol cache."""
        state = self._state(account_id)
        if state.symbols is None:
            state.symbols = dict(self._repo.load_symbol_cache(account_id))
        return state.symbols

    def symbol_by_name(self, account_id: int, name: str) -> SymbolInfo | None:
        return self.symbols_by_name(account_id).get(name)

    @staticmethod
    def _symbol_id(symbols: dict[str, SymbolInfo], name: str) -> int:
        info = symbols.get(name)
        return info.symbol_id if info is not None else 0

    # ---------- reports in ----------

    def update_from_hello(self, account_id: int, org_id: int, hello: HelloReport,
                          now: float) -> None:
        """A (chunk of a) hello: the symbol list accumulates across chunks
        and is persisted on the last one; ids are minted over the whole
        list so a collision across chunks is still probed."""
        state = self._state(account_id, org_id)
        state.hedging = hello.hedging
        state.hedging_loaded = True
        state.last_seen = now
        if hello.chunk <= 1:
            state.hello_symbols = []
        state.hello_symbols.extend(hello.symbols)
        state.symbols = {info.name: info for info in symbol_infos_from_hello(state.hello_symbols)}
        if hello.chunk >= hello.chunks:
            self._repo.save_symbol_cache(account_id, state.symbols)

    def update_from_sync(self, account_id: int, org_id: int, report: SyncReport,
                         now: float) -> None:
        """Store the report; persist positions when the book changed."""
        state = self._state(account_id, org_id)
        state.report = report
        state.last_seen = now
        signature = tuple(sorted(
            (p.ticket, p.volume, p.stop_loss, p.take_profit) for p in report.positions))
        if signature == state.persisted_signature:
            return
        positions, _orders = self.snapshot(account_id)
        symbols_by_id = {info.symbol_id: info
                         for info in self.symbols_by_name(account_id).values()}
        self._repo.upsert_positions(account_id, org_id, positions, symbols_by_id)
        self._repo.close_missing_positions(account_id, [p.position_id for p in positions])
        state.persisted_signature = signature

    # ---------- reads ----------

    def report(self, account_id: int) -> SyncReport | None:
        state = self._accounts.get(account_id)
        return state.report if state is not None else None

    def position(self, account_id: int, ticket: int) -> ReportPosition | None:
        report = self.report(account_id)
        if report is None:
            return None
        return next((p for p in report.positions if p.ticket == ticket), None)

    def _labels(self, state: _AccountState, account_id: int) -> dict[int, str]:
        """ticket -> the label reconcile expects. A copy of a master position
        is 'copy:m<master>'; anything that came from a master pending order
        is 'copy:o<order>' on both the order and the position it became,
        which is what compute_drift's unfilled-order check looks for."""
        labels: dict[int, str] = {}
        for m in self._repo.mapping_rows(org_id=state.org_id):
            if m.get("slave_account_id") != account_id:
                continue
            if m.get("master_order_id"):
                label = f"copy:o{m['master_order_id']}"
                if m.get("slave_order_id"):
                    labels[m["slave_order_id"]] = label
                if m.get("slave_position_id"):
                    labels[m["slave_position_id"]] = label
            elif m.get("master_position_id") and m.get("slave_position_id"):
                labels[m["slave_position_id"]] = f"copy:m{m['master_position_id']}"
        return labels

    def snapshot(self, account_id: int, with_labels: bool = True):
        """(positions, orders) as reconcile's snapshot types, or None before
        the first report. with_labels=False skips the mapping lookup (the
        ingress diff needs only the book)."""
        state = self._accounts.get(account_id)
        if state is None or state.report is None:
            return None
        symbols = self.symbols_by_name(account_id)
        labels = self._labels(state, account_id) if with_labels else {}
        positions = [
            PositionSnapshot(
                position_id=p.ticket, symbol_id=self._symbol_id(symbols, p.symbol),
                side=Side(p.side), volume=p.volume, price=p.open_price,
                label=labels.get(p.ticket, p.comment),
                stop_loss=p.stop_loss, take_profit=p.take_profit,
            )
            for p in state.report.positions
        ]
        orders = [
            OrderSnapshot(
                order_id=o.ticket, symbol_id=self._symbol_id(symbols, o.symbol),
                volume=o.volume, label=labels.get(o.ticket, o.comment),
                side=Side.BUY if o.order_type.startswith("BUY") else Side.SELL,
                order_type=o.order_type.split("_", 1)[1], price=o.price,
                stop_loss=o.stop_loss, take_profit=o.take_profit,
            )
            for o in state.report.orders
        ]
        return positions, orders

    def account_block(self, account_id: int) -> dict | None:
        """The account's entry in get_state's `accounts` block, in exactly the
        shape AccountStateTracker.snapshot() produces for a cTrader account
        -- balance, equity and marks as the terminal reports them."""
        state = self._accounts.get(account_id)
        if state is None or state.report is None:
            return None
        symbols = self.symbols_by_name(account_id)
        report = state.report
        return {
            "balance": report.balance,
            "equity": report.equity,
            "open_pnl": sum(p.pnl for p in report.positions),
            "positions": [
                {"position_id": p.ticket, "symbol_id": self._symbol_id(symbols, p.symbol),
                 "symbol": p.symbol, "side": p.side, "volume": p.volume,
                 "entry_price": p.open_price, "stop_loss": p.stop_loss,
                 "take_profit": p.take_profit, "pnl_quote": p.pnl,
                 "current_price": p.current_price}
                for p in report.positions
            ],
        }

    def last_seen(self, account_id: int) -> float | None:
        state = self._accounts.get(account_id)
        return state.last_seen if state is not None else None

    def is_online(self, account_id: int, now: float) -> bool:
        last = self.last_seen(account_id)
        return last is not None and (now - last) < OFFLINE_AFTER_S

    def hedging(self, account_id: int) -> bool | None:
        """The hello's hedging flag; read from the link row after a restart."""
        state = self._accounts.get(account_id)
        if state is not None and state.hedging_loaded:
            return state.hedging
        link = self._repo.load_mt5_link(account_id)
        if link is None or link.get("hedging") is None:
            return state.hedging if state is not None else None
        state = self._state(account_id)
        state.hedging = link["hedging"]
        state.hedging_loaded = True
        return state.hedging

    def margin_mode(self, account_id: int) -> str | None:
        """"hedging" | "netting" from the hello's flag (spec "Netting
        accounts": both are accepted); None before any hello."""
        hedging = self.hedging(account_id)
        if hedging is None:
            return None
        return "hedging" if hedging else "netting"
