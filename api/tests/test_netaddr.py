"""client_ip: the address a machine door judges. X-Forwarded-For is
believed only when TRUST_PROXY is on AND the socket peer is one of our own
reverse proxy's possible addresses -- loopback, the container's default
gateway, or an entry in TRUSTED_PROXY_IPS. Shared by the TradingView webhook
and the MT5 terminal door."""
from starlette.requests import Request

from api import netaddr
from api.config import ApiConfig

PROD_PROC_NET_ROUTE = (
    "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n"
    "eth0\t00000000\t010012AC\t0003\t0\t0\t0\t00000000\t0\t0\t0\n"
    "eth0\t000012AC\t00000000\t0001\t0\t0\t0\t0000FFFF\t0\t0\t0\n"
)


def _cfg(trust_proxy: bool) -> ApiConfig:
    return ApiConfig(
        postgres_dsn="", session_secret="s", bootstrap_admin_email="",
        bootstrap_admin_password="", copier_control_url="", cookie_secure=False,
        ctrader_client_id="", ctrader_client_secret="", ctrader_redirect_uri="",
        ctrader_auth_url="", ctrader_token_url="", fernet_key="",
        trust_proxy=trust_proxy, public_origin="", registration_enabled=False)


def _request(peer: str, forwarded: str | None = None) -> Request:
    headers = [] if forwarded is None else [(b"x-forwarded-for", forwarded.encode())]
    return Request({"type": "http", "method": "POST", "path": "/", "scheme": "http",
                    "query_string": b"", "headers": headers,
                    "client": (peer, 40000), "server": ("testserver", 80)})


def test_gateway_is_read_from_proc_net_route():
    """Exactly what the production container reports; 010012AC is
    172.18.0.1 in little-endian hex."""
    assert netaddr._gateway_from_proc_route(PROD_PROC_NET_ROUTE) == "172.18.0.1"
    no_default = ("Iface\tDestination\tGateway\n"
                  "eth0\t000012AC\t00000000\t0001\t0\t0\t0\t0000FFFF\t0\t0\t0\n")
    assert netaddr._gateway_from_proc_route(no_default) is None
    assert netaddr._gateway_from_proc_route("") is None


def test_the_forwarded_address_is_believed_only_from_a_trusted_peer(monkeypatch):
    monkeypatch.setattr(netaddr, "_container_gateway", lambda: "172.18.0.1")
    monkeypatch.delenv("TRUSTED_PROXY_IPS", raising=False)
    cfg = _cfg(trust_proxy=True)

    assert netaddr.client_ip(_request("172.18.0.1", "52.89.214.238"), cfg) == "52.89.214.238"
    assert netaddr.client_ip(_request("127.0.0.1", "52.89.214.238"), cfg) == "52.89.214.238"
    # a client that arrived already carrying the header gains nothing: last hop wins
    assert netaddr.client_ip(_request("172.18.0.1", "52.89.214.238, 203.0.113.9"), cfg) == "203.0.113.9"
    # whoever opens a socket to :8000 directly does not get to say who they are
    assert netaddr.client_ip(_request("10.9.8.7", "52.89.214.238"), cfg) == "10.9.8.7"
    # no header: the peer
    assert netaddr.client_ip(_request("172.18.0.1"), cfg) == "172.18.0.1"


def test_without_trust_proxy_the_header_is_never_read(monkeypatch):
    monkeypatch.setattr(netaddr, "_container_gateway", lambda: "172.18.0.1")
    cfg = _cfg(trust_proxy=False)
    assert netaddr.client_ip(_request("172.18.0.1", "52.89.214.238"), cfg) == "172.18.0.1"
    assert netaddr.client_ip(_request("127.0.0.1", "52.89.214.238"), cfg) == "127.0.0.1"


def test_trusted_proxy_ips_names_any_other_proxy_by_address_or_cidr(monkeypatch):
    monkeypatch.setattr(netaddr, "_container_gateway", lambda: None)
    monkeypatch.setenv("TRUSTED_PROXY_IPS", "10.0.0.0/24, 192.0.2.7, not-an-address")
    cfg = _cfg(trust_proxy=True)
    assert netaddr.client_ip(_request("10.0.0.5", "52.89.214.238"), cfg) == "52.89.214.238"
    assert netaddr.client_ip(_request("192.0.2.7", "52.89.214.238"), cfg) == "52.89.214.238"
    assert netaddr.client_ip(_request("10.0.1.5", "52.89.214.238"), cfg) == "10.0.1.5"
    assert netaddr._is_trusted_proxy("garbage") is False
