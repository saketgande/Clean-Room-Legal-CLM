import { describe, expect, it } from "vitest";

import { contractDisplayName, looksLikeFilename, prettyFilename } from "./utils";

describe("looksLikeFilename", () => {
  it("catches what the register was actually showing as contract names", () => {
    // Every one of these was rendering as a row title on /contracts.
    expect(looksLikeFilename("b87f2c89ed253d4d0b7492a6f8b6f4de.jpg")).toBe(true);
    expect(looksLikeFilename("leecounty-scan-6pg.pdf")).toBe(true);
    expect(looksLikeFilename("Oregon-DAS-MSA.docx")).toBe(true);
    expect(looksLikeFilename("Acme_MSA_Counterparty_Draft")).toBe(true);
    expect(looksLikeFilename("")).toBe(true);
    expect(looksLikeFilename(null)).toBe(true);
  });

  it("leaves a real title alone", () => {
    // A human-entered name must never be replaced, even when a type is known.
    expect(looksLikeFilename("Mutual NDA — TCS")).toBe(false);
    expect(looksLikeFilename("SOFTWARE LICENSING AGREEMENT")).toBe(false);
    expect(looksLikeFilename("Master Services Agreement")).toBe(false);
  });
});

describe("contractDisplayName", () => {
  it("names the agreement rather than the upload", () => {
    expect(
      contractDisplayName({
        title: "b87f2c89ed253d4d0b7492a6f8b6f4de.jpg",
        contract_type: "Letter",
        counterparty_name: "Amit Shah",
      }),
    ).toBe("Letter — Amit Shah");
  });

  it("prefers an existing real title over a derived one", () => {
    expect(
      contractDisplayName({
        title: "Mutual NDA — TCS",
        contract_type: "NDA",
        counterparty_name: "Someone Else",
      }),
    ).toBe("Mutual NDA — TCS");
  });

  it("degrades one field at a time instead of jumping to Untitled", () => {
    const filename = "Oregon-DAS-MSA.docx";
    expect(
      contractDisplayName({ title: filename, contract_type: "Master Services Agreement" }),
    ).toBe("Master Services Agreement");
    expect(contractDisplayName({ title: filename, counterparty_name: "Oregon DAS" })).toBe(
      "Oregon DAS",
    );
    // Nothing but the filename: tidy it rather than show "Untitled contract".
    expect(contractDisplayName({ title: filename })).toBe("Oregon DAS MSA");
  });

  it("only says Untitled when there is genuinely nothing", () => {
    expect(contractDisplayName({ title: null })).toBe("Untitled contract");
  });
});

describe("prettyFilename", () => {
  it("keeps the upload readable as the secondary line", () => {
    expect(prettyFilename("Acme_MSA_Counterparty_Draft")).toBe("Acme MSA Counterparty Draft");
    expect(prettyFilename("leecounty-scan-6pg.pdf")).toBe("leecounty scan 6pg");
  });
});
