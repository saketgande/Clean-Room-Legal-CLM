import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  canDeleteComment,
  commentQuote,
  CommentsPanel,
  CommentsTab,
  openCommentCount,
  orderComments,
  type CommentsPanelProps,
} from "./_comments-tab";
import type { ContractComment } from "@/lib/types";

// The Comments tab: every comment on the contract, with the ways to write, resolve, reopen and delete one.
// The view (CommentsPanel) is tested on its own; the tab (CommentsTab) is tested with the server calls
// stood in, including what happens when the server says no.

const h = vi.hoisted(() => ({
  notify: vi.fn(),
  user: { id: "u-me", permissions: [] as string[] },
  contractsApi: { comments: vi.fn(), addComment: vi.fn(), resolveComment: vi.fn(), deleteComment: vi.fn() },
}));

vi.mock("@/lib/endpoints", () => ({ contractsApi: h.contractsApi }));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: h.user }) }));
vi.mock("@/components/toast", () => ({ useToast: () => ({ notify: h.notify }) }));

const comment = (id: string, over: Partial<ContractComment> = {}): ContractComment => ({
  id, contract_id: "c-1", contract_version_id: null, parent_comment_id: null,
  visibility: "internal", author_kind: "user", author_user_id: "u-other", author_name: "Priya Rao",
  body: `Body of ${id}`, anchor: null, mentioned_user_ids: [], resolved: false, resolved_at: null,
  created_at: "2026-10-01T10:00:00Z", ...over,
});

// ---- small helpers ----------------------------------------------------------------

describe("the helpers", () => {
  it("reads the quoted passage of a comment written on text, and nothing from one that was not", () => {
    expect(commentQuote(comment("a", { anchor: { quote: "  thirty days  " } }))).toBe("thirty days");
    expect(commentQuote(comment("b", { anchor: { quote: "   " } }))).toBeNull();
    expect(commentQuote(comment("c", { anchor: { start: 1, end: 4 } }))).toBeNull();
    expect(commentQuote(comment("d", { anchor: null }))).toBeNull();
    expect(commentQuote(comment("e", { anchor: { quote: 7 } }))).toBeNull();
  });

  it("puts the open comments first and the resolved last, each in the order they came", () => {
    const list = [comment("1", { resolved: true }), comment("2"), comment("3", { resolved: true }), comment("4")];
    expect(orderComments(list).map((c) => c.id)).toEqual(["2", "4", "1", "3"]);
    expect(list.map((c) => c.id)).toEqual(["1", "2", "3", "4"]); // the input is left alone
  });

  it("counts the open ones, and 0 before the list has loaded", () => {
    expect(openCommentCount([comment("1"), comment("2", { resolved: true }), comment("3")])).toBe(2);
    expect(openCommentCount(undefined)).toBe(0);
  });

  it("lets the author, an editor, or the all-permissions grant delete, and nobody else", () => {
    const mine = comment("m", { author_user_id: "u-me" });
    const theirs = comment("t", { author_user_id: "u-other" });
    const counterparty = comment("x", { author_user_id: null, author_kind: "counterparty" });
    expect(canDeleteComment(mine, { id: "u-me", permissions: [] })).toBe(true);
    expect(canDeleteComment(theirs, { id: "u-me", permissions: [] })).toBe(false);
    expect(canDeleteComment(theirs, { id: "u-me", permissions: ["contract:update"] })).toBe(true);
    expect(canDeleteComment(counterparty, { id: "u-me", permissions: ["*"] })).toBe(true);
    expect(canDeleteComment(counterparty, { id: "u-me", permissions: ["contract:read"] })).toBe(false);
    expect(canDeleteComment(mine, null)).toBe(false);
  });
});

// ---- the view -----------------------------------------------------------------------

function panel(over: Partial<CommentsPanelProps> = {}) {
  const props: CommentsPanelProps = {
    comments: [],
    loading: false,
    error: null,
    onRetry: vi.fn(),
    pendingIds: new Set(),
    posting: false,
    canDelete: () => true,
    onPost: vi.fn().mockResolvedValue(true),
    onToggleResolved: vi.fn(),
    onDelete: vi.fn(),
    onShowPassage: vi.fn(),
    ...over,
  };
  const view = render(<CommentsPanel {...props} />);
  return { props, ...view };
}

