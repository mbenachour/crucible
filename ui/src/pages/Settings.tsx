import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { auth } from "../api/client";
import { useConfigModels, useHealth } from "../api/hooks";
import { Q } from "../components/states";
import { useTheme } from "../lib/theme";
import type { ModelEndpoint } from "../api/types";

const ROLE_LABEL: Record<string, string> = {
  recon: "recon",
  hunter: "hunter",
  validator_bug: "validator (bug)",
  validator_reach: "validator (reach)",
};

function ModelRow({ role, e }: { role: string; e: ModelEndpoint }) {
  return (
    <tr>
      <td>{ROLE_LABEL[role] ?? role}</td>
      <td className="mono">
        {e.provider}
        <div className="dim">{e.source.provider}</div>
      </td>
      <td className="mono">
        {e.model}
        <div className="dim">{e.source.model}</div>
      </td>
      <td className="mono">
        {e.temperature}
        <div className="dim">{e.source.temperature}</div>
      </td>
    </tr>
  );
}

export function Settings() {
  const qc = useQueryClient();
  const [base, setBase] = useState(auth.getBase());
  const [token, setToken] = useState(auth.getToken());
  const [theme, setTheme] = useTheme();
  const health = useHealth();
  const models = useConfigModels();

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
          <div className="dim">
            No token set — fine if this API has no auth configured; set a write token here to start runs or resolve wishes on one that does.
          </div>
          <div>{healthLine}</div>
        </div>
      </section>

      <section className="panel" style={{ marginBottom: 16, maxWidth: 480 }}>
        <h3>Appearance</h3>
        <div className="row">
          <span className="dim">Theme</span>
          <div className="segmented" role="radiogroup" aria-label="theme">
            {(["system", "light", "dark"] as const).map((t) => (
              <label key={t}>
                <input
                  type="radio"
                  name="theme"
                  value={t}
                  checked={theme === t}
                  onChange={() => setTheme(t)}
                />
                {t}
              </label>
            ))}
          </div>
        </div>
      </section>

      <section className="panel" style={{ maxWidth: 640 }}>
        <h3>Models</h3>
        <Q q={models} notFound={<div className="dim">unavailable — this API doesn't expose model config yet.</div>}>
          {(d) => (
            <div className="tbl-wrap">
              <table>
                <thead>
                  <tr>
                    <th>role</th>
                    <th>provider</th>
                    <th>model</th>
                    <th>temp</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(d.roles).map(([role, e]) => (
                    <ModelRow key={role} role={role} e={e} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Q>
        <div className="dim" style={{ marginTop: 8 }}>
          Read-only for now — small text under each value shows where it came from (default / config.yaml / env var).
        </div>
      </section>
    </div>
  );
}
