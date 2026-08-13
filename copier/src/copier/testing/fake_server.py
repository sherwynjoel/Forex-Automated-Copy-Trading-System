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
        self.reject_next_order: bool = False  # Scriptable rejection
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
        """Listen on a random port with TLS."""
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
        """Broadcast an event to all connected clients (untagged, no clientMsgId)."""
        for p in self.protocols:
            p.send_payload(event, client_msg_id="")

    def push_execution(self, evt):
        """Push an execution event to all clients."""
        self.broadcast(evt)

    def push_spot(self, ctid_trader_account_id: int, symbol_id: int, bid: int, ask: int):
        """Push a spot event to all clients."""
        spot = oa.ProtoOASpotEvent()
        spot.ctidTraderAccountId = ctid_trader_account_id
        spot.symbolId = symbol_id
        spot.bid = bid
        spot.ask = ask
        self.broadcast(spot)

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

    def _handle_trader_req(self, proto, msg):
        """Handle trader request."""
        req = oa.ProtoOATraderReq()
        req.ParseFromString(msg.payload)

        res = oa.ProtoOATraderRes()
        res.ctidTraderAccountId = req.ctidTraderAccountId
        res.trader.ctidTraderAccountId = req.ctidTraderAccountId

        if req.ctidTraderAccountId in self.balances:
            res.trader.balance = self.balances[req.ctidTraderAccountId]
        else:
            res.trader.balance = 100000

        res.trader.depositAssetId = 1

        proto.send_payload(res, msg.clientMsgId)

    def _handle_new_order_req(self, proto, msg):
        """Handle new order request - sends response + broadcasts untagged events."""
        req = oa.ProtoOANewOrderReq()
        req.ParseFromString(msg.payload)

        # Record the trade request
        self.requests.append(req)

        # Send minimal tagged response to resolve SDK.send() deferred
        response = oa.ProtoOAExecutionEvent()
        response.ctidTraderAccountId = req.ctidTraderAccountId
        response.executionType = model.ProtoOAExecutionType.ORDER_ACCEPTED
        proto.send_payload(response, msg.clientMsgId)

        # Check if we should reject this order
        if self.reject_next_order:
            self.reject_next_order = False
            reject_evt = oa.ProtoOAExecutionEvent()
            reject_evt.ctidTraderAccountId = req.ctidTraderAccountId
            reject_evt.executionType = model.ProtoOAExecutionType.ORDER_REJECTED
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

                # Broadcast ORDER_FILLED event (untagged)
                position_id = next(self._position_ids)
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
                fill_evt.position.positionId = position_id
                fill_evt.position.tradeData.symbolId = req.symbolId
                fill_evt.position.tradeData.volume = req.volume
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
                self.broadcast(accept_evt)

    def _handle_close_position_req(self, proto, msg):
        """Handle close position request - sends response + broadcasts untagged event."""
        req = oa.ProtoOAClosePositionReq()
        req.ParseFromString(msg.payload)

        # Record the trade request
        self.requests.append(req)

        # Send minimal tagged response
        response = oa.ProtoOAExecutionEvent()
        response.ctidTraderAccountId = req.ctidTraderAccountId
        response.executionType = model.ProtoOAExecutionType.ORDER_ACCEPTED
        proto.send_payload(response, msg.clientMsgId)

        if self.auto_fill:
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
            fill_evt.deal.closePositionDetail.closedVolume = req.volume
            fill_evt.deal.closePositionDetail.entryPrice = 10000  # Plausible default
            fill_evt.deal.closePositionDetail.grossProfit = 1000  # Plausible default
            fill_evt.deal.closePositionDetail.swap = 0
            fill_evt.deal.closePositionDetail.commission = 5  # Plausible default
            fill_evt.deal.closePositionDetail.balance = 100000  # Plausible default
            fill_evt.position.positionId = req.positionId
            fill_evt.position.tradeData.symbolId = 1  # Default
            fill_evt.position.tradeData.volume = 0  # Closed
            fill_evt.position.tradeData.tradeSide = model.ProtoOATradeSide.BUY  # Default
            fill_evt.position.positionStatus = model.ProtoOAPositionStatus.POSITION_STATUS_CLOSED
            fill_evt.position.swap = 0
            self.broadcast(fill_evt)

    def _handle_amend_position_sltp_req(self, proto, msg):
        """Handle amend position SL/TP request - broadcasts untagged event."""
        req = oa.ProtoOAAmendPositionSLTPReq()
        req.ParseFromString(msg.payload)

        # Record the trade request
        self.requests.append(req)

        # Send tagged response to resolve SDK.send()
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
        proto.send_payload(response, msg.clientMsgId)

    def _handle_amend_order_req(self, proto, msg):
        """Handle amend order request - broadcasts untagged event."""
        req = oa.ProtoOAAmendOrderReq()
        req.ParseFromString(msg.payload)

        # Record the trade request
        self.requests.append(req)

        # Send tagged response to resolve SDK.send()
        response = oa.ProtoOAExecutionEvent()
        response.ctidTraderAccountId = req.ctidTraderAccountId
        response.executionType = model.ProtoOAExecutionType.ORDER_ACCEPTED
        response.order.orderId = req.orderId
        response.order.orderStatus = model.ProtoOAOrderStatus.ORDER_STATUS_ACCEPTED
        response.order.orderType = model.ProtoOAOrderType.LIMIT  # Default
        response.order.tradeData.symbolId = 1  # Default
        response.order.tradeData.volume = 100000  # Default
        response.order.tradeData.tradeSide = model.ProtoOATradeSide.BUY
        if req.HasField("limitPrice"):
            response.order.limitPrice = req.limitPrice
        if req.HasField("stopPrice"):
            response.order.stopPrice = req.stopPrice
        proto.send_payload(response, msg.clientMsgId)

    def _handle_cancel_order_req(self, proto, msg):
        """Handle cancel order request - broadcasts untagged event."""
        req = oa.ProtoOACancelOrderReq()
        req.ParseFromString(msg.payload)

        # Record the trade request
        self.requests.append(req)

        # Send tagged response to resolve SDK.send()
        response = oa.ProtoOAExecutionEvent()
        response.ctidTraderAccountId = req.ctidTraderAccountId
        response.executionType = model.ProtoOAExecutionType.ORDER_CANCELLED
        response.order.orderId = req.orderId
        response.order.orderStatus = model.ProtoOAOrderStatus.ORDER_STATUS_CANCELLED
        response.order.orderType = model.ProtoOAOrderType.LIMIT  # Default
        response.order.tradeData.symbolId = 1  # Default
        response.order.tradeData.volume = 100000  # Default
        response.order.tradeData.tradeSide = model.ProtoOATradeSide.BUY
        proto.send_payload(response, msg.clientMsgId)

    def _handle_heartbeat(self, proto, msg):
        """Handle heartbeat event."""
        self.heartbeats.append(time.time())
