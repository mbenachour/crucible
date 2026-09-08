"""Hermetic fixture repos for the Recon suite (issue #34 / #30).

Every fixture is a tiny synthetic repo written into a tmp dir — no vendored
trees, no network, no installs. Session-scoped so `build_seed` runs once each.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(body).lstrip("\n"))
    return root


@pytest.fixture(scope="session")
def repo_web(tmp_path_factory) -> Path:
    """Python Flask web-api: two subsystems (api/, core/), a framework entry
    point with a nearby eval() sink, a raw-SQL helper, and an auth decorator."""
    root = tmp_path_factory.mktemp("repo_web")
    return _write(root, {
        "requirements.txt": "flask==3.0.0\nPyYAML\n",
        "README.md": "# demo web api\n\nBuild: pip install -r requirements.txt\n",
        "api/__init__.py": "",
        "api/views.py": '''
            from flask import Flask, request
            from core.db import lookup

            app = Flask(__name__)

            def login_required(fn):
                return fn

            @app.route("/search", methods=["GET"])
            @login_required
            def search():
                q = request.args.get("q", "")
                # deliberately unsafe: dynamic evaluation of a request param
                expr = "results_for(" + q + ")"
                return str(eval(expr))

            @app.route("/user/<uid>")
            def user(uid):
                return lookup(uid)
        ''',
        "core/__init__.py": "",
        "core/db.py": '''
            import sqlite3

            _conn = sqlite3.connect(":memory:")

            def lookup(uid):
                # raw string-formatted SQL — a sink core/ owns
                cur = _conn.execute("SELECT * FROM users WHERE id = '%s'" % uid)
                return str(cur.fetchall())
        ''',
        "core/util.py": '''
            def slugify(s):
                return "-".join(s.lower().split())
        ''',
    })


@pytest.fixture(scope="session")
def repo_clean(tmp_path_factory) -> Path:
    """Python library with no entry points the static seed recognises."""
    root = tmp_path_factory.mktemp("repo_clean")
    return _write(root, {
        "pyproject.toml": '[project]\nname = "cleanlib"\nversion = "0.1.0"\n',
        "cleanlib/__init__.py": "",
        "cleanlib/math_utils.py": '''
            def add(a, b):
                return a + b

            def mean(xs):
                return sum(xs) / len(xs) if xs else 0.0
        ''',
        "cleanlib/strings.py": '''
            def shout(s):
                return s.upper() + "!"
        ''',
    })


@pytest.fixture(scope="session")
def repo_native(tmp_path_factory) -> Path:
    """C native repo — a socket read into a fixed buffer (memory-unsafe)."""
    root = tmp_path_factory.mktemp("repo_native")
    return _write(root, {
        "Makefile": "all:\n\tcc -o app src/main.c src/parse.c\n",
        "src/main.c": '''
            #include <stdio.h>
            int handle(const char *buf, int n);
            int main(int argc, char **argv) {
                char line[64];
                fgets(line, sizeof line, stdin);
                return handle(line, 64);
            }
        ''',
        "src/parse.c": '''
            #include <string.h>
            #include <sys/socket.h>
            int handle(const char *buf, int n) {
                char dst[16];
                int len = buf[0];          /* attacker-controlled length */
                memcpy(dst, buf + 1, len); /* OOB write */
                return dst[0];
            }
            int netread(int fd) {
                char b[128];
                return recv(fd, b, 4096, 0); /* OOB read */
            }
        ''',
    })


@pytest.fixture(scope="session")
def repo_mobile(tmp_path_factory) -> Path:
    """React-Native app: a deep-link handler and a WebView screen in different
    subsystems, plus an API client. Drives repo_kind=mobile and a deep-link ->
    WebView data flow."""
    root = tmp_path_factory.mktemp("repo_mobile")
    return _write(root, {
        "package.json": '''
            {
              "name": "rn-demo",
              "version": "0.1.0",
              "scripts": {"start": "react-native start", "test": "jest"},
              "dependencies": {
                "react": "18.2.0",
                "react-native": "0.73.0",
                "react-native-webview": "13.0.0"
              }
            }
        ''',
        "android/app/src/main/AndroidManifest.xml":
            '<manifest><application><activity android:name=".MainActivity">'
            '<intent-filter><data android:scheme="rndemo"/></intent-filter>'
            '</activity></application></manifest>\n',
        "App/linking/DeepLink.js": '''
            import { Linking } from "react-native";
            import { openInWebView } from "../webview/WebViewScreen";

            export async function boot() {
              const url = await Linking.getInitialURL();
              Linking.addEventListener("url", ({ url }) => route(url));
              if (url) route(url);
            }

            function route(url) {
              const target = url.split("?next=")[1];
              // deep-link param flows straight into the WebView source
              openInWebView(target);
            }
        ''',
        "App/webview/WebViewScreen.js": '''
            import React from "react";
            import { WebView } from "react-native-webview";

            let _uri = "about:blank";
            export function openInWebView(uri) { _uri = uri; }

            export default function WebViewScreen() {
              return <WebView source={{ uri: _uri }} />;
            }
        ''',
        "App/api/Client.js": '''
            export async function get(path) {
              const res = await fetch("https://api.example.com" + path);
              return res.json();
            }
        ''',
    })
