import { describe, expect, it } from "vitest";

import { DOCX_MIME, rendererFor } from "./DocumentView";

describe("which renderer draws a file", () => {
  it("draws the types it knows", () => {
    expect(rendererFor("application/pdf")).toBe("pdf");
    expect(rendererFor(DOCX_MIME)).toBe("docx");
    expect(rendererFor("image/png")).toBe("image");
    expect(rendererFor("image/jpeg")).toBe("image");
    expect(rendererFor("text/plain")).toBe("text");
  });

  it("never treats an SVG as a picture", () => {
    // A prefix test on "image/" would accept it, and SVG is a scripting format
    // wearing an image media type — on bytes somebody uploaded.
    expect(rendererFor("image/svg+xml")).toBe("none");
  });

  it("says plainly when it cannot draw something", () => {
    expect(rendererFor("text/html")).toBe("none");
    expect(rendererFor("application/vnd.ms-excel")).toBe("none");
    expect(rendererFor("")).toBe("none");
  });
});
