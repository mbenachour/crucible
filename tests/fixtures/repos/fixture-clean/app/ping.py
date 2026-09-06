"""Deliberately correct counterpart to fixture-py/app/ping.py — no shell, arg list,
host validated against a strict pattern. Any upheld finding here is a false
positive (specs.md §12).
"""

import ipaddress
import subprocess


def ping(host: str) -> str:
    ipaddress.ip_address(host)  # raises ValueError on anything but a bare IP
    out = subprocess.run(
        ["ping", "-c", "1", "--", host], capture_output=True, text=True
    )
    return out.stdout


def handle_request(query: dict) -> str:
    return ping(query["host"])
