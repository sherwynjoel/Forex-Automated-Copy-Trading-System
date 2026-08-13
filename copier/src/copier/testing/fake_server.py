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
        self.open_positions: dict[int, list] = {}
        self.pending_orders: dict[int, list] = {}
        self.next_tokens: tuple[str, str] | None = None
        self.requests, self.app_auths, self.account_auths, self.heartbeats = (
            [],
            [],
            [],
            [],
        )
        self.protocols: list[_Proto] = []
        self._position_ids = itertools.count(5000)
        self._order_ids = itertools.count(9000)
        self._listening = None
        self._handlers: dict[int, Any] = {}
        self._setup_handlers()

    def _setup_handlers(self):
        """Setup message payload type handlers."""
        # Populate handler dict with payload types
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

    def listen(self, reactor) -> int:
        """Listen on a random port with TLS.

        Args:
            reactor: Twisted reactor

        Returns:
            int: The bound port number
        """
        factory = Factory.forProtocol(_Proto)
        factory.server = self
        self._listening = reactor.listenSSL(0, factory, make_self_signed_context())
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
        """Broadcast an event to all connected clients."""
        for p in self.protocols:
            p.send_payload(event)

    def push_execution(self, evt):
        """Push an execution event to all clients."""
        self.broadcast(evt)

    def push_spot(self, symbol_id: int, bid: int, ask: int):
        """Push a spot event to all clients."""
        spot = oa.ProtoOASpotEvent()
        spot.symbolId = symbol_id
        spot.bid = bid
        spot.ask = ask
        self.broadcast(spot)

    def handle(self, proto, msg):
        """Handle an incoming message.

        Args:
            proto: The protocol instance
            msg: The ProtoMessage
        """
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
        self.account_auths.append(req.ctidTraderAccountId)

        res = oa.ProtoOAAccountAuthRes()
        res.ctidTraderAccountId = req.ctidTraderAccountId
        proto.send_payload(res, msg.clientMsgId)

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

        # Map symbol_id to symbol details
        sym_map = {s["symbol_id"]: s for s in self.symbols}

        for sym_id in req.symbolId:
            if sym_id in sym_map:
                sym = sym_map[sym_id]
                full_sym = res.symbol.add()
                full_sym.symbolId = sym["symbol_id"]
                full_sym.digits = sym["digits"]
                full_sym.pipPosition = 4  # Standard pip position for 5-digit forex
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

        # Add open positions
        if req.ctidTraderAccountId in self.open_positions:
            for pos_data in self.open_positions[req.ctidTraderAccountId]:
                pos = res.position.add()
                pos.positionId = pos_data["position_id"]
                pos.tradeData.symbolId = pos_data["symbol_id"]
                pos.tradeData.volume = pos_data["volume"]
                pos.tradeData.tradeSide = pos_data["trade_side"]
                pos.price = pos_data.get("price", 0)

        # Add pending orders
        if req.ctidTraderAccountId in self.pending_orders:
            for ord_data in self.pending_orders[req.ctidTraderAccountId]:
                ord = res.order.add()
                ord.orderId = ord_data["order_id"]
                ord.tradeData.symbolId = ord_data["symbol_id"]
                ord.tradeData.volume = ord_data["volume"]
                ord.tradeData.tradeSide = ord_data["trade_side"]
                ord.orderType = ord_data["order_type"]

        proto.send_payload(res, msg.clientMsgId)

    def _handle_get_account_list_req(self, proto, msg):
        """Handle get account list request."""
        req = oa.ProtoOAGetAccountListByAccessTokenReq()
        req.ParseFromString(msg.payload)

        res = oa.ProtoOAGetAccountListByAccessTokenRes()
        for account_id in self.accounts:
            acc = res.ctidTraderAccount.add()
            acc.ctidTraderAccountId = account_id
            acc.isLimitedRisk = False

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

        proto.send_payload(res, msg.clientMsgId)

    def _handle_subscribe_spots_req(self, proto, msg):
        """Handle subscribe spots request."""
        req = oa.ProtoOASubscribeSpotsReq()
        req.ParseFromString(msg.payload)

        res = oa.ProtoOASubscribeSpotsRes()
        res.ctidTraderAccountId = req.ctidTraderAccountId

        proto.send_payload(res, msg.clientMsgId)

    def _handle_trader_req(self, proto, msg):
        """Handle trader request."""
        req = oa.ProtoOATraderReq()
        req.ParseFromString(msg.payload)

        res = oa.ProtoOATraderRes()
        res.ctidTraderAccountId = req.ctidTraderAccountId
        res.trader.ctidTraderAccountId = req.ctidTraderAccountId

        # Set balance if available
        if req.ctidTraderAccountId in self.balances:
            res.trader.balance = self.balances[req.ctidTraderAccountId]
        else:
            res.trader.balance = 100000  # Default balance

        proto.send_payload(res, msg.clientMsgId)

    def _handle_new_order_req(self, proto, msg):
        """Handle new order request."""
        req = oa.ProtoOANewOrderReq()
        req.ParseFromString(msg.payload)

        # Record the trade request
        self.requests.append(req)

        # Send response (OK - order accepted, or error)
        res = oa.ProtoOAExecutionEvent()
        res.ctidTraderAccountId = req.ctidTraderAccountId

        if self.auto_fill:
            # For MARKET orders: accept then fill
            if req.orderType == model.ProtoOAOrderType.MARKET:
                # Send ORDER_ACCEPTED event
                accept_evt = oa.ProtoOAExecutionEvent()
                accept_evt.ctidTraderAccountId = req.ctidTraderAccountId
                accept_evt.executionType = model.ProtoOAExecutionType.ORDER_ACCEPTED
                accept_evt.order.orderId = next(self._order_ids)
                accept_evt.order.tradeData.symbolId = req.tradeData.symbolId
                accept_evt.order.tradeData.volume = req.tradeData.volume
                accept_evt.order.tradeData.tradeSide = req.tradeData.tradeSide
                accept_evt.order.tradeData.label = req.tradeData.label
                accept_evt.order.orderType = req.orderType
                accept_evt.order.clientOrderId = req.clientOrderId
                self.broadcast(accept_evt)

                # Send ORDER_FILLED event
                fill_evt = oa.ProtoOAExecutionEvent()
                fill_evt.ctidTraderAccountId = req.ctidTraderAccountId
                fill_evt.executionType = model.ProtoOAExecutionType.ORDER_FILLED
                fill_evt.deal.filledVolume = req.tradeData.volume
                fill_evt.position.positionId = next(self._position_ids)
                fill_evt.position.tradeData.symbolId = req.tradeData.symbolId
                fill_evt.position.tradeData.volume = req.tradeData.volume
                fill_evt.position.tradeData.tradeSide = req.tradeData.tradeSide
                fill_evt.order.orderId = accept_evt.order.orderId
                fill_evt.order.clientOrderId = req.clientOrderId
                fill_evt.order.tradeData.label = req.tradeData.label
                self.broadcast(fill_evt)
            else:
                # For LIMIT/STOP orders: just accept
                accept_evt = oa.ProtoOAExecutionEvent()
                accept_evt.ctidTraderAccountId = req.ctidTraderAccountId
                accept_evt.executionType = model.ProtoOAExecutionType.ORDER_ACCEPTED
                accept_evt.order.orderId = next(self._order_ids)
                accept_evt.order.tradeData.symbolId = req.tradeData.symbolId
                accept_evt.order.tradeData.volume = req.tradeData.volume
                accept_evt.order.tradeData.tradeSide = req.tradeData.tradeSide
                accept_evt.order.tradeData.label = req.tradeData.label
                accept_evt.order.orderType = req.orderType
                accept_evt.order.clientOrderId = req.clientOrderId
                self.broadcast(accept_evt)

    def _handle_close_position_req(self, proto, msg):
        """Handle close position request."""
        req = oa.ProtoOAClosePositionReq()
        req.ParseFromString(msg.payload)

        # Record the trade request
        self.requests.append(req)

        if self.auto_fill:
            # Send ORDER_FILLED event with closePositionDetail
            fill_evt = oa.ProtoOAExecutionEvent()
            fill_evt.ctidTraderAccountId = req.ctidTraderAccountId
            fill_evt.executionType = model.ProtoOAExecutionType.ORDER_FILLED
            fill_evt.deal.positionId = req.positionId
            fill_evt.deal.filledVolume = req.volume
            fill_evt.deal.closePositionDetail = req.volume
            fill_evt.position.positionId = req.positionId
            fill_evt.position.tradeData.volume = 0  # Remaining volume is 0
            self.broadcast(fill_evt)

    def _handle_amend_position_sltp_req(self, proto, msg):
        """Handle amend position SL/TP request."""
        req = oa.ProtoOAAmendPositionSLTPReq()
        req.ParseFromString(msg.payload)

        # Record the trade request
        self.requests.append(req)

        # Send OK response
        res = oa.ProtoOAExecutionEvent()
        res.ctidTraderAccountId = req.ctidTraderAccountId
        res.position.positionId = req.positionId
        if req.hasValue_stopLoss:
            res.position.stopLoss = req.stopLoss
        if req.hasValue_takeProfit:
            res.position.takeProfit = req.takeProfit
        self.broadcast(res)

    def _handle_amend_order_req(self, proto, msg):
        """Handle amend order request."""
        req = oa.ProtoOAAmendOrderReq()
        req.ParseFromString(msg.payload)

        # Record the trade request
        self.requests.append(req)

        # Send OK response
        res = oa.ProtoOAExecutionEvent()
        res.ctidTraderAccountId = req.ctidTraderAccountId
        res.order.orderId = req.orderId
        if req.hasValue_limitPrice:
            res.order.limitPrice = req.limitPrice
        if req.hasValue_stopPrice:
            res.order.stopPrice = req.stopPrice
        self.broadcast(res)

    def _handle_cancel_order_req(self, proto, msg):
        """Handle cancel order request."""
        req = oa.ProtoOACancelOrderReq()
        req.ParseFromString(msg.payload)

        # Record the trade request
        self.requests.append(req)

        # Send OK response
        res = oa.ProtoOAExecutionEvent()
        res.ctidTraderAccountId = req.ctidTraderAccountId
        res.order.orderId = req.orderId
        self.broadcast(res)

    def _handle_heartbeat(self, proto, msg):
        """Handle heartbeat event."""
        self.heartbeats.append(time.time())
