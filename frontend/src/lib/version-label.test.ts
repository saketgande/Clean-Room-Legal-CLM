import { describe, expect, it } from "vitest";
import { versionLabel } from "./version-label";

const v = (n: number, source = "playbook_redline") => ({ id: `v${n}`, version_number: n, source });
const current = { id: "v4", version_number: 4 };

describe("versionLabel", () => {
  it("marks the live version as current", () => {
    expect(versionLabel(v(4), current)).toBe("V4 · Playbook Redline · current");
  });

  it("marks a newer version that was never applied as not in use, so a high number is not read as the live one", () => {
    expect(versionLabel(v(5, "assistant_edit"), current)).toBe("V5 · Assistant Edit · not in use");
    expect(versionLabel(v(6, "assistant_edit"), current)).toBe("V6 · Assistant Edit · not in use");
  });

  it("marks an older version as earlier", () => {
    expect(versionLabel(v(1, "upload"), current)).toBe("V1 · Upload · earlier");
  });

  it("says nothing more when it does not know which version is live", () => {
    expect(versionLabel(v(2, "upload"), null)).toBe("V2 · Upload");
    expect(versionLabel(v(2, "upload"), undefined)).toBe("V2 · Upload");
  });
});
