import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { auth } from "../api/client";
import { Settings } from "./Settings";
import type { ConfigCatalog, ConfigModels, Health } from "../api/types";

const HEALTH: Health = { status: "ok", version: "0.9.0", store_ok: true };

const CATALOG: ConfigCatalog = {
  families: {
    deepseek: [
      { id: "deepseek/deepseek-chat-v3.1", label: "DeepSeek Chat V3.1", family: "deepseek" },
      { id: "deepseek/deepseek-r1-0528", label: "DeepSeek R1 (0528)", family: "deepseek" },
    ],
    qwen: [
      { id: "qwen/qwen-2.5-72b-instruct", label: "Qwen 2.5 72B Instruct", family: "qwen" },
      { id: "qwen/qwen3-32b", label: "Qwen3 32B", family: "qwen" },
    ],
  },
};

const MODELS: ConfigModels = {
  roles: {
    recon: {
      provider: "openrouter", model: "qwen/qwen-2.5-72b-instruct", temperature: 0.1, base_url: "http://x",
      source: { model: "config.yaml", temperature: "default", base_url: "default" },
    },
    hunter: {
      provider: "openrouter", model: "deepseek/deepseek-chat-v3.1", temperature: 0.3, base_url: "http://y",
      source: { model: "env:CRUCIBLE_MODEL_HUNTER", temperature: "default", base_url: "default" },
    },
    validator_bug: {
      provider: "openrouter", model: "qwen/qwen3-32b", temperature: 0.1, base_url: "http://x",
      source: { model: "default", temperature: "default", base_url: "default" },
    },
    validator_reach: {
      provider: "openrouter", model: "deepseek/deepseek-r1-0528", temperature: 0.1, base_url: "http://x",
      source: { model: "default", temperature: "default", base_url: "default" },
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
    mockFetch({ "/health": HEALTH, "/config/models": MODELS, "/config/catalog": CATALOG });
    renderSettings();
    expect(screen.getByText("Connection")).toBeTruthy();
    expect(screen.getByText("Appearance")).toBeTruthy();
    expect(screen.getByText("Models")).toBeTruthy();
  });

  it("renders a model dropdown per role, preselected to the effective model, with its provenance", async () => {
    mockFetch({ "/health": HEALTH, "/config/models": MODELS, "/config/catalog": CATALOG });
    renderSettings();
    const hunterSelect = await screen.findByLabelText<HTMLSelectElement>("hunter model");
    expect(hunterSelect.value).toBe("deepseek/deepseek-chat-v3.1");
    expect(hunterSelect.disabled).toBe(true); // read-only for now — see the section note
    expect((screen.getByLabelText<HTMLSelectElement>("recon model")).value).toBe(
      "qwen/qwen-2.5-72b-instruct",
    );
    expect(screen.getAllByText("deepseek/deepseek-r1-0528").length).toBe(1); // validator_reach only
    expect(screen.getByText("env:CRUCIBLE_MODEL_HUNTER")).toBeTruthy();
    expect(screen.getByText("config.yaml")).toBeTruthy();
    expect(screen.getAllByText("default").length).toBeGreaterThan(0);
  });

  it("shows no provider column or input — every role is OpenRouter", async () => {
    mockFetch({ "/health": HEALTH, "/config/models": MODELS, "/config/catalog": CATALOG });
    renderSettings();
    await screen.findByLabelText("hunter model");
    expect(screen.getByText(/every role runs on openrouter/i)).toBeTruthy();
    expect(screen.queryByText("provider")).toBeNull();
  });

  it("shows the health line inline", async () => {
    mockFetch({ "/health": HEALTH, "/config/models": MODELS, "/config/catalog": CATALOG });
    renderSettings();
    expect(await screen.findByText(/API v0\.9\.0 · store ok/)).toBeTruthy();
  });

  it("degrades gracefully when /config/models 404s", async () => {
    mockFetch({ "/health": HEALTH, "/config/models": { __status: 404 }, "/config/catalog": CATALOG });
    renderSettings();
    expect(await screen.findByText(/unavailable/i)).toBeTruthy();
  });

  it("applies connection changes on blur and invalidates queries", async () => {
    mockFetch({ "/health": HEALTH, "/config/models": MODELS, "/config/catalog": CATALOG });
    renderSettings();
    const baseInput = screen.getByLabelText("API base URL");
    fireEvent.change(baseInput, { target: { value: "http://other.test" } });
    fireEvent.blur(baseInput);
    await waitFor(() => expect(auth.getBase()).toBe("http://other.test"));
  });

  it("switches theme via the segmented control and persists it", async () => {
    mockFetch({ "/health": HEALTH, "/config/models": MODELS, "/config/catalog": CATALOG });
    renderSettings();
    fireEvent.click(screen.getByRole("radio", { name: "dark" }));
    await waitFor(() => expect(localStorage.getItem("crucible.theme")).toBe("dark"));
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
  });
});
