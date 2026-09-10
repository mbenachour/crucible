import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch, ApiError, auth } from "./client";

describe("apiFetch", () => {
  beforeEach(() => {
    auth.setBase("http://api.test");
    auth.setToken("secret");
  });
  afterEach(() => {
    auth.setToken("");
    auth.setBase("");
    vi.restoreAllMocks();
  });

  it("attaches the bearer token and base URL, encodes array query params", async () => {
    const spy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ ok: 1 }), { status: 200, headers: { "content-type": "application/json" } }),
    );
    await apiFetch("/runs", { query: { status: ["a", "b"], limit: 10 } });
    const [url, init] = spy.mock.calls[0];
    expect(String(url)).toBe("http://api.test/runs?status=a&status=b&limit=10");
    expect((init as RequestInit).headers).toMatchObject({ Authorization: "Bearer secret" });
  });

  it("throws a typed ApiError with the {error, detail} body", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ error: "run not found", detail: "abc" }), { status: 404 }),
    );
    await expect(apiFetch("/runs/abc")).rejects.toMatchObject({
      status: 404,
      message: "run not found",
      detail: "abc",
    });
  });

  it("wraps a network failure as ApiError status 0", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("boom"));
    const err = (await apiFetch("/health").catch((e) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(0);
  });
});
