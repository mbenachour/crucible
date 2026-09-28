import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { auth } from "../api/client";
import { Findings } from "./Findings";
import type { Run } from "../api/types";

const RUN = {
  run_id: "r1", counts: { total: 12, mechanical_failed: 6, duplicate: 6 },
} as unknown as Run;

function renderFindings(initial = "/runs/r1/findings") {
  auth.setBase("http://api.test");
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
    new Response(JSON.stringify({ items: [], total: 0, limit: 50, offset: 0 }), {
      status: 200, headers: { "content-type": "application/json" },
    }),
  );
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[initial]}>
        <Routes>
          <Route path="/runs/:runId" element={<Outlet context={{ run: RUN }} />}>
            <Route path="findings" element={<Findings />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return fetchMock;
}

const lastUrl = (m: ReturnType<typeof renderFindings>) => String(m.mock.calls.at(-1)![0]);

describe("Findings default view (issue #107)", () => {
  it("requests only reported (reach_upheld) findings by default", async () => {
    const fetchMock = renderFindings();
    await screen.findByText(/Nothing survived validation/);
    expect(lastUrl(fetchMock)).toContain("status=reach_upheld");
  });

  it("explains what was hidden when nothing was reported", async () => {
    renderFindings();
    expect(await screen.findByText(/12 raw findings were rejected or/)).toBeTruthy();
    expect(screen.getByText("reported (0)")).toBeTruthy();
    expect(screen.getByText("all raw output (12)")).toBeTruthy();
  });

  it("shows every status once the raw view is chosen", async () => {
    const fetchMock = renderFindings();
    await screen.findByText(/Nothing survived validation/);
    fireEvent.click(screen.getByText("all raw output (12)"));
    await vi.waitFor(() => expect(lastUrl(fetchMock)).not.toContain("status="));
    expect(screen.getByText("mechanical_failed")).toBeTruthy(); // status filters now visible
  });

  it("keeps an explicit status filter in the raw view", async () => {
    const fetchMock = renderFindings("/runs/r1/findings?view=all&status=duplicate");
    await screen.findByText("No findings match these filters.");
    expect(lastUrl(fetchMock)).toContain("status=duplicate");
    expect(lastUrl(fetchMock)).not.toContain("reach_upheld");
  });
});
