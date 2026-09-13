import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { auth } from "../api/client";
import { useHealth } from "../api/hooks";
import { useTheme } from "../lib/theme";

/**
 * Minimal settings page: just the Connection + Appearance controls that used
 * to live in the top bar (issue #71). This is the landing spot issue #72
 * will expand into a full settings page — a Models section is coming later,
 * once #73 lands the endpoint it needs. Don't add it here yet.
 */
export function Settings() {
  const qc = useQueryClient();
  const [base, setBase] = useState(auth.getBase());
  const [token, setToken] = useState(auth.getToken());
  const [theme, setTheme] = useTheme();
  const health = useHealth();

  function applyConn() {
    auth.setBase(base);
    auth.setToken(token);
    qc.invalidateQueries();
  }

  const healthLine = health.data
    ? `API v${health.data.version} · store ${health.data.store_ok ? "ok" : "down"}`
    : health.error
    ? `unreachable: ${(health.error as Error).message}`
    : "checking…";

  return (
    <div>
      <h1>Settings</h1>

      <section className="panel" style={{ marginBottom: 16, maxWidth: 480 }}>
        <h3>Connection</h3>
        <div className="grid" style={{ gap: 10 }}>
          <label className="kv" style={{ gridTemplateColumns: "100px 1fr" }}>
            <span className="dim">API base</span>
            <input
              aria-label="API base URL"
              placeholder="API base (/)"
              value={base}
              onChange={(e) => setBase(e.target.value)}
              onBlur={applyConn}
              onKeyDown={(e) => e.key === "Enter" && applyConn()}
            />
          </label>
          <label className="kv" style={{ gridTemplateColumns: "100px 1fr" }}>
            <span className="dim">Token</span>
            <input
              aria-label="API token"
              type="password"
              placeholder="token"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              onBlur={applyConn}
              onKeyDown={(e) => e.key === "Enter" && applyConn()}
            />
          </label>
          <div className="dim">{healthLine}</div>
        </div>
      </section>

      <section className="panel" style={{ maxWidth: 480 }}>
        <h3>Appearance</h3>
        <label className="row">
          <span className="dim">Theme</span>
          <select aria-label="theme" value={theme} onChange={(e) => setTheme(e.target.value)}>
            <option value="system">System 🖥️</option>
            <option value="light">Light ☀️</option>
            <option value="dark">Dark 🌙</option>
          </select>
        </label>
      </section>
    </div>
  );
}
