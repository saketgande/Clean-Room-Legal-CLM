import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SignatureCard, signersReady, startingRows } from "./_signature-card";

vi.mock("@/components/toast", () => ({ useToast: () => ({ notify: vi.fn() }) }));
vi.mock("@/lib/endpoints", () => ({
  contractsApi: { signers: vi.fn().mockResolvedValue([
    { name: "Acme Ltd", email: "legal@acme.test", kind: "party" },
    { name: "Priya", email: "priya@us.test", kind: "user" },
  ]) },
  signaturesApi: { send: vi.fn() },
}));

const wrap = (ui: React.ReactElement) => render(<QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>);

describe("startingRows", () => {
  it("pre-fills only the contract parties — our own signer is never added unasked", () => {
    expect(startingRows([{ name: "Acme", email: "a@a.test", kind: "party" }, { name: "Me", email: "m@m.test", kind: "user" }]))
      .toEqual([{ name: "Acme", email: "a@a.test", role: "Counterparty" }]);
  });
  it("gives one empty row when no party has an email, so the form is never blank of inputs", () => {
    expect(startingRows([])).toEqual([{ name: "", email: "", role: "" }]);
  });
});

describe("signersReady", () => {
  it("refuses a half-filled signer (the envelope would go out to a bad address)", () => {
    expect(signersReady([{ name: "A", email: "", role: "" }])).toBe(false);
    expect(signersReady([{ name: "A", email: "a@a.test", role: "" }])).toBe(true);
  });
});

describe("SignatureCard", () => {
  it("shows each signer's status once the envelope is out, with no send button (a second send would double-sign)", () => {
    wrap(<SignatureCard contractId="c1" rows={[
      { name: "Sent for signature", kind: "DocuSign", detail: "Envelope sent", who: "Sam", status: "done", when: null },
      { name: "Acme signs", kind: "Signer", detail: "legal@acme.test", who: "Acme", status: "done", when: null },
      { name: "Bo signs", kind: "Signer", detail: "bo@us.test", who: "Bo", status: "waiting", when: null },
    ]} />);
    expect(screen.getByText("Signed")).toBeTruthy();
    expect(screen.getByText("Waiting")).toBeTruthy();
    expect(screen.queryByText(/Send for signature →/)).toBeNull();
  });

  it("pre-fills the counterparty and enables Send", async () => {
    wrap(<SignatureCard contractId="c1" rows={[]} />);
    expect(await screen.findByDisplayValue("legal@acme.test")).toBeTruthy();
    expect((screen.getByText(/Send for signature →/) as HTMLButtonElement).disabled).toBe(false);
  });
});
