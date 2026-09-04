"""IP → ASN via Team Cymru's DNS interface.

Why operators and not a netmask: Russian carriers spread one subscriber pool over
many /16s. MTS hands out both 91.78.x and 91.79.x, Rostelecom both 85.112.x and
80.234.x — same operator, two networks each. Counting /16 therefore punishes a
single person on mobile, whose address changes on every reconnect, exactly as if
they were sharing the account. Counting operators does not.

The lookup is a plain DNS TXT query built on a UDP socket — no extra package in
the image and no subprocess per address. Results are cached for a day; a failure
degrades to the address's /16 rather than dropping it, because an unknown network
still has to count as something.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import random
import socket
import struct
import time

log = logging.getLogger("remna.asn")

_TTL = 24 * 3600.0
_NEG_TTL = 600.0
_TIMEOUT = 2.0
_MAX_ENTRIES = 20000
_cache: dict[str, tuple[float, str]] = {}
_resolvers: list[str] | None = None


def _slash16(ip: str) -> str:
    parts = ip.split(".")
    return f"net:{parts[0]}.{parts[1]}" if len(parts) == 4 else f"ip:{ip}"


def _nameservers() -> list[str]:
    global _resolvers
    if _resolvers is not None:
        return _resolvers
    found: list[str] = []
    try:
        with open("/etc/resolv.conf", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("nameserver"):
                    parts = line.split()
                    if len(parts) > 1 and ":" not in parts[1]:
                        found.append(parts[1])
    except OSError:
        pass
    for fallback in ("1.1.1.1", "8.8.8.8"):
        if fallback not in found:
            found.append(fallback)
    _resolvers = found[:3]
    return _resolvers


def _encode_name(name: str) -> bytes:
    out = b""
    for label in name.split("."):
        if label:
            out += bytes([len(label)]) + label.encode("ascii")
    return out + b"\x00"


def _skip_name(buf: bytes, pos: int) -> int:
    while pos < len(buf):
        ln = buf[pos]
        if ln == 0:
            return pos + 1
        if ln & 0xC0 == 0xC0:  # compression pointer ends the name
            return pos + 2
        pos += 1 + ln
    return pos


def _txt_query(name: str) -> str | None:
    qid = random.randint(0, 0xFFFF)
    header = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0)
    packet = header + _encode_name(name) + struct.pack(">HH", 16, 1)  # TXT, IN
    for server in _nameservers():
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(_TIMEOUT)
                sock.sendto(packet, (server, 53))
                data, _ = sock.recvfrom(2048)
        except Exception:
            continue
        if len(data) < 12 or struct.unpack(">H", data[:2])[0] != qid:
            continue
        ancount = struct.unpack(">H", data[6:8])[0]
        if ancount == 0:
            return None
        pos = _skip_name(data, 12) + 4  # question name + qtype/qclass
        for _ in range(ancount):
            pos = _skip_name(data, pos)
            if pos + 10 > len(data):
                return None
            rtype, _cls, _ttl, rdlen = struct.unpack(">HHIH", data[pos:pos + 10])
            pos += 10
            rdata = data[pos:pos + rdlen]
            pos += rdlen
            if rtype != 16 or not rdata:
                continue
            text = rdata[1:1 + rdata[0]].decode("ascii", "replace")
            return text
    return None


def _query(ip: str) -> str | None:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return None
    if addr.version != 4 or addr.is_private or addr.is_loopback:
        return None
    rev = ".".join(reversed(ip.split(".")))
    text = _txt_query(f"{rev}.origin.asn.cymru.com")
    if not text or "|" not in text:
        return None
    # "8359 | 91.78.72.0/21 | RU | ripencc | 2006-08-21"
    head = text.split("|")[0].strip().split()
    return f"as:{head[0]}" if head and head[0].isdigit() else None


def network_of(ip: str) -> str:
    """Stable grouping key for an address: its operator when known, else its /16."""
    now = time.time()
    hit = _cache.get(ip)
    if hit and now - hit[0] < (_TTL if hit[1].startswith("as:") else _NEG_TTL):
        return hit[1]
    key = _query(ip) or _slash16(ip)
    if len(_cache) > _MAX_ENTRIES:
        _cache.clear()
    _cache[ip] = (now, key)
    return key


def networks_of(ips) -> set[str]:
    return {network_of(ip) for ip in ips if ip}


def cache_size() -> int:
    return len(_cache)
