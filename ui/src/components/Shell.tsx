import { useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { auth } from "../api/client";
import { useHealth } from "../api/hooks";
import { NewRunModal, type NewRunPrefill } from "./NewRunModal";
import { NewRunModalContext } from "../lib/newRunModal";

function useTheme(): [string, (t: string) => void] {
  const [theme, setTheme] = useState<string>(() => {
    try {
      return localStorage.getItem("crucible.theme") || "system";
    } catch {
      return "system";
    }
  });
  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
    try {
      localStorage.setItem("crucible.theme", theme);
    } catch {
      /* ignore */
    }
  }, [theme]);
  return [theme, setTheme];
}

function HealthDot() {
  const h = useHealth();
  const color = h.data?.store_ok ? "var(--ok-fg)" : h.error ? "var(--bad-fg)" : "var(--warn-fg)";
  const title = h.data
    ? `API v${h.data.version} · store ${h.data.store_ok ? "ok" : "down"}`
    : h.error
    ? `unreachable: ${(h.error as Error).message}`
    : "checking…";
  return <span title={title} style={{ width: 9, height: 9, borderRadius: 9, background: color, display: "inline-block" }} />;
}

export function Shell() {
  const qc = useQueryClient();
  const [base, setBase] = useState(auth.getBase());
  const [token, setToken] = useState(auth.getToken());
  const [theme, setTheme] = useTheme();
  const [newRunOpen, setNewRunOpen] = useState(false);
  const [newRunPrefill, setNewRunPrefill] = useState<NewRunPrefill | undefined>(undefined);

  function applyConn() {
    auth.setBase(base);
    auth.setToken(token);
    qc.invalidateQueries();
  }

  const link = ({ isActive }: { isActive: boolean }) => (isActive ? "active" : "");

  return (
    <NewRunModalContext.Provider
      value={{
        open: (prefill) => {
          setNewRunPrefill(prefill);
          setNewRunOpen(true);
        },
      }}
    >
    <div className="shell">
      <div className="topbar">
        <span className="brand">CRUCIBLE</span>
        <HealthDot />
        <span className="spacer" />
        <button className="primary" onClick={() => { setNewRunPrefill(undefined); setNewRunOpen(true); }}>
          + New run
        </button>
        <input
          aria-label="API base URL"
          placeholder="API base (/)"
          value={base}
          onChange={(e) => setBase(e.target.value)}
          onBlur={applyConn}
          onKeyDown={(e) => e.key === "Enter" && applyConn()}
        />
        <input
          className="token"
          aria-label="API token"
          type="password"
          placeholder="token"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          onBlur={applyConn}
          onKeyDown={(e) => e.key === "Enter" && applyConn()}
        />
        <select aria-label="theme" value={theme} onChange={(e) => setTheme(e.target.value)}>
          <option value="system">🖥️</option>
          <option value="light">☀️</option>
          <option value="dark">🌙</option>
        </select>
      </div>
      <div className="main">
        <nav className="sidenav">
          <NavLink to="/runs" className={link}>
            Runs
          </NavLink>
          <NavLink to="/wishes" className={link}>
            Wishlist
          </NavLink>
        </nav>
        <div className="content">
          <Outlet />
        </div>
      </div>
      <NewRunModal open={newRunOpen} onClose={() => setNewRunOpen(false)} prefill={newRunPrefill} />
    </div>
    </NewRunModalContext.Provider>
  );
}
