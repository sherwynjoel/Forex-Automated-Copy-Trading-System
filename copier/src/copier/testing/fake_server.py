"""Fake cTrader protobuf server for integration testing."""

import itertools
import time
from typing import Any

from ctrader_open_api.messages.OpenApiCommonMessages_pb2 import ProtoHeartbeatEvent, ProtoMessage
from ctrader_open_api.messages import OpenApiMessages_pb2 as oa
from ctrader_open_api.messages import OpenApiModelMessages_pb2 as model
from twisted.internet.protocol import Factory
from twisted.protocols.basic import Int32StringReceiver

from copier.testing.tls import make_self_signed_context


class _Proto(Int32StringReceiver):
    """Protocol handler for cTrader messages."""

    MAX_LENGTH = 16 * 1024 * 1024

    def connectionMade(self):
        self.factory.server.protocols.append(self)
        # Accounts confirmed account-authed on THIS connection specifically
        # (see FakeCTraderServer.enforce_auth) -- distinct per connection,
        # naturally, since each _Proto instance is its own object.
        self.authed_accounts: set[int] = set()

    def stringReceived(self, data):
        msg = ProtoMessage()
        msg.ParseFromString(data)
        self.factory.server.handle(self, msg)

    def send_payload(self, res, client_msg_id=""):
        out = ProtoMessage(
            payloadType=res.payloadType,
            payload=res.SerializeToString(),
            clientMsgId=client_msg_id,
        )
        self.sendString(out.SerializeToString())


