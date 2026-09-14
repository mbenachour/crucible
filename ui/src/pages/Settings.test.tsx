import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { auth } from "../api/client";
import { Settings } from "./Settings";
import type { ConfigModels, Health } from "../api/types";

const HEALTH: Health = { status: "ok", version: "0.9.0", store_ok: true };
const MODELS: ConfigModels = {
  roles: {
    recon: {
      provider: "ollama", model: "qwen2.5-coder:7b", temperature: 0.1, base_url: "http://x",
      source: { provider: "default", model: "config.yaml", temperature: "default", base_url: "default" },
    },
    hunter: {
      provider: "deepseek", model: "deepseek-v4-flash", temperature: 0.3, base_url: "http://y",
      source: { provider: "env:CRUCIBLE_MODEL_HUNTER", model: "env:CRUCIBLE_MODEL_HUNTER", temperature: "default", base_url: "default" },
    },
    validator_bug: {
      provider: "ollama", model: "llama3.1:8b", temperature: 0.1, base_url: "http://x",
      source: { provider: "default", model: "default", temperature: "default", base_url: "default" },
    },
    validator_reach: {
      provider: "ollama", model: "llama3.1:8b", temperature: 0.1, base_url: "http://x",
      source: { provider: "default", model: "default", temperature: "default", base_url: "default" },
    },
  },
};

type MockResponse = unknown | { __status: number };

function mockFetch(responses: Record<string, MockResponse>) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    const path = Object.keys(responses).find((p) => url.includes(p));
    const body = path ? responses[path] : undefined;
    if (body && typeof body === "object" && "__status" in body) {
      return new Response(JSON.stringify({ error: "not found" }), { status: (body as { __status: number }).__status });
    }
    return new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });
  });
}

function renderSettings() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <Settings />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("Settings page", () => {
  beforeEach(() => {
    auth.setBase("http://api.test");
    auth.setToken("");
    try {
      localStorage.removeItem("crucible.theme");
    } catch {
      /* ignore */
    }
  });
  afterEach(() => {
    vi.restoreAllMocks();
    auth.setBase("");
    auth.setToken("");
  });

  it("renders all three sections", async () => {
    mockFetch({ "/health": HEALTH, "/config/models": MODELS });
    renderSettings();
    expect(screen.getByText("Connection")).toBeTruthy();
    expect(screen.getByText("Appearance")).toBeTruthy();
    expect(screen.getByText("Models")).toBeTruthy();
  });

  it("renders a row per role with its provenance", async () => {
    mockFetch({ "/health": HEALTH, "/config/models": MODELS });
    renderSettings();
    await screen.findByText("deepseek-v4-flash");
    expect(screen.getByText("qwen2.5-coder:7b")).toBeTruthy();
    expect(screen.getAllByText("llama3.1:8b").length).toBe(2); // validator_bug + validator_reach
    expect(screen.getAllByText("env:CRUCIBLE_MODEL_HUNTER").length).toBeGreaterThan(0);
    expect(screen.getByText("config.yaml")).toBeTruthy();
    expect(screen.getAllByText("default").length).toBeGreaterThan(0);
  });

  it("shows the health line inline", async () => {
    mockFetch({ "/health": HEALTH, "/config/models": MODELS });
    renderSettings();
    expect(await screen.findByText(/API v0\.9\.0 · store ok/)).toBeTruthy();
  });

  it("degrades gracefully when /config/models 404s", async () => {
    mockFetch({ "/health": HEALTH, "/config/models": { __status: 404 } });
    renderSettings();
    expect(await screen.findByText(/unavailable/i)).toBeTruthy();
  });

  it("applies connection changes on blur and invalidates queries", async () => {
    mockFetch({ "/health": HEALTH, "/config/models": MODELS });
    renderSettings();
    const baseInput = screen.getByLabelText("API base URL");
    fireEvent.change(baseInput, { target: { value: "http://other.test" } });
    fireEvent.blur(baseInput);
    await waitFor(() => expect(auth.getBase()).toBe("http://other.test"));
  });

  it("switches theme via the segmented control and persists it", async () => {
    mockFetch({ "/health": HEALTH, "/config/models": MODELS });
    renderSettings();
    fireEvent.click(screen.getByRole("radio", { name: "dark" }));
    await waitFor(() => expect(localStorage.getItem("crucible.theme")).toBe("dark"));
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
  });
});
