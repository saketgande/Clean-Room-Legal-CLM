import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider, useAuth } from "./auth";
import { HttpError, NO_REPLY_STATUS } from "./api";
import type { UserResponse } from "./types";

const h = vi.hoisted(() => ({ me: vi.fn() }));
vi.mock("./endpoints", () => ({ authApi: { me: h.me, login: vi.fn(), logout: vi.fn(), switchRole: vi.fn() } }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn() }) }));

const user = { id: "u1", email: "a@example.com", full_name: "Ada" } as unknown as UserResponse;

function Probe() {
  const { user: u, loading } = useAuth();
  return <div>{loading ? "loading" : u ? `signed in as ${u.full_name}` : "signed out"}</div>;
}

function mount() {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <AuthProvider>
        <Probe />
      </AuthProvider>
    </QueryClientProvider>,
  );
}

const flush = async () => {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
};

beforeEach(() => {
  vi.useFakeTimers();
  h.me.mockReset();
  window.localStorage.setItem("aegis.access_token", "token");
});
afterEach(() => {
  vi.useRealTimers();
  window.localStorage.clear();
});

describe("AuthProvider when the server does not answer", () => {
  it("keeps waiting instead of signing the person out, then signs them in once the server is back", async () => {
    h.me.mockRejectedValueOnce(new HttpError(NO_REPLY_STATUS, "Can't reach the server.")).mockResolvedValueOnce(user);
    mount();
    await flush();
    expect(screen.getByText("loading")).toBeInTheDocument();
    await act(async () => {
      vi.advanceTimersByTime(4000);
    });
    await flush();
    expect(screen.getByText("signed in as Ada")).toBeInTheDocument();
    expect(h.me).toHaveBeenCalledTimes(2);
  });

  it("does not keep asking after the page is left", async () => {
    h.me.mockRejectedValue(new HttpError(NO_REPLY_STATUS, "Can't reach the server."));
    const view = mount();
    await flush();
    view.unmount();
    await act(async () => {
      vi.advanceTimersByTime(20_000);
    });
    expect(h.me).toHaveBeenCalledTimes(1);
  });
});

describe("AuthProvider when the server answers with a refusal", () => {
  it("signs the person out on a real error reply", async () => {
    h.me.mockRejectedValueOnce(new HttpError(403, "Forbidden"));
    mount();
    await flush();
    expect(screen.getByText("signed out")).toBeInTheDocument();
  });

  it("signs in when the server answers", async () => {
    h.me.mockResolvedValueOnce(user);
    mount();
    await flush();
    expect(screen.getByText("signed in as Ada")).toBeInTheDocument();
  });
});
