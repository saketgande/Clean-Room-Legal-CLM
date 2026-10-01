import JSZip from "jszip";
import { describe, expect, it } from "vitest";

import { renderDocx } from "./DocumentView/DocxRenderer";

const W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main";
const R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships";

// A counterparty's Word file is attacker-controlled bytes, drawn in this app's
// origin. Two ways docx-preview has put that file's contents into live markup.
async function hostileDocx(): Promise<Blob> {
  const zip = new JSZip();
  zip.file(
    "[Content_Types].xml",
    `<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">` +
      `<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>` +
      `<Default Extension="xml" ContentType="application/xml"/>` +
      `<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>`,
  );
  zip.file(
    "_rels/.rels",
    `<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">` +
      `<Relationship Id="rId1" Type="${R}/officeDocument" Target="word/document.xml"/></Relationships>`,
  );
  zip.file(
    "word/_rels/document.xml.rels",
    `<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">` +
      `<Relationship Id="rId9" Type="${R}/hyperlink" Target="javascript:window.__pwned=2" TargetMode="External"/>` +
      `<Relationship Id="rId8" Type="${R}/hyperlink" Target="https://example.com/terms" TargetMode="External"/></Relationships>`,
  );
  zip.file(
    "word/document.xml",
    `<?xml version="1.0"?><w:document xmlns:w="${W}" xmlns:r="${R}"><w:body><w:p>` +
      `<w:r><w:t>1. Payment.</w:t></w:r>` +
      // w:char is meant to be a hex code point; 0.3.x pasted it into innerHTML.
      `<w:r><w:sym w:font="Symbol" w:char="41;&lt;img src=x onerror=&quot;window.__pwned=1&quot;&gt;"/></w:r>` +
      `<w:hyperlink r:id="rId9"><w:r><w:t>click here</w:t></w:r></w:hyperlink>` +
      `<w:hyperlink r:id="rId8"><w:r><w:t>terms</w:t></w:r></w:hyperlink>` +
      `</w:p></w:body></w:document>`,
  );
  return zip.generateAsync({ type: "blob" });
}

describe("drawing a Word file somebody else wrote", () => {
  it("never turns the file's text into markup or script links", async () => {
    const host = document.createElement("div");
    await renderDocx(await hostileDocx(), host);

    expect(host.textContent).toContain("Payment");
    expect(host.querySelector("img")).toBeNull();
    expect(host.querySelector("[onerror]")).toBeNull();
    const hrefs = [...host.querySelectorAll("a")].map((a) => a.getAttribute("href") ?? "");
    expect(hrefs.some((h) => /^\s*javascript:/i.test(h))).toBe(false);
    // An ordinary link is still a link.
    expect(hrefs).toContain("https://example.com/terms");
  });
});
