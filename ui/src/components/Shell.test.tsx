import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Shell } from "./Shell";

function renderShell(initialPath = "/runs") {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify({ version: "test", store_ok: true }), {
      status: 200,
      headers: { "content-type": "application/json" },
    }),
  );
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route element={<Shell />}>
            <Route path="/runs" element={<div>runs page</div>} />
            <Route path="/wishes" element={<div>wishes page</div>} />
            <Route path="/settings" element={<div>settings page</div>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("Shell side nav", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => localStorage.clear());

  it("renders brand, New run, Runs, Wishlist, and Settings", () => {
    renderShell();
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(nav.textContent).toContain("CRUCIBLE");
    expect(screen.getByTitle("New run")).toBeTruthy();
    expect(screen.getByTitle("Runs")).toBeTruthy();
    expect(screen.getByTitle("Wishlist")).toBeTruthy();
    expect(screen.getByTitle("Settings")).toBeTruthy();
  });

  it("marks the active route", () => {
    renderShell("/wishes");
    expect(screen.getByTitle("Wishlist").className).toContain("active");
    expect(screen.getByTitle("Runs").className).not.toContain("active");
  });

  it("toggles collapsed state and persists it to localStorage", () => {
    renderShell();
    const toggle = screen.getByTitle("Collapse nav");
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(nav.className).not.toContain("collapsed");

    fireEvent.click(toggle);
    expect(nav.className).toContain("collapsed");
    expect(localStorage.getItem("crucible.sidenavCollapsed")).toBe("true");
  });

  it("restores the collapsed state on remount (localStorage)", () => {
    localStorage.setItem("crucible.sidenavCollapsed", "true");
    renderShell();
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(nav.className).toContain("collapsed");
  });
});