const items = () => within(screen.getByRole("list", { name: "Comments" })).getAllByRole("listitem");
const item = (id: string) => items().find((li) => li.getAttribute("data-comment-id") === id) as HTMLElement;

describe("CommentsPanel: what is shown", () => {
  it("says so when there are no comments, and still offers the box to write one", () => {
    panel();
    expect(screen.getByText("No comments yet.")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Add a comment" })).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Comments" })).toBeNull();
  });

  it("shows a spinner while the comments load, and no empty message", () => {
    panel({ loading: true });
    expect(screen.getByRole("status")).toHaveTextContent("Loading comments");
    expect(screen.queryByText("No comments yet.")).toBeNull();
  });

  it("says the comments could not be loaded, and tries again on request", () => {
    const { props } = panel({ error: new Error("network down") });
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.queryByText("No comments yet.")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(props.onRetry).toHaveBeenCalledTimes(1);
  });

  it("lists open comments first, then resolved ones, and says how many of each", () => {
    panel({ comments: [comment("a", { resolved: true }), comment("b"), comment("c")] });
    expect(items().map((li) => li.getAttribute("data-comment-id"))).toEqual(["b", "c", "a"]);
    expect(screen.getByText("2 open · 1 resolved")).toBeInTheDocument();
  });

  it("says everything is resolved when nothing is open", () => {
    panel({ comments: [comment("a", { resolved: true })] });
    expect(screen.getByText("Everything is resolved · 1 resolved")).toBeInTheDocument();
  });

  it("shows who wrote it, who can see it, and whether it is resolved", () => {
    panel({
      comments: [
        comment("int"),
        comment("sh", { visibility: "shared", author_name: "Alex" }),
        comment("cp", { visibility: "shared", author_kind: "counterparty", author_user_id: null, author_name: "Acme Legal" }),
        comment("rs", { resolved: true }),
      ],
    });
    expect(within(item("int")).getByText("Priya Rao")).toBeInTheDocument();
    expect(within(item("int")).getByText("Internal")).toBeInTheDocument();
    expect(within(item("sh")).getByText("Shared")).toBeInTheDocument();
    expect(within(item("cp")).getByText("Counterparty")).toBeInTheDocument();
    expect(within(item("cp")).getByText("Acme Legal")).toBeInTheDocument();
    expect(within(item("rs")).getByText("Resolved")).toBeInTheDocument();
    expect(within(item("int")).queryByText("Resolved")).toBeNull();
    expect(within(item("int")).queryByText("Counterparty")).toBeNull();
  });

  it("keeps the line breaks of what was written", () => {
    panel({ comments: [comment("a", { body: "line one\nline two" })] });
    expect(item("a").querySelector(".cmx-body")?.textContent).toBe("line one\nline two");
  });
});

describe("CommentsPanel: a comment written on a passage", () => {
  const anchored = comment("a", { anchor: { quote: "within thirty days" } });

  it("shows the passage, and asks for it to be shown in the contract when it is clicked", () => {
    const { props } = panel({ comments: [anchored] });
    fireEvent.click(within(item("a")).getByRole("button", { name: /within thirty days/ }));
    expect(props.onShowPassage).toHaveBeenCalledWith(anchored);
  });

  it("shows the passage of a resolved comment as plain text, since the contract no longer marks it", () => {
    panel({ comments: [{ ...anchored, resolved: true }] });
    expect(within(item("a")).getByText(/within thirty days/)).toBeInTheDocument();
    expect(within(item("a")).queryByRole("button", { name: /within thirty days/ })).toBeNull();
  });

  it("shows no passage for a comment that was not written on one", () => {
    panel({ comments: [comment("b")] });
    expect(item("b").querySelector(".cmx-quote")).toBeNull();
  });
});

