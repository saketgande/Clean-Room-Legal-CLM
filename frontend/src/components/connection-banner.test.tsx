import { afterEach, describe, expect, it } from "vitest";
import { act, render, screen } from "@testing-library/react";
import { ConnectionBanner } from "./connection-banner";
import { connectionMonitor } from "@/lib/api";

afterEach(() => act(() => connectionMonitor.serverAnswered()));

describe("ConnectionBanner", () => {
  it("shows nothing while the server answers", () => {
    render(<ConnectionBanner />);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("explains the silence when the server stops answering, and goes away when it is back", () => {
    render(<ConnectionBanner />);
    act(() => connectionMonitor.serverNotAnswering());
    expect(screen.getByRole("alert")).toHaveTextContent("The server isn't answering right now");
    act(() => connectionMonitor.serverAnswered());
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
