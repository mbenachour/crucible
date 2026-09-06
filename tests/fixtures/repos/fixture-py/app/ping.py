"""Planted bug py-cmdi-1: command injection via shell=True string concat."""

import subprocess


def ping(host: str) -> str:
    # BUG(py-cmdi-1): `host` comes straight from an HTTP query param.
    cmd = "ping -c 1 " + host
    out = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return out.stdout


def handle_request(query: dict) -> str:
    return ping(query["host"])