class FakeCTraderServer:
    """Fake cTrader server for testing."""

    def __init__(self, auto_fill: bool = True):
        self.auto_fill = auto_fill
        self.accounts: dict[int, str] = {}
        self.symbols = [
            {
                "symbol_id": 1,
                "name": "EURUSD",
                "digits": 5,
                "lot_size": 10_000_000,
                "min_volume": 100_000,
                "step_volume": 100_000,
            }
        ]
        self.balances: dict[int, int] = {}
        # Price every auto-filled deal reports as ProtoOADeal.executionPrice.
        # The real server always sets it on a fill; without it the copier's
        # slave-fill handler had no price to stamp onto the mapping row, so
        # the Positions screen's Fill Price column (spec §7, see
        # T9c) were untestable end to end. Scriptable so a test can model a
        # slave filling away from the master's price.
        self.execution_price: float = 1.10500
        self.open_positions: dict[int, list] = {}
        self.pending_orders: dict[int, list] = {}
        self.next_tokens: tuple[str, str] | None = None
        # Scriptable history/details data for the read-model handlers
        # (queries.py): account_id -> list of deal/order dicts, plus the
        # broker-level asset table and per-account ProtoOATrader field
        # overrides. See _handle_deal_list_req/_handle_order_list_req/
        # _handle_trader_req for the recognized keys.
        self.deals: dict[int, list[dict]] = {}
        self.historical_orders: dict[int, list[dict]] = {}
        self.assets: list[dict] = [
            {"asset_id": 1, "name": "USD", "display_name": "US Dollar"},
        ]
        self.trader_details: dict[int, dict] = {}
        # (buy_factor, sell_factor): expected margin = volume * factor, in
        # broker cents (moneyDigits=2).
        self.margin_factors: tuple[float, float] = (0.002, 0.002)
        # (account_id, symbol_id, period) -> list of bar dicts for
        # _handle_get_trendbars_req.
        self.trendbars: dict[tuple, list[dict]] = {}
        # account_id -> list of deposit/withdraw dicts for
        # _handle_cash_flow_req.
        self.cash_flows: dict[int, list[dict]] = {}
        # When set, the NEXT tagged read-model request (trader, asset list,
        # deal list, order list) answers with a ProtoOAErrorRes carrying
        # this errorCode instead of data, then clears -- so tests can prove
        # the query layer maps broker errors to failures.
        self.fail_next_query_error_code: str | None = None
        self.reject_next_order: bool = False  # Scriptable rejection
        self.reject_error_code: str = "CH_TRADING_DISABLED"  # Scriptable reject errorCode
        # Opt-in (off by default -- most tests don't care about auth
        # sequencing): when True, every trade handler rejects a request for
        # an account that has not completed ProtoOAAccountAuthReq on the
        # SAME connection the request arrived on, faithful to real
        # cTrader. Exists to let CI catch the NEW-1 race (send_no_reply's
        # instant=True write can otherwise reach the wire before this
        # connection's own account-auth round trip completes, e.g. right
        # after a reconnect) -- with this off, the fake accepts trades
        # regardless of auth state and that race is invisible to tests.
        self.enforce_auth: bool = False
        self.requests, self.app_auths, self.account_auths, self.heartbeats = (
            [],
            [],
            [],
            [],
        )
        self.protocols: list[_Proto] = []
        self._position_ids = itertools.count(5000)
        self._order_ids = itertools.count(9000)
        self._deal_ids = itertools.count(1000)
        # position_id -> currently open volume, for ANY account (master or
        # slave -- position_ids are drawn from one global counter above, so
        # they never collide across accounts). Populated when a MARKET order
        # fills; consulted by _handle_close_position_req so a close can
        # correctly report whether it was full or partial (real cTrader:
        # position.tradeData.volume is the REMAINING volume after the close,
        # and positionStatus stays OPEN when some volume remains). Without
        # this, every close looked full, and MasterPositionClosed's
        # remaining_volume was always 0 (see engine/normalize.py), which
        # collapses partial_close_volume() to a full close every time
        # (domain/sizing.py:25) -- the fraction math this fake exists to let
        # the e2e exercise was structurally unreachable.
        self._position_volumes: dict[int, int] = {}
        self._listening = None
        self._handlers: dict[int, Any] = {}
        self._setup_handlers()

    def _setup_handlers(self):
        """Setup message payload type handlers."""
        self._handlers[oa.ProtoOAApplicationAuthReq().payloadType] = (
            self._handle_app_auth_req
        )
        self._handlers[oa.ProtoOAAccountAuthReq().payloadType] = (
            self._handle_account_auth_req
        )
        self._handlers[oa.ProtoOASymbolsListReq().payloadType] = (
            self._handle_symbols_list_req
        )
        self._handlers[oa.ProtoOASymbolByIdReq().payloadType] = (
            self._handle_symbol_by_id_req
        )
        self._handlers[oa.ProtoOAReconcileReq().payloadType] = (
            self._handle_reconcile_req
        )
        self._handlers[oa.ProtoOAGetAccountListByAccessTokenReq().payloadType] = (
            self._handle_get_account_list_req
        )
        self._handlers[oa.ProtoOARefreshTokenReq().payloadType] = (
            self._handle_refresh_token_req
        )
        self._handlers[oa.ProtoOASubscribeSpotsReq().payloadType] = (
            self._handle_subscribe_spots_req
        )
        self._handlers[oa.ProtoOATraderReq().payloadType] = self._handle_trader_req
        self._handlers[oa.ProtoOAAssetListReq().payloadType] = (
            self._handle_asset_list_req
        )
        self._handlers[oa.ProtoOADealListReq().payloadType] = (
            self._handle_deal_list_req
        )
        self._handlers[oa.ProtoOAOrderListReq().payloadType] = (
            self._handle_order_list_req
        )
        self._handlers[oa.ProtoOAExpectedMarginReq().payloadType] = (
            self._handle_expected_margin_req
        )
        self._handlers[oa.ProtoOAGetTrendbarsReq().payloadType] = (
            self._handle_get_trendbars_req
        )
        self._handlers[oa.ProtoOACashFlowHistoryListReq().payloadType] = (
            self._handle_cash_flow_req
        )
        self._handlers[oa.ProtoOADealListByPositionIdReq().payloadType] = (
            self._handle_deal_list_by_position_req
        )
        self._handlers[oa.ProtoOANewOrderReq().payloadType] = (
            self._handle_new_order_req
        )
        self._handlers[oa.ProtoOAClosePositionReq().payloadType] = (
            self._handle_close_position_req
        )
        self._handlers[oa.ProtoOAAmendPositionSLTPReq().payloadType] = (
            self._handle_amend_position_sltp_req
        )
        self._handlers[oa.ProtoOAAmendOrderReq().payloadType] = (
            self._handle_amend_order_req
        )
        self._handlers[oa.ProtoOACancelOrderReq().payloadType] = (
            self._handle_cancel_order_req
        )
        self._handlers[ProtoHeartbeatEvent().payloadType] = self._handle_heartbeat

    def listen(self, reactor, port: int = 0) -> int:
        """Listen on `port` (default: a random free port, the in-process-test
        default) with TLS. `port=0` is Twisted's convention for "pick any
        free port"; fake_main.py (the compose-level e2e's standalone
        process) passes the real cTrader port (5035) instead, since it has
        no in-process caller to hand a random port back to."""
        factory = Factory.forProtocol(_Proto)
        factory.server = self
        self._listening = reactor.listenSSL(port, factory, make_self_signed_context())
        return self._listening.getHost().port

    def shutdown(self):
        """Shutdown the server."""
        self.drop_all_connections()
        if self._listening:
            self._listening.stopListening()

    def drop_all_connections(self):
        """Drop all client connections."""
        for p in list(self.protocols):
            p.transport.loseConnection()
        self.protocols.clear()

    def broadcast(self, event):
        """Broadcast an event to all connected clients (untagged, no clientMsgId)."""
        for p in self.protocols:
            p.send_payload(event, client_msg_id="")

    def push_execution(self, evt):
        """Push an execution event to all clients."""
        self.broadcast(evt)

    def push_trader_update(self, account_id: int, balance: int, money_digits: int = 2):
        """Broadcast a ProtoOATraderUpdatedEvent (pushed balance change)."""
        evt = oa.ProtoOATraderUpdatedEvent()
        evt.ctidTraderAccountId = account_id
        evt.trader.ctidTraderAccountId = account_id
        evt.trader.balance = balance
        evt.trader.moneyDigits = money_digits
        evt.trader.depositAssetId = 1  # required field in proto2
        self.broadcast(evt)

    def push_margin_call(self, account_id: int, threshold: float = 50.0):
        """Broadcast a ProtoOAMarginCallTriggerEvent."""
        evt = oa.ProtoOAMarginCallTriggerEvent()
        evt.ctidTraderAccountId = account_id
        evt.marginCall.marginCallType = (
            model.ProtoOANotificationType.MARGIN_LEVEL_THRESHOLD_1)
        evt.marginCall.marginLevelThreshold = threshold
        self.broadcast(evt)

    def push_spot(self, ctid_trader_account_id: int, symbol_id: int, bid: int, ask: int):
        """Push a spot event to all clients."""
        spot = oa.ProtoOASpotEvent()
        spot.ctidTraderAccountId = ctid_trader_account_id
        spot.symbolId = symbol_id
        spot.bid = bid
        spot.ask = ask
        self.broadcast(spot)

    def reset_book(self) -> None:
        """Flatten this fake broker's whole book: every account's open
        positions, working orders, and per-position volume tracking.

        Exists for the compose-level e2e tests, whose fake-ctrader container
        outlives any one test: they truncate the DATABASE to start clean, but
        that says nothing about the broker's memory, and
        register_market_fill() above merges a same-side fill into whatever
        position that account already had -- from a previous test, or a
        previous run of the same test. Without this the second run of
        e2e/test_full_stack.py sees its "1.00 lot then +0.50" master position
        report a total carried over from before, and the two e2e tests (which
        share account ids) are order-dependent.

        Deliberately leaves everything that is NOT the book alone: live
        connections and their account-auth state, the symbol table,
        scriptable balances/history/details, and the id counters -- ids stay
        monotonic so a new run's positions can never be confused with an
        earlier one's.
        """
        self.open_positions.clear()
        self.pending_orders.clear()
        self._position_volumes.clear()

    def register_market_fill(self, account_id: int, symbol_id: int, trade_side: int,
                             volume: int, label: str = "") -> tuple[int, int]:
        """Book a MARKET fill against this account, MERGING same-side adds.

        Returns (position_id, position_total_volume_after_this_fill).

        Real cTrader aggregates: a second market order in the SAME direction
        on the SAME symbol does not open a second position, it increases the
        existing one -- the resulting ORDER_FILLED carries the ORIGINAL
        positionId, a `deal.filledVolume` of just the added amount, and a
        `position.tradeData.volume` of the new total. (An OPPOSITE-side
        order is a different matter -- hedging accounts open a separate
        position -- and is left as a new position here, matching this
        fake's previous behaviour for every case any other test exercises.)

        This fake used to mint a fresh position_id for every single market
        fill, i.e. it modelled a broker that never aggregates. That made the
        entire position-increase path (spec/plan Task 5, domain/decision.py's
        delta OpenMarket, and the N2 defect in the persistence seam beneath
        it) structurally unreachable from any test that drives real wire
        traffic -- both for a MASTER add (scripted through fake_main's
        /fill) and for the SLAVE side, where the copier's own second
        ProtoOANewOrderReq has to land on the slave's existing position for
        the mapping row's aggregate volume to mean anything.
        """
        existing = next(
            (p for p in self.open_positions.get(account_id, [])
             if p["symbol_id"] == symbol_id and p["trade_side"] == trade_side),
            None,
        )
        if existing is not None:
            position_id = existing["position_id"]
            existing["volume"] += volume
            total = existing["volume"]
        else:
            position_id = next(self._position_ids)
            total = volume
            self.open_positions.setdefault(account_id, []).append({
                "position_id": position_id,
                "symbol_id": symbol_id,
                "volume": volume,
                "trade_side": trade_side,
                "label": label,
            })
        self._position_volumes[position_id] = total
        return position_id, total

    def handle(self, proto, msg):
        """Handle an incoming message."""
        handler = self._handlers.get(msg.payloadType)
        if handler:
            handler(proto, msg)

    def _handle_app_auth_req(self, proto, msg):
        """Handle application authentication request."""
        req = oa.ProtoOAApplicationAuthReq()
        req.ParseFromString(msg.payload)
        self.app_auths.append((req.clientId, req.clientSecret))

        res = oa.ProtoOAApplicationAuthRes()
        proto.send_payload(res, msg.clientMsgId)

    def _handle_account_auth_req(self, proto, msg):
        """Handle account authentication request."""
        req = oa.ProtoOAAccountAuthReq()
        req.ParseFromString(msg.payload)

        # Validate token if accounts are set
        if self.accounts and req.ctidTraderAccountId not in self.accounts:
            error = oa.ProtoOAErrorRes()
            error.ctidTraderAccountId = req.ctidTraderAccountId
            error.errorCode = "CH_UNKNOWN_ACCOUNT"
            proto.send_payload(error, msg.clientMsgId)
            return

        if self.accounts and self.accounts[req.ctidTraderAccountId] != req.accessToken:
            error = oa.ProtoOAErrorRes()
            error.ctidTraderAccountId = req.ctidTraderAccountId
            error.errorCode = "CH_ACCESS_TOKEN_INVALID"
            proto.send_payload(error, msg.clientMsgId)
            return

        self.account_auths.append(req.ctidTraderAccountId)
        proto.authed_accounts.add(req.ctidTraderAccountId)

        res = oa.ProtoOAAccountAuthRes()
        res.ctidTraderAccountId = req.ctidTraderAccountId
        proto.send_payload(res, msg.clientMsgId)

    def _reject_if_not_authed(self, proto, account_id: int) -> bool:
        """When enforce_auth is on, reject (broadcast an untagged
        ProtoOAOrderErrorEvent, matching how a trade request's outcome
        normally arrives -- see send_no_reply) and return True if
        `account_id` has not completed account-auth on THIS connection.
        Off by default; see enforce_auth's docstring in __init__."""
        if not self.enforce_auth or account_id in proto.authed_accounts:
            return False
        err = oa.ProtoOAOrderErrorEvent()
        err.ctidTraderAccountId = account_id
        err.errorCode = "ACCOUNT_NOT_AUTHORIZED"
        self.broadcast(err)
        return True

    def _handle_symbols_list_req(self, proto, msg):
        """Handle symbols list request."""
        req = oa.ProtoOASymbolsListReq()
        req.ParseFromString(msg.payload)

        res = oa.ProtoOASymbolsListRes()
        res.ctidTraderAccountId = req.ctidTraderAccountId
        for sym in self.symbols:
            light_sym = res.symbol.add()
            light_sym.symbolId = sym["symbol_id"]
            light_sym.symbolName = sym["name"]
            light_sym.enabled = True

        proto.send_payload(res, msg.clientMsgId)

    def _handle_symbol_by_id_req(self, proto, msg):
        """Handle symbol by ID request."""
        req = oa.ProtoOASymbolByIdReq()
        req.ParseFromString(msg.payload)

        res = oa.ProtoOASymbolByIdRes()
        res.ctidTraderAccountId = req.ctidTraderAccountId

        sym_map = {s["symbol_id"]: s for s in self.symbols}

        for sym_id in req.symbolId:
            if sym_id in sym_map:
                sym = sym_map[sym_id]
                full_sym = res.symbol.add()
                full_sym.symbolId = sym["symbol_id"]
                full_sym.digits = sym["digits"]
                full_sym.pipPosition = 4
                full_sym.lotSize = sym["lot_size"]
                full_sym.minVolume = sym["min_volume"]
                full_sym.stepVolume = sym["step_volume"]

        proto.send_payload(res, msg.clientMsgId)

    def _handle_reconcile_req(self, proto, msg):
        """Handle reconcile request."""
        req = oa.ProtoOAReconcileReq()
        req.ParseFromString(msg.payload)

        res = oa.ProtoOAReconcileRes()
        res.ctidTraderAccountId = req.ctidTraderAccountId

        if req.ctidTraderAccountId in self.open_positions:
            for pos_data in self.open_positions[req.ctidTraderAccountId]:
                pos = res.position.add()
                pos.positionId = pos_data["position_id"]
                pos.tradeData.symbolId = pos_data["symbol_id"]
                pos.tradeData.volume = pos_data["volume"]
                pos.tradeData.tradeSide = pos_data["trade_side"]
                pos.tradeData.label = pos_data.get("label", "")
                pos.positionStatus = model.ProtoOAPositionStatus.POSITION_STATUS_OPEN
                pos.swap = 0
                pos.price = pos_data.get("price", 0)

        if req.ctidTraderAccountId in self.pending_orders:
            for ord_data in self.pending_orders[req.ctidTraderAccountId]:
                order = res.order.add()
                order.orderId = ord_data["order_id"]
                order.tradeData.symbolId = ord_data["symbol_id"]
                order.tradeData.volume = ord_data["volume"]
                order.tradeData.tradeSide = ord_data["trade_side"]
                order.tradeData.label = ord_data.get("label", "")
                order.orderStatus = model.ProtoOAOrderStatus.ORDER_STATUS_ACCEPTED
                order.orderType = ord_data["order_type"]

        proto.send_payload(res, msg.clientMsgId)

    def _handle_get_account_list_req(self, proto, msg):
        """Handle get account list request."""
        req = oa.ProtoOAGetAccountListByAccessTokenReq()
        req.ParseFromString(msg.payload)

        res = oa.ProtoOAGetAccountListByAccessTokenRes()
        res.accessToken = req.accessToken
        for account_id in self.accounts:
            acc = res.ctidTraderAccount.add()
            acc.ctidTraderAccountId = account_id
            acc.isLive = False

        proto.send_payload(res, msg.clientMsgId)

    def _handle_refresh_token_req(self, proto, msg):
        """Handle refresh token request."""
        req = oa.ProtoOARefreshTokenReq()
        req.ParseFromString(msg.payload)

        res = oa.ProtoOARefreshTokenRes()
        if self.next_tokens:
            res.accessToken, res.refreshToken = self.next_tokens
        else:
            res.accessToken = "new-access-token"
            res.refreshToken = "new-refresh-token"
        res.tokenType = "Bearer"
        res.expiresIn = 3600

        proto.send_payload(res, msg.clientMsgId)

    def _handle_subscribe_spots_req(self, proto, msg):
        """Handle subscribe spots request."""
        req = oa.ProtoOASubscribeSpotsReq()
        req.ParseFromString(msg.payload)

        res = oa.ProtoOASubscribeSpotsRes()
        res.ctidTraderAccountId = req.ctidTraderAccountId

        proto.send_payload(res, msg.clientMsgId)

    def _handle_expected_margin_req(self, proto, msg):
        """Handle expected margin request: volume * margin_factors, cents."""
        req = oa.ProtoOAExpectedMarginReq()
        req.ParseFromString(msg.payload)

        if self._maybe_fail_query(proto, msg):
            return

        res = oa.ProtoOAExpectedMarginRes()
        res.ctidTraderAccountId = req.ctidTraderAccountId
        res.moneyDigits = 2
        buy_f, sell_f = self.margin_factors
        for volume in req.volume:
            entry = res.margin.add()
            entry.volume = volume
            entry.buyMargin = int(volume * buy_f)
            entry.sellMargin = int(volume * sell_f)

        proto.send_payload(res, msg.clientMsgId)

    def _handle_get_trendbars_req(self, proto, msg):
        """Handle trendbars request from the scriptable self.trendbars table,
        filtered to the requested window."""
        req = oa.ProtoOAGetTrendbarsReq()
        req.ParseFromString(msg.payload)

        if self._maybe_fail_query(proto, msg):
            return

        res = oa.ProtoOAGetTrendbarsRes()
        res.ctidTraderAccountId = req.ctidTraderAccountId
        res.period = req.period
        res.symbolId = req.symbolId
        res.timestamp = req.toTimestamp  # required field in proto2
        key = (req.ctidTraderAccountId, req.symbolId, req.period)
        for b in self.trendbars.get(key, []):
            ts_ms = b["utc_ts_minutes"] * 60_000
            if not (req.fromTimestamp <= ts_ms <= req.toTimestamp):
                continue
            bar = res.trendbar.add()
            bar.utcTimestampInMinutes = b["utc_ts_minutes"]
            bar.volume = b["volume"]
            bar.low = b["low"]
            bar.deltaOpen = b["delta_open"]
            bar.deltaHigh = b["delta_high"]
            if "delta_close" in b:
                bar.deltaClose = b["delta_close"]
            bar.period = req.period

        proto.send_payload(res, msg.clientMsgId)

    def _handle_cash_flow_req(self, proto, msg):
        """Handle cash flow history request from self.cash_flows."""
        req = oa.ProtoOACashFlowHistoryListReq()
        req.ParseFromString(msg.payload)

        if self._maybe_fail_query(proto, msg):
            return

        res = oa.ProtoOACashFlowHistoryListRes()
        res.ctidTraderAccountId = req.ctidTraderAccountId
        for c in self.cash_flows.get(req.ctidTraderAccountId, []):
            if not (req.fromTimestamp <= c["timestamp"] <= req.toTimestamp):
                continue
            op = res.depositWithdraw.add()
            op.balanceHistoryId = c["id"]
            op.operationType = c["operation_type"]
            op.balance = c["balance"]
            op.delta = c["delta"]
            op.changeBalanceTimestamp = c["timestamp"]
            op.moneyDigits = c.get("money_digits", 2)
            if c.get("note"):
                op.externalNote = c["note"]

        proto.send_payload(res, msg.clientMsgId)

    def _handle_deal_list_by_position_req(self, proto, msg):
        """Handle deals-by-position request: self.deals filtered by
        position_id and window."""
        req = oa.ProtoOADealListByPositionIdReq()
        req.ParseFromString(msg.payload)

        if self._maybe_fail_query(proto, msg):
            return

        res = oa.ProtoOADealListByPositionIdRes()
        res.ctidTraderAccountId = req.ctidTraderAccountId
        for d in self.deals.get(req.ctidTraderAccountId, []):
            if d["position_id"] != req.positionId:
                continue
            if not (req.fromTimestamp <= d["execution_timestamp"] <= req.toTimestamp):
                continue
            self._fill_deal(res.deal.add(), d)

        proto.send_payload(res, msg.clientMsgId)

    def _maybe_fail_query(self, proto, msg) -> bool:
        """One-shot scripted failure for tagged read-model requests."""
        if not self.fail_next_query_error_code:
            return False
        code = self.fail_next_query_error_code
        self.fail_next_query_error_code = None
        err = oa.ProtoOAErrorRes()
        err.errorCode = code
        proto.send_payload(err, msg.clientMsgId)
        return True

    def _handle_trader_req(self, proto, msg):
        """Handle trader request."""
        req = oa.ProtoOATraderReq()
        req.ParseFromString(msg.payload)

        if self._maybe_fail_query(proto, msg):
            return

        res = oa.ProtoOATraderRes()
        res.ctidTraderAccountId = req.ctidTraderAccountId
        res.trader.ctidTraderAccountId = req.ctidTraderAccountId

        if req.ctidTraderAccountId in self.balances:
            res.trader.balance = self.balances[req.ctidTraderAccountId]
        else:
            res.trader.balance = 100000

        details = self.trader_details.get(req.ctidTraderAccountId, {})
        res.trader.depositAssetId = details.get("deposit_asset_id", 1)
        if "leverage_in_cents" in details:
            res.trader.leverageInCents = details["leverage_in_cents"]
        if "max_leverage" in details:
            res.trader.maxLeverage = details["max_leverage"]
        if "broker_name" in details:
            res.trader.brokerName = details["broker_name"]
        if "registration_timestamp" in details:
            res.trader.registrationTimestamp = details["registration_timestamp"]
        if "account_type" in details:
            res.trader.accountType = details["account_type"]
        if "trader_login" in details:
            res.trader.traderLogin = details["trader_login"]
        if "money_digits" in details:
            res.trader.moneyDigits = details["money_digits"]
        if "swap_free" in details:
            res.trader.swapFree = details["swap_free"]
        if "access_rights" in details:
            res.trader.accessRights = details["access_rights"]
        if "is_limited_risk" in details:
            res.trader.isLimitedRisk = details["is_limited_risk"]

        proto.send_payload(res, msg.clientMsgId)

    def _handle_asset_list_req(self, proto, msg):
        """Handle asset list request."""
        req = oa.ProtoOAAssetListReq()
        req.ParseFromString(msg.payload)

        if self._maybe_fail_query(proto, msg):
            return

        res = oa.ProtoOAAssetListRes()
        res.ctidTraderAccountId = req.ctidTraderAccountId
        for a in self.assets:
            asset = res.asset.add()
            asset.assetId = a["asset_id"]
            asset.name = a["name"]
            if a.get("display_name"):
                asset.displayName = a["display_name"]

        proto.send_payload(res, msg.clientMsgId)

    def _handle_deal_list_req(self, proto, msg):
        """Handle deal list (history) request, filtered by the requested
        [fromTimestamp, toTimestamp] window on executionTimestamp."""
        req = oa.ProtoOADealListReq()
        req.ParseFromString(msg.payload)

        if self._maybe_fail_query(proto, msg):
            return

        res = oa.ProtoOADealListRes()
        res.ctidTraderAccountId = req.ctidTraderAccountId
        res.hasMore = False
        for d in self.deals.get(req.ctidTraderAccountId, []):
            if not (req.fromTimestamp <= d["execution_timestamp"] <= req.toTimestamp):
                continue
            self._fill_deal(res.deal.add(), d)

        proto.send_payload(res, msg.clientMsgId)

    @staticmethod
    def _fill_deal(deal, d: dict) -> None:
        """Populate one ProtoOADeal from a scriptable deal dict (shared by
        the full-history and by-position handlers)."""
        deal.dealId = d["deal_id"]
        deal.orderId = d["order_id"]
        deal.positionId = d["position_id"]
        deal.volume = d["volume"]
        deal.filledVolume = d["filled_volume"]
        deal.symbolId = d["symbol_id"]
        deal.createTimestamp = d["create_timestamp"]
        deal.executionTimestamp = d["execution_timestamp"]
        deal.executionPrice = d["execution_price"]
        deal.tradeSide = d["trade_side"]
        deal.dealStatus = d["status"]
        if "commission" in d:
            deal.commission = d["commission"]
        deal.moneyDigits = d.get("money_digits", 2)
        cpd = d.get("close_position_detail")
        if cpd:
            deal.closePositionDetail.entryPrice = cpd["entry_price"]
            deal.closePositionDetail.grossProfit = cpd["gross_profit"]
            deal.closePositionDetail.swap = cpd["swap"]
            deal.closePositionDetail.commission = cpd["commission"]
            deal.closePositionDetail.balance = cpd["balance"]
            deal.closePositionDetail.closedVolume = cpd["closed_volume"]
            deal.closePositionDetail.moneyDigits = cpd.get("money_digits", 2)

    def _handle_order_list_req(self, proto, msg):
        """Handle order list (history) request, filtered by the requested
        window on utcLastUpdateTimestamp."""
        req = oa.ProtoOAOrderListReq()
        req.ParseFromString(msg.payload)

        if self._maybe_fail_query(proto, msg):
            return

        res = oa.ProtoOAOrderListRes()
        res.ctidTraderAccountId = req.ctidTraderAccountId
        res.hasMore = False
        for o in self.historical_orders.get(req.ctidTraderAccountId, []):
            if not (req.fromTimestamp <= o["utc_last_update_timestamp"] <= req.toTimestamp):
                continue
            order = res.order.add()
            order.orderId = o["order_id"]
            order.tradeData.symbolId = o["symbol_id"]
            order.tradeData.volume = o["volume"]
            order.tradeData.tradeSide = o["trade_side"]
            if "open_timestamp" in o:
                order.tradeData.openTimestamp = o["open_timestamp"]
            order.tradeData.label = o.get("label", "")
            order.orderType = o["order_type"]
            order.orderStatus = o["order_status"]
            order.utcLastUpdateTimestamp = o["utc_last_update_timestamp"]
            if "limit_price" in o:
                order.limitPrice = o["limit_price"]
            if "stop_price" in o:
                order.stopPrice = o["stop_price"]
            if "execution_price" in o:
                order.executionPrice = o["execution_price"]
            if "executed_volume" in o:
                order.executedVolume = o["executed_volume"]
            if "position_id" in o:
                order.positionId = o["position_id"]

        proto.send_payload(res, msg.clientMsgId)

    def _handle_new_order_req(self, proto, msg):
        """Handle new order request - broadcasts untagged execution events only.

        The real cTrader server never sends a synchronous, clientMsgId-tagged
        reply to a trade request; outcomes arrive exclusively as untagged
        broadcast ProtoOAExecutionEvents. No tagged response is sent here.
        """
        req = oa.ProtoOANewOrderReq()
        req.ParseFromString(msg.payload)

        # Record the trade request
        self.requests.append(req)

        if self._reject_if_not_authed(proto, req.ctidTraderAccountId):
            return

        # Check if we should reject this order
        if self.reject_next_order:
            self.reject_next_order = False
            reject_evt = oa.ProtoOAExecutionEvent()
            reject_evt.ctidTraderAccountId = req.ctidTraderAccountId
            reject_evt.executionType = model.ProtoOAExecutionType.ORDER_REJECTED
            reject_evt.errorCode = self.reject_error_code
            reject_evt.order.orderId = next(self._order_ids)
            reject_evt.order.clientOrderId = req.clientOrderId
            reject_evt.order.orderType = req.orderType
            reject_evt.order.orderStatus = model.ProtoOAOrderStatus.ORDER_STATUS_REJECTED
            reject_evt.order.tradeData.symbolId = req.symbolId
            reject_evt.order.tradeData.volume = req.volume
            reject_evt.order.tradeData.tradeSide = req.tradeSide
            self.broadcast(reject_evt)
            return

        if self.auto_fill:
            # For MARKET orders: accept then fill
            if req.orderType == model.ProtoOAOrderType.MARKET:
                order_id = next(self._order_ids)

                # Broadcast ORDER_ACCEPTED event (untagged)
                accept_evt = oa.ProtoOAExecutionEvent()
                accept_evt.ctidTraderAccountId = req.ctidTraderAccountId
                accept_evt.executionType = model.ProtoOAExecutionType.ORDER_ACCEPTED
                accept_evt.order.orderId = order_id
                accept_evt.order.orderStatus = model.ProtoOAOrderStatus.ORDER_STATUS_ACCEPTED
                accept_evt.order.orderType = req.orderType
                accept_evt.order.tradeData.symbolId = req.symbolId
                accept_evt.order.tradeData.volume = req.volume
                accept_evt.order.tradeData.tradeSide = req.tradeSide
                accept_evt.order.tradeData.label = req.label
                accept_evt.order.clientOrderId = req.clientOrderId
                self.broadcast(accept_evt)

                # Broadcast ORDER_FILLED event (untagged). register_market_fill
                # also keeps open_positions/_position_volumes in sync and
                # MERGES a same-side add into the existing position (real
                # cTrader aggregation) -- see its docstring.
                position_id, position_total = self.register_market_fill(
                    req.ctidTraderAccountId, req.symbolId, req.tradeSide, req.volume, req.label,
                )
                deal_id = next(self._deal_ids)
                fill_evt = oa.ProtoOAExecutionEvent()
                fill_evt.ctidTraderAccountId = req.ctidTraderAccountId
                fill_evt.executionType = model.ProtoOAExecutionType.ORDER_FILLED
                fill_evt.deal.dealId = deal_id
                fill_evt.deal.orderId = order_id
                fill_evt.deal.positionId = position_id
                fill_evt.deal.volume = req.volume
                fill_evt.deal.filledVolume = req.volume
                fill_evt.deal.symbolId = req.symbolId
                fill_evt.deal.tradeSide = req.tradeSide
                fill_evt.deal.dealStatus = model.ProtoOADealStatus.FILLED
                fill_evt.deal.createTimestamp = int(time.time() * 1000)
                fill_evt.deal.executionTimestamp = int(time.time() * 1000)
                # Real cTrader always reports the price a deal filled at; the
                # copier stamps it onto the mapping row (T9c) so the
                # Positions screen can show Fill Price.
                fill_evt.deal.executionPrice = self.execution_price
                fill_evt.position.positionId = position_id
                fill_evt.position.tradeData.symbolId = req.symbolId
                # The position's TOTAL volume after this fill (== req.volume
                # for a first open, more for a same-side add); deal.volume /
                # deal.filledVolume above stay the DELTA, which is what
                # normalize() reads as MasterPositionOpened.volume.
                fill_evt.position.tradeData.volume = position_total
                fill_evt.position.tradeData.tradeSide = req.tradeSide
                fill_evt.position.tradeData.label = req.label
                fill_evt.position.positionStatus = model.ProtoOAPositionStatus.POSITION_STATUS_OPEN
                fill_evt.position.swap = 0
                fill_evt.order.orderId = order_id
                fill_evt.order.orderStatus = model.ProtoOAOrderStatus.ORDER_STATUS_FILLED
                fill_evt.order.orderType = req.orderType
                fill_evt.order.tradeData.symbolId = req.symbolId
                fill_evt.order.tradeData.volume = req.volume
                fill_evt.order.tradeData.tradeSide = req.tradeSide
                fill_evt.order.tradeData.label = req.label
                fill_evt.order.clientOrderId = req.clientOrderId
                self.broadcast(fill_evt)
                # NB: register_market_fill above already recorded the
                # position in open_positions, so a ProtoOAReconcileReq sent
                # AFTER this fill -- e.g. the copier's resync()/state-tracker
                # refresh -- reports it as open instead of empty. Kept in
                # sync by _handle_close_position_req below.
            else:
                # For LIMIT/STOP orders: just accept (untagged)
                order_id = next(self._order_ids)
                accept_evt = oa.ProtoOAExecutionEvent()
                accept_evt.ctidTraderAccountId = req.ctidTraderAccountId
                accept_evt.executionType = model.ProtoOAExecutionType.ORDER_ACCEPTED
                accept_evt.order.orderId = order_id
                accept_evt.order.orderStatus = model.ProtoOAOrderStatus.ORDER_STATUS_ACCEPTED
                accept_evt.order.orderType = req.orderType
                accept_evt.order.tradeData.symbolId = req.symbolId
                accept_evt.order.tradeData.volume = req.volume
                accept_evt.order.tradeData.tradeSide = req.tradeSide
                accept_evt.order.tradeData.label = req.label
                accept_evt.order.clientOrderId = req.clientOrderId
                # Echo the requested price back (required for MasterPendingPlaced.price
                # to reflect reality when this account is a copier-monitored master).
                if req.orderType == model.ProtoOAOrderType.LIMIT and req.HasField("limitPrice"):
                    accept_evt.order.limitPrice = req.limitPrice
                elif req.orderType == model.ProtoOAOrderType.STOP and req.HasField("stopPrice"):
                    accept_evt.order.stopPrice = req.stopPrice
                self.broadcast(accept_evt)

    def _handle_close_position_req(self, proto, msg):
        """Handle close position request - broadcasts untagged execution event only."""
        req = oa.ProtoOAClosePositionReq()
        req.ParseFromString(msg.payload)

        # Record the trade request
        self.requests.append(req)

        if self._reject_if_not_authed(proto, req.ctidTraderAccountId):
            return

        if self.auto_fill:
            # Compute the position's REMAINING volume after this close, so a
            # request for less than the whole position reports a genuine
            # partial close (position stays OPEN) rather than always
            # reporting the position as fully closed regardless of
            # req.volume. Falls back to treating req.volume as the entire
            # position when it was never seen opened here (e.g. a
            # position_id fabricated directly by a test) -- preserves the
            # previous always-full-close behaviour for that case.
            current_volume = self._position_volumes.get(req.positionId, req.volume)
            remaining_volume = max(current_volume - req.volume, 0)
            self._position_volumes[req.positionId] = remaining_volume
            is_full_close = remaining_volume == 0

            # Broadcast ORDER_FILLED event with closePositionDetail (untagged)
            deal_id = next(self._deal_ids)
            fill_evt = oa.ProtoOAExecutionEvent()
            fill_evt.ctidTraderAccountId = req.ctidTraderAccountId
            fill_evt.executionType = model.ProtoOAExecutionType.ORDER_FILLED
            fill_evt.deal.dealId = deal_id
            fill_evt.deal.orderId = 0  # REQUIRED (no corresponding order for close)
            fill_evt.deal.positionId = req.positionId
            fill_evt.deal.volume = req.volume
            fill_evt.deal.filledVolume = req.volume
            fill_evt.deal.symbolId = 1  # Default, varies by position
            fill_evt.deal.tradeSide = model.ProtoOATradeSide.BUY  # Default, varies
            fill_evt.deal.dealStatus = model.ProtoOADealStatus.FILLED
            fill_evt.deal.createTimestamp = int(time.time() * 1000)
            fill_evt.deal.executionTimestamp = int(time.time() * 1000)
            fill_evt.deal.executionPrice = self.execution_price
            fill_evt.deal.closePositionDetail.closedVolume = req.volume
            fill_evt.deal.closePositionDetail.entryPrice = 10000  # Plausible default
            fill_evt.deal.closePositionDetail.grossProfit = 1000  # Plausible default
            fill_evt.deal.closePositionDetail.swap = 0
            fill_evt.deal.closePositionDetail.commission = 5  # Plausible default
            fill_evt.deal.closePositionDetail.balance = 100000  # Plausible default
            fill_evt.position.positionId = req.positionId
            fill_evt.position.tradeData.symbolId = 1  # Default
            # REMAINING volume (real cTrader semantics), not always 0: this
            # is what normalize() reads as MasterPositionClosed.remaining_volume
            # (engine/normalize.py), which domain/sizing.py:partial_close_volume
            # uses to compute each slave's proportional close -- a hardcoded
            # 0 here made every close look 100% regardless of req.volume.
            fill_evt.position.tradeData.volume = remaining_volume
            fill_evt.position.tradeData.tradeSide = model.ProtoOATradeSide.BUY  # Default
            fill_evt.position.positionStatus = (
                model.ProtoOAPositionStatus.POSITION_STATUS_CLOSED if is_full_close
                else model.ProtoOAPositionStatus.POSITION_STATUS_OPEN
            )
            fill_evt.position.swap = 0
            # normalize() keys its symbol lookup off order.tradeData.symbolId
            # for every execution event, closes included (see
            # tests/unit/test_normalize.py's base_event + closePositionDetail);
            # without this a close from the master account is silently
            # dropped (unknown symbol -> None) instead of replicating.
            # orderId=0 mirrors the deal.orderId convention above: no real
            # order backs a close.
            fill_evt.order.orderId = 0
            fill_evt.order.orderStatus = model.ProtoOAOrderStatus.ORDER_STATUS_FILLED
            fill_evt.order.orderType = model.ProtoOAOrderType.MARKET
            fill_evt.order.tradeData.symbolId = 1  # Default, varies by position
            fill_evt.order.tradeData.volume = req.volume
            fill_evt.order.tradeData.tradeSide = model.ProtoOATradeSide.BUY  # Default, varies
            self.broadcast(fill_evt)

            # Keep open_positions (see _handle_new_order_req) in sync: drop
            # the position on a full close, shrink its recorded volume on a
            # partial one -- otherwise a ProtoOAReconcileReq sent after this
            # close would keep reporting the position at its ORIGINAL
            # volume (or reporting it at all, once fully closed) forever.
            # Preserves the tracked entry's real symbol_id/trade_side/label
            # (set when it was opened) rather than this handler's own
            # symbol/side defaults above, which only stand in for fields
            # ProtoOAClosePositionReq never carries (a close references a
            # position_id, not a symbol or side).
            existing = next(
                (p for p in self.open_positions.get(req.ctidTraderAccountId, [])
                 if p["position_id"] == req.positionId),
                None,
            )
            remaining = [
                p for p in self.open_positions.get(req.ctidTraderAccountId, [])
                if p["position_id"] != req.positionId
            ]
            if not is_full_close:
                updated = dict(existing) if existing is not None else {
                    "position_id": req.positionId, "symbol_id": 1,
                    "trade_side": model.ProtoOATradeSide.BUY, "label": "",
                }
                updated["volume"] = remaining_volume
                remaining.append(updated)
            self.open_positions[req.ctidTraderAccountId] = remaining

    def _handle_amend_position_sltp_req(self, proto, msg):
        """Handle amend position SL/TP request - broadcasts untagged event.

        No synchronous tagged reply is sent (matches the real server); the
        outcome arrives only as an untagged broadcast ProtoOAExecutionEvent.
        """
        req = oa.ProtoOAAmendPositionSLTPReq()
        req.ParseFromString(msg.payload)

        # Record the trade request
        self.requests.append(req)

        if self._reject_if_not_authed(proto, req.ctidTraderAccountId):
            return

        response = oa.ProtoOAExecutionEvent()
        response.ctidTraderAccountId = req.ctidTraderAccountId
        response.executionType = model.ProtoOAExecutionType.ORDER_ACCEPTED
        response.position.positionId = req.positionId
        response.position.tradeData.symbolId = 1  # Default
        response.position.tradeData.volume = 0
        response.position.tradeData.tradeSide = model.ProtoOATradeSide.BUY
        response.position.positionStatus = model.ProtoOAPositionStatus.POSITION_STATUS_OPEN
        response.position.swap = 0
        if req.HasField("stopLoss"):
            response.position.stopLoss = req.stopLoss
        if req.HasField("takeProfit"):
            response.position.takeProfit = req.takeProfit
        # Real cTrader tags SL/TP amendments with an order of type
        # STOP_LOSS_TAKE_PROFIT carrying the position's symbol; without this
        # the copier's normalize() has no symbol to key off of and silently
        # drops the event (evt.order.tradeData.symbolId defaults to 0). The
        # other order.* fields below are REQUIRED by the protobuf schema once
        # the submessage is touched at all; there is no real order backing an
        # SL/TP-only amendment so orderId is 0, matching the close-position
        # convention elsewhere in this file.
        response.order.orderId = 0
        response.order.orderStatus = model.ProtoOAOrderStatus.ORDER_STATUS_ACCEPTED
        response.order.orderType = model.ProtoOAOrderType.STOP_LOSS_TAKE_PROFIT
        response.order.tradeData.symbolId = 1  # Default
        response.order.tradeData.volume = 0
        response.order.tradeData.tradeSide = model.ProtoOATradeSide.BUY
        self.broadcast(response)

    def _handle_amend_order_req(self, proto, msg):
        """Handle amend order request - broadcasts untagged event.

        No synchronous tagged reply is sent (matches the real server); the
        outcome arrives only as an untagged broadcast ProtoOAExecutionEvent.
        """
        req = oa.ProtoOAAmendOrderReq()
        req.ParseFromString(msg.payload)

        # Record the trade request
        self.requests.append(req)

        if self._reject_if_not_authed(proto, req.ctidTraderAccountId):
            return

        response = oa.ProtoOAExecutionEvent()
        response.ctidTraderAccountId = req.ctidTraderAccountId
        # Real cTrader reports a successful amend as ORDER_REPLACED (the type
        # normalize() keys a MasterPendingReplaced off of), not ORDER_ACCEPTED
        # (which is reserved for the order's initial placement).
        response.executionType = model.ProtoOAExecutionType.ORDER_REPLACED
        response.order.orderId = req.orderId
        response.order.orderStatus = model.ProtoOAOrderStatus.ORDER_STATUS_ACCEPTED
        response.order.orderType = model.ProtoOAOrderType.LIMIT  # Default
        response.order.tradeData.symbolId = 1  # Default
        # Echo the requested volume back rather than a fixed default, so
        # replicated-volume math (mirror_volume) exercises the caller's
        # actual amend, not an arbitrary constant.
        response.order.tradeData.volume = req.volume
        response.order.tradeData.tradeSide = model.ProtoOATradeSide.BUY
        if req.HasField("limitPrice"):
            response.order.limitPrice = req.limitPrice
        if req.HasField("stopPrice"):
            response.order.stopPrice = req.stopPrice
        self.broadcast(response)

    def _handle_cancel_order_req(self, proto, msg):
        """Handle cancel order request - broadcasts untagged event.

        No synchronous tagged reply is sent (matches the real server); the
        outcome arrives only as an untagged broadcast ProtoOAExecutionEvent.
        """
        req = oa.ProtoOACancelOrderReq()
        req.ParseFromString(msg.payload)

        # Record the trade request
        self.requests.append(req)

        if self._reject_if_not_authed(proto, req.ctidTraderAccountId):
            return

        response = oa.ProtoOAExecutionEvent()
        response.ctidTraderAccountId = req.ctidTraderAccountId
        response.executionType = model.ProtoOAExecutionType.ORDER_CANCELLED
        response.order.orderId = req.orderId
        response.order.orderStatus = model.ProtoOAOrderStatus.ORDER_STATUS_CANCELLED
        response.order.orderType = model.ProtoOAOrderType.LIMIT  # Default
        response.order.tradeData.symbolId = 1  # Default
        response.order.tradeData.volume = 100000  # Default
        response.order.tradeData.tradeSide = model.ProtoOATradeSide.BUY
        self.broadcast(response)

        if self.auto_fill:
            # A cancelled order STOPS BEING WORKING, so it must leave the
            # book the next reconcile reports -- exactly as a closed position
            # leaves open_positions in _handle_close_position_req above.
            # Without this the fake announced ORDER_CANCELLED and then kept
            # handing the order back forever, so nothing that VERIFIES a
            # cancellation against the broker could ever pass. The kill
            # switch now does verify, and this is the difference between the
            # double modelling a broker and modelling only the announcement.
            self.pending_orders[req.ctidTraderAccountId] = [
                o for o in self.pending_orders.get(req.ctidTraderAccountId, [])
                if o["order_id"] != req.orderId
            ]

    def _handle_heartbeat(self, proto, msg):
        """Handle heartbeat event."""
        self.heartbeats.append(time.time())
