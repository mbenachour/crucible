import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { auth } from "../api/client";
import { RunOverview } from "./RunOverview";
import type { ConfigModels, Metrics, Run } from "../api/types";

const RUN_ID = "r1";

function runFixture(): Run {
  return {
    run_id: RUN_ID, repo_path: "/repo", repo_commit: "a".repeat(40), primary_language: "python",
    created_at: null, finished_at: null, status: "finished", outcome: "completed",
    recon_quality: "full", workspace_path: "", report_available: true, counts: {},
    source_spec: "", clone_status: "", clone_error: "", model_override: {},
  };
}

const METRICS: Metrics = {
  run_id: RUN_ID, counts: {}, fork_rate: "0/0", tool_usage: {},
  cycles: null, continuations: null, token_spend: null,
};

function effectiveEndpoint(overrides: Partial<ConfigModels["roles"][string]> = {}) {
  return {
    provider: "ollama", model: "qwen2.5-coder:7b", temperature: 0.1,
    base_url: "http://localhost:11434",
    source: { provider: "default", model: "default", temperature: "default", base_url: "default" },
    ...overrides,
  };
}

function renderOverview(configModels: ConfigModels) {
  auth.setBase("http://api.test");
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const u = String(input);
    if (u.includes("/config/models")) return new Response(JSON.stringify(configModels), { status: 200 });
    if (u.includes("/metrics")) return new Response(JSON.stringify(METRICS), { status: 200 });
    return new Response(JSON.stringify(runFixture()), { status: 200 });
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/runs/${RUN_ID}`]}>
        <Routes>
          <Route path="/runs/:runId" element={<RunOverview />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("RunOverview models panel (issue #77)", () => {
  it("renders the host-config value for a role without an override, untagged", async () => {
    renderOverview({ roles: { hunter: effectiveEndpoint({ model: "deepseek-v4-flash", provider: "deepseek" }) } });
    expect(await screen.findByText("deepseek-v4-flash")).toBeTruthy();
    expect(screen.queryByText("override")).toBeNull();
  });

  it("tags a field whose provenance is a run override", async () => {
    renderOverview({
      roles: {
        hunter: effectiveEndpoint({
          model: "deepseek-chat", provider: "deepseek",
          source: { provider: "run override", model: "run override", temperature: "default", base_url: "default" },
        }),
      },
    });
    expect(await screen.findByText("deepseek-chat")).toBeTruthy();
    expect(screen.getAllByText("override").length).toBeGreaterThan(0);
  });
});