describe("CommentsPanel: resolving and deleting", () => {
  it("offers Resolve on an open comment and Reopen on a resolved one", () => {
    const { props } = panel({ comments: [comment("a"), comment("b", { resolved: true })] });
    fireEvent.click(within(item("a")).getByRole("button", { name: "Resolve" }));
    expect(props.onToggleResolved).toHaveBeenLastCalledWith(expect.objectContaining({ id: "a" }));
    fireEvent.click(within(item("b")).getByRole("button", { name: "Reopen" }));
    expect(props.onToggleResolved).toHaveBeenLastCalledWith(expect.objectContaining({ id: "b" }));
  });

  it("asks before deleting, and deletes only on the second click", () => {
    const { props } = panel({ comments: [comment("a")] });
    fireEvent.click(within(item("a")).getByRole("button", { name: "Delete" }));
    expect(props.onDelete).not.toHaveBeenCalled();
    expect(within(item("a")).getByText("Delete this comment?")).toBeInTheDocument();
    fireEvent.click(within(item("a")).getByRole("button", { name: "Yes, delete" }));
    expect(props.onDelete).toHaveBeenCalledTimes(1);
    expect(props.onDelete).toHaveBeenCalledWith(expect.objectContaining({ id: "a" }));
  });

  it("backs out of a delete without deleting", () => {
    const { props } = panel({ comments: [comment("a")] });
    fireEvent.click(within(item("a")).getByRole("button", { name: "Delete" }));
    fireEvent.click(within(item("a")).getByRole("button", { name: "Cancel" }));
    expect(props.onDelete).not.toHaveBeenCalled();
    expect(within(item("a")).getByRole("button", { name: "Delete" })).toBeInTheDocument();
  });

  it("does not offer Delete on a comment the person may not delete", () => {
    panel({ comments: [comment("a"), comment("b")], canDelete: (c) => c.id === "b" });
    expect(within(item("a")).queryByRole("button", { name: "Delete" })).toBeNull();
    expect(within(item("b")).getByRole("button", { name: "Delete" })).toBeInTheDocument();
    expect(within(item("a")).getByRole("button", { name: "Resolve" })).toBeInTheDocument();
  });

  it("holds the buttons of a comment while a request for it is in flight, and no other", () => {
    panel({ comments: [comment("a"), comment("b")], pendingIds: new Set(["a"]) });
    expect(within(item("a")).getByRole("button", { name: "Resolve" })).toBeDisabled();
    expect(within(item("a")).getByRole("button", { name: "Delete" })).toBeDisabled();
    expect(within(item("b")).getByRole("button", { name: "Resolve" })).toBeEnabled();
  });
});

