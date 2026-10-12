// Which pages the app frame pads and centres for them.
//
// The pages that were redesigned lay themselves out edge to edge and bring their own padding. The older
// pages (built from PageHeader, Card and Tabs) bring none, so the frame has to give them the standard gutter.
// A page missing from this list is drawn flush against the top and left edges, which is how the Org structure
// and Screen access titles came to touch the top of the window. A new old-style page must be added here.

/** Older-style pages that need the frame's padding. A trailing "/" means "pages under it, not the list page itself". */
const OLD_STYLE_PAGES = [
  "/admin",
  "/jobs",
  "/notifications",
  "/notices/",
  "/tabular-reviews/",
  "/org-structure",
  "/screen-access",
  "/delegations",
  "/ai-usage",
  "/trademarks",
];

export function needsOldChrome(pathname: string): boolean {
  if (pathname.startsWith("/playbooks/") && pathname !== "/playbooks/build") return true;
  return OLD_STYLE_PAGES.some((page) =>
    page.endsWith("/") ? pathname.startsWith(page) : pathname === page || pathname.startsWith(`${page}/`),
  );
}
