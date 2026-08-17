import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { Markdown } from "./markdown";

describe("Markdown", () => {
  it("does not render raw HTML from markdown source", () => {
    // ReactMarkdown defaults to skipHtml; a <script> tag in the source must
    // not become a real <script> in the DOM.
    const dangerous = '<script id="xss">alert(1)</script>safe text';
    render(<Markdown>{dangerous}</Markdown>);
    expect(document.getElementById("xss")).toBeNull();
    expect(screen.getByText(/safe text/)).toBeInTheDocument();
  });

  it("renders external links with rel='noopener noreferrer'", () => {
    render(<Markdown>{"[link](https://example.com)"}</Markdown>);
    const anchor = screen.getByRole("link", { name: "link" });
    expect(anchor.getAttribute("rel")).toBe("noopener noreferrer");
    expect(anchor.getAttribute("target")).toBe("_blank");
  });

  it("neutralizes fabricated in-app links to plain text", () => {
    // The assistant sometimes invents a route like /intake/requests/REQ-4196;
    // it must render as text, never a clickable (dead) link.
    render(<Markdown>{"[REQ-4196](/intake/requests/REQ-4196)"}</Markdown>);
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.getByText("REQ-4196")).toBeInTheDocument();
  });
});
