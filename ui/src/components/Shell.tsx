import { NavLink, Outlet } from "react-router-dom";
import { NewRunModal, type NewRunPrefill } from "./NewRunModal";
import { NewRunModalContext } from "../lib/newRunModal";
import { useHealth } from "../api/hooks";
import { useSidenavCollapsed } from "../lib/theme";
import { useState } from "react";

function HealthDot() {
  const h = useHealth();
  const color = h.data?.store_ok ? "var(--ok-fg)" : h.error ? "var(--bad-fg)" : "var(--warn-fg)";
  const title = h.data
    ? `API v${h.data.version} · store ${h.data.store_ok ? "ok" : "down"}`
    : h.error
    ? `unreachable: ${(h.error as Error).message}`
    : "checking…";
  return <span title={title} style={{ width: 9, height: 9, borderRadius: 9, background: color, display: "inline-block", flex: "none" }} />;
}

function SandboxBanner() {
  const h = useHealth();
  // Undefined while loading / on a fetch error — only warn once health has
  // actually answered "not ok", so this never flashes on a slow first load.
  if (!h.data || h.data.sandbox_ok) return null;
  return (
    <div className="banner bad">
      <span className="dim">⚠</span> Sandbox unavailable — new runs will fail immediately
      (docker sandbox is required unless a run is started with --no-sandbox).{" "}
      {h.data.sandbox_detail && <code className="mono">{h.data.sandbox_detail}</code>}
    </div>
  );
}

const navLinkClass = ({ isActive }: { isActive: boolean }) => (isActive ? "active" : "");

export function Shell() {
  const [collapsed, setCollapsed] = useSidenavCollapsed();
  const [newRunOpen, setNewRunOpen] = useState(false);
  const [newRunPrefill, setNewRunPrefill] = useState<NewRunPrefill | undefined>(undefined);

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
        <div className="main">
          <nav className={collapsed ? "sidenav collapsed" : "sidenav"} aria-label="Main">
            <div className="sidenav-brand">
              <span className="brand">{collapsed ? "C" : "CRUCIBLE"}</span>
              <HealthDot />
            </div>

            <button
              className="primary sidenav-newrun"
              title="New run"
              onClick={() => {
                setNewRunPrefill(undefined);
                setNewRunOpen(true);
              }}
            >
              <span aria-hidden="true">+</span>
              <span className="label">New run</span>
            </button>

            <NavLink to="/runs" className={navLinkClass} title="Runs">
              <span aria-hidden="true">▤</span>
              <span className="label">Runs</span>
            </NavLink>
            <NavLink to="/wishes" className={navLinkClass} title="Wishlist">
              <span aria-hidden="true">✦</span>
              <span className="label">Wishlist</span>
            </NavLink>

            <span className="sidenav-spacer" />

            <button
              className="sidenav-collapse"
              onClick={() => setCollapsed(!collapsed)}
              aria-pressed={collapsed}
              title={collapsed ? "Expand nav" : "Collapse nav"}
            >
              <span aria-hidden="true">{collapsed ? "»" : "«"}</span>
              <span className="label">Collapse</span>
            </button>
            <NavLink to="/settings" className={navLinkClass} title="Settings">
              <span aria-hidden="true">⚙</span>
              <span className="label">Settings</span>
            </NavLink>
          </nav>
          <div className="content">
            <SandboxBanner />
            <Outlet />
          </div>
        </div>
        <NewRunModal open={newRunOpen} onClose={() => setNewRunOpen(false)} prefill={newRunPrefill} />
      </div>
    </NewRunModalContext.Provider>
  );
}
