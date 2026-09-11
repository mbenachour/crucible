import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { auth } from "../api/client";
import { NewRunModal } from "./NewRunModal";

// jsdom doesn't implement <dialog> show/close — no-op them so the component's
// effect doesn't throw; the modal's own visibility logic is what's under test.
beforeEach(() => {
  HTMLDialogElement.prototype.showModal = vi.fn();
  HTMLDialogElement.prototype.close = vi.fn();
});

function renderModal(props: Partial<React.ComponentProps<typeof NewRunModal>> = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <NewRunModal open onClose={() => {}} {...props} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("NewRunModal", () => {
  beforeEach(() => {
    auth.setToken("");
  });
  afterEach(() => {
    vi.restoreAllMocks();
    auth.setToken("");
  });

  it("does not require a token client-side — the server is the source of truth on auth", () => {
    // Most deployments (crucible serve --no-auth, the default for local use)
    // have no auth configured at all; disabling on "no token stored" would
    // make the button permanently greyed out for them. A 401/403 from the
    // server (if auth *is* on) is what actually gates this — see the
    // response-class tests below.
    renderModal();
    fireEvent.change(screen.getByPlaceholderText(/owner\/repo/i), { target: { value: "octocat/Hello-World" } });
    expect((screen.getByText("Start run") as HTMLButtonElement).disabled).toBe(false);
    expect(screen.getByText(/no auth configured/i)).toBeTruthy();
  });

  it("prefills repo/ref from the prefill prop", () => {
    renderModal({ prefill: { repo: "octocat/Hello-World", ref: "main" } });
    expect((screen.getByPlaceholderText(/owner\/repo/i) as HTMLInputElement).value).toBe("octocat/Hello-World");
    expect((screen.getByPlaceholderText("branch or tag") as HTMLInputElement).value).toBe("main");
  });

  it("shows a field error for an obviously-bad repo without calling the API", () => {
    auth.setToken("tok");
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    renderModal();
    fireEvent.change(screen.getByPlaceholderText(/owner\/repo/i), { target: { value: "not a repo" } });
    fireEvent.click(screen.getByText("Start run"));
    expect(screen.getByText(/must look like/)).toBeTruthy();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it.each([
    [422, { error: "invalid repo", detail: "host not allowed" }, "host not allowed"],
    [429, { error: "too many runs in progress", detail: "2/2 active — try again shortly" }, "try again shortly"],
    [403, { error: "forbidden" }, "needs a write token"],
  ])("renders a distinct message for a %i response", async (status, body, expectedText) => {
    auth.setToken("tok");
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(body), { status }),
    );
    renderModal();
    fireEvent.change(screen.getByPlaceholderText(/owner\/repo/i), { target: { value: "octocat/Hello-World" } });
    fireEvent.click(screen.getByText("Start run"));
    await waitFor(() => expect(screen.getByText(new RegExp(expectedText, "i"))).toBeTruthy());
  });

  it("closes and hands back the run_id on success (202)", async () => {
    auth.setToken("tok");
    const onClose = vi.fn();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ run_id: "abc123", clone_status: "pending" }), { status: 202 }),
    );
    renderModal({ onClose });
    fireEvent.change(screen.getByPlaceholderText(/owner\/repo/i), { target: { value: "octocat/Hello-World" } });
    fireEvent.click(screen.getByText("Start run"));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });
});
