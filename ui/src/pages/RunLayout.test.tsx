import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { auth } from "../api/client";
import { RunLayout } from "./RunLayout";
import type { Run } from "../api/types";

function runFixture(overrides: Partial<Run> = {}): Run {
  return {
    run_id: "r1", repo_path: "", repo_commit: "", primary_language: "",
    created_at: null, finished_at: null, status: "running", outcome: "",
    recon_quality: "", workspace_path: "", report_available: false, counts: {},
    source_spec: "octocat/Hello-World", clone_status: "pending", clone_error: "",
    ...overrides,
  };
}

function renderAt(run: Run) {
  auth.setBase("http://api.test");
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify(run), { status: 200, headers: { "content-type": "application/json" } }),
  );
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/runs/${run.run_id}`]}>
        <Routes>
          <Route path="/runs/:runId" element={<RunLayout />}>
            <Route index element={<div>overview</div>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("RunLayout launch banner", () => {
  it("shows a queued banner while clone_status is pending", async () => {
    renderAt(runFixture({ clone_status: "pending" }));
    expect(await screen.findByText(/Queued/)).toBeTruthy();
  });

  it("shows a cloning banner", async () => {
    renderAt(runFixture({ clone_status: "cloning" }));
    expect(await screen.findByText(/Cloning/)).toBeTruthy();
  });

  it("shows the clone error and a retry button on failure", async () => {
    renderAt(runFixture({ clone_status: "clone_failed", clone_error: "host not allowed" }));
    expect(await screen.findByText(/host not allowed/)).toBeTruthy();
    expect(screen.getByText("Try again")).toBeTruthy();
  });

  it("shows no banner once cloned", async () => {
    renderAt(runFixture({ clone_status: "cloned", repo_path: "/x", repo_commit: "a".repeat(40) }));
    await screen.findByText("overview");
    expect(screen.queryByText(/Queued|Cloning|Failed to clone/)).toBeNull();
  });
});
