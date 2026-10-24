"""Shared application objects (created once at startup, see backend/main.py)."""
from __future__ import annotations

import socket
import time
from dataclasses import dataclass, field

from fastapi import Request


@dataclass
class AppContext:
    settings: object
    db: object
    ws: object
    events: object
    engine: object | None
    metrics: object
    video: dict = field(default_factory=lambda: {"bytes": 0, "frames": 0, "active": 0,
                                                 "since": time.time()})


def get_ctx(request: Request) -> AppContext:
    return request.app.state.ctx


def lan_addresses() -> list[str]:
    """IPv4 addresses of this laptop on the local network (works without Internet)."""
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except socket.gaierror:
        pass
    try:  # UDP 'connect' sends no packet; it just asks the OS which interface it would use
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.168.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith("127."))
