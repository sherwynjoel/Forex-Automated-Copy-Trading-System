"""Which address a machine door judges.

Shared by the TradingView webhook (routes/webhooks.py) and the MT5 terminal
door (routes/mt5.py): both are sessionless, both key a per-source rate bucket
on the caller's address, and the webhook allowlists it. auth.get_client_ip
honours X-Forwarded-For whenever TRUST_PROXY is set; these doors are
stricter and believe it only when the immediate socket peer is one of our
own reverse proxy's possible addresses.

Only what is genuinely a proxy may set X-Forwarded-For for these routes.
Three kinds of peer qualify, and the middle one is the shipped topology:

  loopback              Caddy talking to a bare uvicorn on the same host.
  the container's       Caddy on the host -> 127.0.0.1:8000 (published
  default gateway       loopback-only) -> docker-proxy -> us. Every such
                        connection reaches the container FROM THE BRIDGE
                        GATEWAY, never from loopback. The first live alert
                        was refused as "non-TradingView source 172.18.0.1"
                        for exactly this reason. Trusting the gateway is
                        the same trust as loopback ONLY because the port is
                        bound to 127.0.0.1 on the host; publish it wider
                        and every LAN peer arrives the same way.
  TRUSTED_PROXY_IPS     anything else, by address or CIDR -- Caddy running
                        inside the compose network, say.
"""
from __future__ import annotations

import functools
import ipaddress
import logging
import os
import socket
import struct
from typing import Optional

from fastapi import Request

from .auth import get_client_ip
from .config import ApiConfig

logger = logging.getLogger(__name__)

_LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})


def _gateway_from_proc_route(text: str) -> Optional[str]:
    """The default gateway out of /proc/net/route: the row whose Destination
    is 00000000, with the gateway as little-endian hex (010012AC is
    172.18.0.1). None when there is no default route or the file is not in
    the expected shape."""
    for line in text.splitlines()[1:]:
        fields = line.split()
        if len(fields) >= 3 and fields[1] == "00000000":
            try:
                return socket.inet_ntoa(struct.pack("<L", int(fields[2], 16)))
            except (ValueError, struct.error):
                return None
    return None


@functools.lru_cache(maxsize=1)
def _container_gateway() -> Optional[str]:
    """The address host-originated connections arrive from when we run in a
    container. Read once; a container's default route does not change."""
    try:
        with open("/proc/net/route", encoding="ascii") as f:
            return _gateway_from_proc_route(f.read())
    except OSError:
        return None


def _is_trusted_proxy(peer: str) -> bool:
    if peer in _LOOPBACK or peer == _container_gateway():
        return True
    try:
        addr = ipaddress.ip_address(peer)
    except ValueError:
        return False
    for item in os.environ.get("TRUSTED_PROXY_IPS", "").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            if addr in ipaddress.ip_network(item, strict=False):
                return True
        except ValueError:
            logger.warning("TRUSTED_PROXY_IPS entry %r is not an address or CIDR", item)
    return False


def client_ip(request: Request, cfg: ApiConfig) -> str:
    """The address a machine door judges.

    X-Forwarded-For is believed ONLY when TRUST_PROXY is set AND the
    immediate peer is one of our own reverse proxy's possible addresses
    (see _is_trusted_proxy). The review showed that honouring it from any
    peer turns an allowlist into a header check: anyone reaching :8000
    directly could claim to be TradingView. Of the header's hops the LAST is
    used -- the one Caddy appended -- so a client that arrives already
    carrying the header gains nothing.
    """
    peer = request.client.host if request.client else "unknown"
    if cfg.trust_proxy and _is_trusted_proxy(peer):
        return get_client_ip(request, trust_proxy=True)
    return peer
