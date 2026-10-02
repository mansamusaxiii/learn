"""Minimal OSC 1.0 encoder/decoder over UDP (no dependencies)."""

from __future__ import annotations

import socket
import struct
import threading
import time
from collections import deque


def _pad(b: bytes) -> bytes:
    return b + b"\0" * (4 - len(b) % 4)


def _osc_string(s: str) -> bytes:
    return _pad(s.encode("utf-8"))


def encode_message(address: str, *args) -> bytes:
    if not address.startswith("/"):
        raise ValueError(f"OSC address must start with '/': {address!r}")
    tags, payload = ",", b""
    for a in args:
        if isinstance(a, bool):
            tags += "i"
            payload += struct.pack(">i", int(a))
        elif isinstance(a, int):
            tags += "i"
            payload += struct.pack(">i", a)
        elif isinstance(a, float):
            tags += "f"
            payload += struct.pack(">f", a)
        elif isinstance(a, str):
            tags += "s"
            payload += _osc_string(a)
        else:
            raise TypeError(f"Unsupported OSC argument type: {type(a).__name__}")
    return _osc_string(address) + _osc_string(tags) + payload


def _read_string(data: bytes, i: int) -> tuple[str, int]:
    end = data.index(b"\0", i)
    s = data[i:end].decode("utf-8", "replace")
    return s, (end + 4) & ~3


def decode_packet(data: bytes) -> list[tuple[str, list]]:
    """Decode a message or bundle into [(address, args), ...]."""
    if data.startswith(b"#bundle\0"):
        out, i = [], 16
        while i + 4 <= len(data):
            (size,) = struct.unpack(">i", data[i:i + 4])
            out += decode_packet(data[i + 4:i + 4 + size])
            i += 4 + size
        return out
    address, i = _read_string(data, 0)
    if i >= len(data):
        return [(address, [])]
    tags, i = _read_string(data, i)
    args: list = []
    for t in tags[1:]:
        if t == "i":
            args.append(struct.unpack(">i", data[i:i + 4])[0]); i += 4
        elif t == "f":
            args.append(round(struct.unpack(">f", data[i:i + 4])[0], 4)); i += 4
        elif t == "d":
            args.append(struct.unpack(">d", data[i:i + 8])[0]); i += 8
        elif t == "h":
            args.append(struct.unpack(">q", data[i:i + 8])[0]); i += 8
        elif t == "s":
            s, i = _read_string(data, i); args.append(s)
        elif t == "T":
            args.append(True)
        elif t == "F":
            args.append(False)
        elif t == "b":
            (n,) = struct.unpack(">i", data[i:i + 4]); i = (i + 4 + n + 3) & ~3
            args.append(f"<blob {n} bytes>")
    return [(address, args)]


def parse_hosts(spec: str) -> list[tuple[str, int]]:
    """'10.0.0.11:5000,10.0.0.12' -> [('10.0.0.11', 5000), ('10.0.0.12', 5000)]"""
    hosts = []
    for part in filter(None, (p.strip() for p in spec.split(","))):
        host, _, port = part.rpartition(":") if ":" in part else (part, "", "5000")
        hosts.append((host, int(port or 5000)))
    return hosts


class OscSender:
    def __init__(self, hosts: list[tuple[str, int]]):
        self.hosts = hosts
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def send(self, address: str, *args) -> list[str]:
        packet = encode_message(address, *args)
        sent = []
        for host, port in self.hosts:
            self.sock.sendto(packet, (host, port))
            sent.append(f"{host}:{port}")
        return sent


class FeedbackListener:
    """Collects OSC feedback that Millumin sends out (set its OSC output to this machine/port)."""

    def __init__(self, port: int, keep: int = 200):
        self.port = port
        self.messages: deque = deque(maxlen=keep)
        self.state: dict[str, tuple[float, list]] = {}
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("0.0.0.0", port))
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        while True:
            data, (host, _) = self.sock.recvfrom(65535)
            try:
                for address, args in decode_packet(data):
                    now = time.time()
                    self.messages.append((now, host, address, args))
                    self.state[address] = (now, args)
            except (ValueError, struct.error, IndexError):
                continue