describe("CommentsPanel: writing a comment", () => {
  const box = () => screen.getByRole("textbox", { name: "Add a comment" });
  const post = () => screen.getByRole("button", { name: "Comment" });

  it("cannot post nothing, or only spaces", () => {
    panel();
    expect(post()).toBeDisabled();
    fireEvent.change(box(), { target: { value: "   " } });
    expect(post()).toBeDisabled();
  });

  it("posts what was written, trimmed, as internal by default, and clears the box", async () => {
    const { props } = panel();
    fireEvent.change(box(), { target: { value: "  Please check the cap.  " } });
    fireEvent.click(post());
    await waitFor(() => expect(props.onPost).toHaveBeenCalledWith("Please check the cap.", "internal"));
    await waitFor(() => expect(box()).toHaveValue(""));
  });

  it("posts as shared when Shared is chosen, and says what that means", async () => {
    const { props } = panel();
    expect(screen.getByText(/Internal: only your team sees it/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Shared" }));
    expect(screen.getByRole("button", { name: "Shared" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Internal" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByText(/Shared: the other side can see it/)).toBeInTheDocument();
    fireEvent.change(box(), { target: { value: "For them" } });
    fireEvent.click(post());
    await waitFor(() => expect(props.onPost).toHaveBeenCalledWith("For them", "shared"));
  });

  it("keeps what was written when the comment could not be posted", async () => {
    const { props } = panel({ onPost: vi.fn().mockResolvedValue(false) });
    fireEvent.change(box(), { target: { value: "Do not lose me" } });
    fireEvent.click(post());
    await waitFor(() => expect(props.onPost).toHaveBeenCalled());
    expect(box()).toHaveValue("Do not lose me");
  });

  it("posts with Ctrl+Enter or Cmd+Enter", async () => {
    const { props } = panel();
    fireEvent.change(box(), { target: { value: "Quick one" } });
    fireEvent.keyDown(box(), { key: "Enter", ctrlKey: true });
    await waitFor(() => expect(props.onPost).toHaveBeenCalledWith("Quick one", "internal"));
  });

  it("does not post a second time while the first is going", () => {
    const { props } = panel({ posting: true });
    fireEvent.change(box(), { target: { value: "Once" } });
    expect(screen.getByRole("button", { name: "Posting…" })).toBeDisabled();
    fireEvent.keyDown(box(), { key: "Enter", metaKey: true });
    expect(props.onPost).not.toHaveBeenCalled();
  });
});

describe("CommentsPanel: bringing a comment into view", () => {
  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn();
  });

  it("scrolls to the asked-for comment, marks it for a moment, and tells the page it has done so", () => {
    vi.useFakeTimers();
    try {
      const onFocused = vi.fn();
      panel({ comments: [comment("a"), comment("b")], focusId: "b", onFocused });
      expect(Element.prototype.scrollIntoView).toHaveBeenCalledTimes(1);
      expect((Element.prototype.scrollIntoView as ReturnType<typeof vi.fn>).mock.contexts[0]).toBe(item("b"));
      expect(item("b")).toHaveClass("flash");
      expect(item("a")).not.toHaveClass("flash");
      expect(onFocused).toHaveBeenCalledTimes(1);
      act(() => { vi.advanceTimersByTime(2000); });
      expect(item("b")).not.toHaveClass("flash");
    } finally {
      vi.useRealTimers();
    }
  });

  it("waits for the comment to be on the list before it tries", () => {
    const onFocused = vi.fn();
    const { rerender, props } = panel({ comments: [comment("a")], focusId: "new", onFocused });
    expect(onFocused).not.toHaveBeenCalled();
    rerender(<CommentsPanel {...props} comments={[comment("a"), comment("new")]} focusId="new" onFocused={onFocused} />);
    expect(onFocused).toHaveBeenCalledTimes(1);
    expect(Element.prototype.scrollIntoView).toHaveBeenCalledTimes(1);
  });

  it("does nothing when no comment is asked for", () => {
    const onFocused = vi.fn();
    panel({ comments: [comment("a")], onFocused });
    expect(Element.prototype.scrollIntoView).not.toHaveBeenCalled();
    expect(onFocused).not.toHaveBeenCalled();
  });
});

// ---- the tab: data and server calls -------------------------------------------------

function mount(props: Partial<{ focusId: string | null; onFocused: () => void }> = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidate = vi.spyOn(qc, "invalidateQueries");
  render(
    <QueryClientProvider client={qc}>
      <CommentsTab contractId="c-1" {...props} />
    </QueryClientProvider>,
  );
  return { qc, invalidate };
}

const keysOf = (spy: { mock: { calls: unknown[][] } }) => spy.mock.calls.map((c) => JSON.stringify((c[0] as { queryKey: unknown[] }).queryKey));

// A stand-in for the server's list that changes when a comment is resolved or deleted, as the real one does.
// The tab asks for the list again after each change, so a list that never changed would undo it.
function serverWith(list: ContractComment[]) {
  let rows = [...list];
  h.contractsApi.comments.mockImplementation(async () => rows.map((r) => ({ ...r })));
  h.contractsApi.resolveComment.mockImplementation(async (_c: string, id: string, resolved: boolean) => {
    rows = rows.map((r) => (r.id === id ? { ...r, resolved } : r));
    return rows.find((r) => r.id === id);
  });
  h.contractsApi.deleteComment.mockImplementation(async (_c: string, id: string) => {
    rows = rows.filter((r) => r.id !== id);
    return { status: "deleted", id };
  });
}

beforeEach(() => {
  vi.resetAllMocks();
  h.user = { id: "u-me", permissions: [] };
  serverWith([comment("a"), comment("b", { author_user_id: "u-me" })]);
});

describe("CommentsTab: loading", () => {
  it("shows this contract's comments", async () => {
    mount();
    expect(await screen.findByText("Body of a")).toBeInTheDocument();
    expect(h.contractsApi.comments).toHaveBeenCalledWith("c-1");
    expect(screen.getByText("Body of b")).toBeInTheDocument();
  });

  it("says why when the comments cannot be loaded, and loads again on request", async () => {
    h.contractsApi.comments.mockRejectedValueOnce(new Error("boom"));
    mount();
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Body of a")).toBeInTheDocument();
    expect(h.contractsApi.comments).toHaveBeenCalledTimes(2);
  });

  it("keeps showing the comments it has when a later refresh fails", async () => {
    const { qc } = mount();
    await screen.findByText("Body of a");
    h.contractsApi.comments.mockRejectedValue(new Error("offline"));
    await act(async () => { await qc.refetchQueries({ queryKey: ["contract-comments", "c-1"] }); });
    expect(screen.getByText("Body of a")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows only the Delete buttons the person may use", async () => {
    mount();
    await screen.findByText("Body of a");
    const a = screen.getByText("Body of a").closest("li") as HTMLElement;
    const b = screen.getByText("Body of b").closest("li") as HTMLElement;
    expect(within(a).queryByRole("button", { name: "Delete" })).toBeNull(); // someone else's, and no edit right
    expect(within(b).getByRole("button", { name: "Delete" })).toBeInTheDocument(); // their own
  });

  it("shows every Delete button to someone who can edit the contract", async () => {
    h.user = { id: "u-me", permissions: ["contract:update"] };
    mount();
    await screen.findByText("Body of a");
    expect(screen.getAllByRole("button", { name: "Delete" })).toHaveLength(2);
  });
});

describe("CommentsTab: writing", () => {
  it("sends the comment with who may see it, shows it, and refreshes the list and the review status", async () => {
    h.contractsApi.addComment.mockResolvedValue(comment("n", { body: "Fresh one", visibility: "shared", author_user_id: "u-me" }));
    const { invalidate } = mount();
    await screen.findByText("Body of a");
    fireEvent.click(screen.getByRole("button", { name: "Shared" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Add a comment" }), { target: { value: "Fresh one" } });
    fireEvent.click(screen.getByRole("button", { name: "Comment" }));
    await waitFor(() => expect(h.contractsApi.addComment).toHaveBeenCalledWith("c-1", { body: "Fresh one", visibility: "shared" }));
    expect(await screen.findByText("Fresh one")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Add a comment" })).toHaveValue("");
    await waitFor(() => expect(keysOf(invalidate)).toEqual(expect.arrayContaining([
      JSON.stringify(["contract-comments", "c-1"]), JSON.stringify(["review-status", "c-1"]),
    ])));
  });

  it("shows the server's reason and keeps the text when the comment is refused", async () => {
    h.contractsApi.addComment.mockRejectedValue(new Error("Comment body is required"));
    mount();
    await screen.findByText("Body of a");
    fireEvent.change(screen.getByRole("textbox", { name: "Add a comment" }), { target: { value: "Try this" } });
    fireEvent.click(screen.getByRole("button", { name: "Comment" }));
    await waitFor(() => expect(h.notify).toHaveBeenCalledWith("Comment body is required", "error"));
    expect(screen.getByRole("textbox", { name: "Add a comment" })).toHaveValue("Try this");
  });

  it("sends a double click once", async () => {
    let finish!: (c: ContractComment) => void;
    h.contractsApi.addComment.mockReturnValue(new Promise<ContractComment>((r) => { finish = r; }));
    mount();
    await screen.findByText("Body of a");
    fireEvent.change(screen.getByRole("textbox", { name: "Add a comment" }), { target: { value: "Once" } });
    const post = screen.getByRole("button", { name: "Comment" });
    fireEvent.click(post);
    fireEvent.click(post);
    await waitFor(() => expect(h.contractsApi.addComment).toHaveBeenCalledTimes(1));
    await act(async () => { finish(comment("n", { body: "Once" })); });
  });
});

describe("CommentsTab: resolving", () => {
  it("resolves a comment, moves it below the open ones, and refreshes the review status", async () => {
    const { invalidate } = mount();
    await screen.findByText("Body of a");
    const a = screen.getByText("Body of a").closest("li") as HTMLElement;
    fireEvent.click(within(a).getByRole("button", { name: "Resolve" }));
    await waitFor(() => expect(h.contractsApi.resolveComment).toHaveBeenCalledWith("c-1", "a", true));
    await waitFor(() => expect(within(screen.getByText("Body of a").closest("li") as HTMLElement).getByText("Resolved")).toBeInTheDocument());
    expect(items().map((li) => li.getAttribute("data-comment-id"))).toEqual(["b", "a"]);
    await waitFor(() => expect(keysOf(invalidate)).toContain(JSON.stringify(["review-status", "c-1"])));
  });

  it("reopens a resolved comment", async () => {
    serverWith([comment("a", { resolved: true })]);
    mount();
    await screen.findByText("Body of a");
    fireEvent.click(screen.getByRole("button", { name: "Reopen" }));
    await waitFor(() => expect(h.contractsApi.resolveComment).toHaveBeenCalledWith("c-1", "a", false));
    await waitFor(() => expect(within(screen.getByText("Body of a").closest("li") as HTMLElement).queryByText("Resolved")).toBeNull());
    expect(within(screen.getByText("Body of a").closest("li") as HTMLElement).getByRole("button", { name: "Resolve" })).toBeInTheDocument();
  });

  it("puts the comment back and says why when the server refuses, even if asking for the list again also fails", async () => {
    h.contractsApi.resolveComment.mockRejectedValue(new Error("Comment not found"));
    mount();
    await screen.findByText("Body of a");
    h.contractsApi.comments.mockRejectedValue(new Error("offline")); // so only the put-back can undo the change
    const a = screen.getByText("Body of a").closest("li") as HTMLElement;
    fireEvent.click(within(a).getByRole("button", { name: "Resolve" }));
    await waitFor(() => expect(h.notify).toHaveBeenCalledWith("Comment not found", "error"));
    const after = screen.getByText("Body of a").closest("li") as HTMLElement;
    expect(within(after).queryByText("Resolved")).toBeNull();
    expect(within(after).getByRole("button", { name: "Resolve" })).toBeEnabled();
  });

  it("sends one request for a double click", async () => {
    let finish!: (c: ContractComment) => void;
    h.contractsApi.resolveComment.mockReturnValue(new Promise<ContractComment>((r) => { finish = r; }));
    mount();
    await screen.findByText("Body of a");
    const button = within(screen.getByText("Body of a").closest("li") as HTMLElement).getByRole("button", { name: "Resolve" });
    fireEvent.click(button);
    fireEvent.click(button);
    expect(h.contractsApi.resolveComment).toHaveBeenCalledTimes(1);
    await act(async () => { finish(comment("a", { resolved: true })); });
  });
});

describe("CommentsTab: deleting", () => {
  it("deletes after the second click, and the comment goes", async () => {
    mount();
    await screen.findByText("Body of b");
    const b = screen.getByText("Body of b").closest("li") as HTMLElement;
    fireEvent.click(within(b).getByRole("button", { name: "Delete" }));
    expect(h.contractsApi.deleteComment).not.toHaveBeenCalled();
    fireEvent.click(within(b).getByRole("button", { name: "Yes, delete" }));
    await waitFor(() => expect(h.contractsApi.deleteComment).toHaveBeenCalledWith("c-1", "b"));
    await waitFor(() => expect(screen.queryByText("Body of b")).toBeNull());
    expect(screen.getByText("Body of a")).toBeInTheDocument();
  });

  it("brings the comment back and says why when the server refuses, even if asking for the list again also fails", async () => {
    h.contractsApi.deleteComment.mockRejectedValue(new Error("Only the author or an editor can delete this comment"));
    mount();
    await screen.findByText("Body of b");
    h.contractsApi.comments.mockRejectedValue(new Error("offline")); // so only the put-back can undo the change
    const b = screen.getByText("Body of b").closest("li") as HTMLElement;
    fireEvent.click(within(b).getByRole("button", { name: "Delete" }));
    fireEvent.click(within(b).getByRole("button", { name: "Yes, delete" }));
    await waitFor(() => expect(h.notify).toHaveBeenCalledWith("Only the author or an editor can delete this comment", "error"));
    expect(screen.getByText("Body of b")).toBeInTheDocument();
  });
});

describe("CommentsTab: showing a passage in the contract", () => {
  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn();
    h.contractsApi.comments.mockResolvedValue([comment("a", { anchor: { quote: "within thirty days" } })]);
  });

  it("scrolls the contract to the text the comment is on", async () => {
    const mark = document.createElement("mark");
    mark.id = "anchor-comment-a";
    document.body.appendChild(mark);
    try {
      mount();
      fireEvent.click(await screen.findByRole("button", { name: /within thirty days/ }));
      expect(Element.prototype.scrollIntoView).toHaveBeenCalledTimes(1);
      expect((Element.prototype.scrollIntoView as ReturnType<typeof vi.fn>).mock.contexts[0]).toBe(mark);
      expect(h.notify).not.toHaveBeenCalled();
    } finally {
      mark.remove();
    }
  });

  it("says so, instead of doing nothing, when the text is not on screen", async () => {
    mount();
    fireEvent.click(await screen.findByRole("button", { name: /within thirty days/ }));
    expect(h.notify).toHaveBeenCalledWith(expect.stringMatching(/not on screen/), "info");
    expect(Element.prototype.scrollIntoView).not.toHaveBeenCalled();
  });
});
