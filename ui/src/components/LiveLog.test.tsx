import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { auth } from "../api/client";
import { LiveLog } from "./LiveLog";

function streamOf(chunks: string[], status = 200): Response {
  const encoder = new TextEncoder();
  let i = 0;
  const body = new ReadableStream<Uint8Array>({
    pull(controller) {
      if (i < chunks.length) {
        controller.enqueue(encoder.encode(chunks[i++]));
      } else {
        controller.close();
      }
    },
  });
  return new Response(body, { status });
}

describe("LiveLog", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    auth.setToken("");
    auth.setBase("");
  });

  it("splits a chunked stream on newlines, including a split-across-chunks line", async () => {
    auth.setBase("http://api.test");
    vi.spyOn(globalThis, "fetch").mockResolvedValue(streamOf(["l1\nl2\n", "l3-sp", "lit\n"]));
    render(<LiveLog runId="r1" />);
    await waitFor(() => expect(screen.getByText(/l3-split/)).toBeTruthy());
    expect(screen.getByText(/l1/)).toBeTruthy();
    expect(screen.getByText(/l2/)).toBeTruthy();
  });

  it("sends the bearer token when one is set", async () => {
    auth.setBase("http://api.test");
    auth.setToken("secret");
    const spy = vi.spyOn(globalThis, "fetch").mockResolvedValue(streamOf(["hi\n"]));
    render(<LiveLog runId="r1" />);
    await waitFor(() => expect(spy).toHaveBeenCalled());
    const [url, init] = spy.mock.calls[0];
    expect(String(url)).toContain("/runs/r1/log/stream");
    expect((init as RequestInit).headers).toMatchObject({ Authorization: "Bearer secret" });
  });

  it("shows the server error on a non-2xx response", async () => {
    auth.setBase("http://api.test");
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ error: "run not found" }), { status: 404 }),
    );
    render(<LiveLog runId="does-not-exist" />);
    expect(await screen.findByText(/run not found/)).toBeTruthy();
  });

  it("shows 'stream ended' once the body closes", async () => {
    auth.setBase("http://api.test");
    vi.spyOn(globalThis, "fetch").mockResolvedValue(streamOf(["done\n"]));
    render(<LiveLog runId="r1" />);
    expect(await screen.findByText(/stream ended/)).toBeTruthy();
  });
});
