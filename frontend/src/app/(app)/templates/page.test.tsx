import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import TemplatesPage from "./page";
import { CHECK_POLL_MS } from "./_check-panel";
import { sha256Hex } from "@/lib/templates";
import type {
  CheckAllResult,
  DraftingTemplate,
  DraftingTemplateVersion,
  TemplateCheck,
  TemplateCheckSummary,
  TemplateMutationResult,
  TemplatePlaybookRef,
  TemplateStatus,
  TemplateStatusState,
} from "@/lib/types";

// The Templates page is where people decide whether our own paper is safe to draft from, and where an edit can
// start a paid AI review or be lost. Failure modes these tests guard: a status shown by colour alone (FR-65), a
// blank page while loading or after an error (FR-60), controls offered to people the server refuses (FR-64),
// Check all running without saying what it costs (FR-17), unsaved wording lost to a template switch, the Back
// button, a refresh or a link (FR-61), a page that calls the rule "locked" when it is only matched by name (D-11),
// and a check whose result describes wording the editor no longer holds.

const h = vi.hoisted(() => ({
  notify: vi.fn(),
  list: vi.fn(),
  get: vi.fn(),
  versions: vi.fn(),
  checks: vi.fn(),
  checkDetail: vi.fn(),
  check: vi.fn(),
  checkAll: vi.fn(),
  save: vi.fn(),
  reset: vi.fn(),
  restore: vi.fn(),
  setPlaybook: vi.fn(),
  options: vi.fn(),
  preview: vi.fn(),
  router: { push: vi.fn(), replace: vi.fn() },
  perms: { current: [] as string[] },
}));

vi.mock("@/lib/endpoints", () => ({
  draftingTemplatesApi: {
    list: h.list, get: h.get, versions: h.versions, checks: h.checks, checkDetail: h.checkDetail, check: h.check,
    checkAll: h.checkAll, save: h.save, reset: h.reset, restore: h.restore, setPlaybook: h.setPlaybook,
    options: h.options, preview: h.preview,
  },
}));
vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ user: { full_name: "Legal One", permissions: h.perms.current } }),
}));
vi.mock("@/components/toast", () => ({ useToast: () => ({ notify: h.notify }) }));
vi.mock("next/navigation", () => ({ useRouter: () => h.router }));

// ---- fixtures ---------------------------------------------------------------------------------------------
const ADMIN = ["playbook:read", "playbook:run", "playbook:update"];
const FIELDS = [
  { name: "company", label: "Our entity", about: "The company that drafts", sample: "Acme Ltd" },
  { name: "counterparty", label: "The other party", about: "The company we contract with", sample: "Globex" },
];
const SAAS_BODY = "Fees: the Provider shall invoice annually in advance, for {company} and {counterparty}.";
const QUOTE = "invoice annually in advance";
const NDA_BODY = "The {counterparty} agrees to keep {company} secrets.";
let SAAS_SHA = "";
beforeAll(async () => {
  SAAS_SHA = await sha256Hex(SAAS_BODY);
});

const WORDS: Record<TemplateStatusState, string> = {
  passes: "Passes", has_findings: "Has findings", checking: "Checking", out_of_date: "Out of date",
  partly_checked: "Partly checked", couldnt_check: "Couldn't check", never_checked: "Never checked", no_playbook: "No playbook",
};
const REASON: Record<TemplateStatusState, string> = {
  passes: "The latest check found nothing.", has_findings: "2 findings, highest High.", checking: "A check is running.",
  out_of_date: "The wording changed since the last check.", partly_checked: "Only part of the template was read.",
  couldnt_check: "The AI review was unavailable.", never_checked: "No check has been made.",
  no_playbook: "No published playbook covers this template.",
};

const run = (over: Partial<TemplateCheckSummary> = {}): TemplateCheckSummary => ({
  id: "c1", state: "completed", trigger: "saved", unsaved: false, template_version: 3, playbook_name: "NDA Playbook",
  playbook_version_number: 2, finding_count: 0, highest_severity: null, text_was_truncated: false, error_code: null,
  error_message: null, requested_by: "Priya Shah", on_behalf: false, created_at: "2026-10-15T08:00:00Z",
  started_at: "2026-10-15T08:00:05Z", finished_at: "2026-10-15T08:01:00Z", ...over,
});
const status = (state: TemplateStatusState, over: Partial<TemplateStatus> = {}): TemplateStatus => ({
  state, label: WORDS[state], reason: REASON[state], finding_count: null, highest_severity: null, changed: null,
  error_code: null, results_vary: false, decided_by: null, last_completed: null, active_check: null, ...over,
});
const pb = (id: string, name: string, version: number, pinned: boolean): TemplatePlaybookRef => ({
  id, name, version_id: `${id}v`, version_number: version, pinned, available: true,
});
const tpl = (over: Partial<DraftingTemplate>): DraftingTemplate => ({
  key: "nda", name: "Non-Disclosure Agreement", label: "Mutual NDA", contract_type: "NDA",
  binding: { form_key: "new_agreement", form_name: "New agreement", agreement_kind: "NDA", label: "New agreement · NDA" },
  playbook: pb("p1", "NDA Playbook", 2, true), shipped: true, active: true, source: "default", version: 0,
  original_filename: null, updated_at: null, updated_by: null, editable: true, status: status("never_checked"),
  body: NDA_BODY, default_body: NDA_BODY, placeholders: FIELDS, fields: [], original_file: null, ...over,
});

const nda = () =>
  tpl({ status: status("passes", { decided_by: run(), last_completed: run({ template_version: 0 }) }) });
const msa = () =>
  tpl({
    key: "msa", name: "Master Services Agreement", contract_type: "MSA",
    binding: { form_key: "new_agreement", form_name: "New agreement", agreement_kind: "MSA", label: "New agreement · MSA" },
    playbook: pb("p2", "MSA Playbook", 1, false), source: "text", version: 3, updated_by: "Priya Shah",
    updated_at: "2026-10-10T09:00:00Z", body: "MSA wording for {company}.",
    status: status("has_findings", { finding_count: 2, highest_severity: "high", results_vary: true }),
  });
const saas = () =>
  tpl({
    key: "saas", name: "Software / SaaS Agreement", contract_type: "SaaS", playbook: pb("p3", "SaaS Playbook", 3, true),
    binding: { form_key: "saas", form_name: "SaaS", agreement_kind: null, label: "SaaS agreements" },
    source: "text", version: 3, body: SAAS_BODY, default_body: SAAS_BODY, status: status("never_checked"),
  });
const vendor = () =>
  tpl({ key: "vendor", name: "Vendor Agreement", playbook: null, status: status("no_playbook"), body: "Vendor wording." });
const dpa = () =>
  tpl({
    key: "dpa", name: "Data Processing Agreement", status: status("checking", { active_check: run({ id: "a1", state: "running" }) }),
    body: "DPA wording.",
  });
const gone = () =>
  tpl({
    key: "gone", name: "Retired-Playbook Agreement",
    playbook: { id: "p7", name: "Old Playbook", version_id: null, version_number: null, pinned: true, available: false },
    status: status("no_playbook"), body: "Gone wording.",
  });
const ALL = () => [nda(), msa(), saas(), vendor(), dpa()];

let details: Record<string, DraftingTemplate> = {};
const finding = {
  index: 0, rule_index: 8, clause_type: "payment_terms", severity: "high" as const,
  issue: "Fees are invoiced a year in advance; the playbook allows at most 20%.",
  quote: QUOTE, quote_template: QUOTE, suggested_fix: "invoice quarterly in arrears", suggested_fix_template: "invoice quarterly in arrears",
  suggestion_is_wording: true, approval_required: true,
};
const saasCheck = (over: Partial<TemplateCheck> = {}): TemplateCheck => ({
  ...run({ playbook_name: "SaaS Playbook", playbook_version_number: 3, finding_count: 1, highest_severity: "high" }),
  template_key: "saas", trigger_detail: null, wording_sha256: SAAS_SHA, playbook_id: "p3", playbook_version_id: "p3v",
  rule_count: 8, findings: [finding], summary: null, text_length: 100, read_length: 100, ...over,
});
/** The SaaS template as the list reports it once a completed check with one finding decides its status. */
const saasWithFinding = () =>
  tpl({
    ...saas(),
    status: status("has_findings", { finding_count: 1, highest_severity: "high", decided_by: run({ id: "c1", playbook_name: "SaaS Playbook", playbook_version_number: 3 }), last_completed: run({ id: "c1", playbook_name: "SaaS Playbook", playbook_version_number: 3 }) }),
  });

function mount(opts: { list?: DraftingTemplate[] | Promise<DraftingTemplate[]>; perms?: string[]; extra?: ReactNode } = {}) {
  h.perms.current = opts.perms ?? ADMIN;
  const list = opts.list ?? ALL();
  if (Array.isArray(list)) {
    for (const t of list) details[t.key] = details[t.key] ?? t;
    h.list.mockResolvedValue(list);
  } else {
    h.list.mockReturnValue(list);
  }
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={qc}>
      {opts.extra}
      <TemplatesPage />
    </QueryClientProvider>,
  );
  return { qc, ...view };
}

const nav = async () => within(await screen.findByRole("navigation", { name: "Templates" }));
const strip = async () => within(await screen.findByRole("group", { name: "Filter templates by status" }));
const heading = (name: string) => screen.findByRole("heading", { level: 2, name });
const headerOf = async (name: string) => (await heading(name)).closest("header") as HTMLElement;
const editor = (name: string) => screen.getByLabelText(`${name} template text`) as HTMLTextAreaElement;
const edit = (name: string, value: string) => fireEvent.change(editor(name), { target: { value } });
async function open(name: RegExp, title: string) {
  fireEvent.click((await (await nav()).findByRole("button", { name })));
  return heading(title);
}
const tab = (name: string) => fireEvent.click(screen.getByRole("tab", { name }));
const SAAS = "Software / SaaS Agreement";
const openSaas = () => open(/^Software \/ SaaS Agreement/, SAAS);
const confirmDialog = async (button: string) => {
  const dialog = await screen.findByRole("dialog");
  fireEvent.click(within(dialog).getByRole("button", { name: button }));
};

beforeEach(() => {
  vi.resetAllMocks();
  window.history.pushState(null, ""); // an ordinary entry under whatever a test arms, so Back lands on it
  details = {};
  h.get.mockImplementation(async (key: string) => {
    const d = details[key];
    if (!d) throw new Error(`no such template ${key}`);
    return d;
  });
  h.versions.mockResolvedValue([]);
  h.checks.mockResolvedValue([]);
  h.options.mockResolvedValue({ bindings: [], playbooks: [] });
  h.preview.mockResolvedValue({ text: "Filled in text." });
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

// ---- FR-60: loading, empty, error ---------------------------------------------------------------------------
describe("the states of the page (FR-60)", () => {
  it("shows a loading state while the list loads, never a blank page", () => {
    mount({ list: new Promise(() => {}) });
    expect(screen.getByRole("heading", { level: 1, name: "Templates" })).toBeInTheDocument();
    expect(screen.getByRole("status", { name: "Loading" })).toBeInTheDocument();
  });

  it("says what to do next when the organisation has no templates", async () => {
    mount({ list: [] });
    expect(await screen.findByText("No templates")).toBeInTheDocument();
    expect(screen.getByText(/Ask an administrator to set the templates up/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Check all" })).toBeNull();
  });

  it("shows an error with a retry when the list cannot be loaded", async () => {
    h.list.mockRejectedValueOnce(new Error("The server hiccuped."));
    mount();
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't load the templates.");
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("navigation", { name: "Templates" })).toBeInTheDocument();
    expect(h.list).toHaveBeenCalledTimes(2);
  });

  it("shows an error with a retry when one template cannot be loaded, and keeps the list", async () => {
    h.get.mockRejectedValueOnce(new Error("boom"));
    mount();
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't load this template.");
    expect(await nav()).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await heading("Non-Disclosure Agreement")).toBeInTheDocument();
  });
});

// ---- FR-1, FR-65: what the list shows ------------------------------------------------------------------------
describe("the list (FR-1, FR-2, FR-65)", () => {
  it("shows each template's status, playbook, binding and wording without opening it", async () => {
    mount();
    const list = await nav();
    const msaRow = list.getByRole("button", { name: /^Master Services Agreement/ });
    expect(msaRow).toHaveTextContent("Has findings");
    expect(msaRow).toHaveTextContent("2 findings, highest High.");
    expect(msaRow).toHaveTextContent("Results vary");
    expect(msaRow).toHaveTextContent("Reviewed by MSA Playbook (version 1)");
    expect(msaRow).toHaveTextContent("Drafts New agreement · MSA");
    expect(msaRow).toHaveTextContent("Edited · v3");
    expect(list.getByRole("button", { name: /^Non-Disclosure Agreement/ })).toHaveTextContent("Shipped wording");
    expect(list.getByRole("button", { name: /^Vendor Agreement/ })).toHaveTextContent("No playbook reviews it");
    expect(list.getAllByRole("button")).toHaveLength(5);
  });

  it("shows every status in words with an icon, not by colour alone", async () => {
    const states = Object.keys(WORDS) as TemplateStatusState[];
    const eight = states.map((s, i) => tpl({ key: `t${i}`, name: `Template ${i}`, status: status(s) }));
    mount({ list: eight });
    const list = await nav();
    for (const s of states) {
      const badge = list.getByText(WORDS[s]);
      expect(badge).toBeInTheDocument();
      expect(badge.querySelector("svg")).not.toBeNull();
    }
  });

  it("marks the open template and lets the keyboard pick another", async () => {
    mount();
    const list = await nav();
    expect(list.getByRole("button", { name: /^Non-Disclosure Agreement/ })).toHaveAttribute("aria-current", "true");
    const msaRow = list.getByRole("button", { name: /^Master Services Agreement/ });
    expect(msaRow.tagName).toBe("BUTTON"); // a real button: Tab reaches it, Enter and Space press it
    fireEvent.click(msaRow);
    expect(await heading("Master Services Agreement")).toBeInTheDocument();
    expect(msaRow).toHaveAttribute("aria-current", "true");
  });
});

// ---- FR-63: counts and the Needs attention filter -------------------------------------------------------------
describe("the status strip (FR-63)", () => {
  it("counts templates by status and narrows to those that need attention", async () => {
    mount();
    const s = await strip();
    const list = await nav();
    expect(s.getByRole("button", { name: "All (5)" })).toHaveAttribute("aria-pressed", "true");
    for (const name of ["Passes (1)", "Has findings (1)", "Checking (1)", "Never checked (1)", "No playbook (1)"]) {
      expect(s.getByRole("button", { name })).toHaveAttribute("aria-pressed", "false");
    }
    expect(s.queryByRole("button", { name: /^Couldn't check/ })).toBeNull(); // none: not offered

    const attention = s.getByRole("button", { name: "Needs attention (2)" });
    fireEvent.click(attention);
    expect(attention).toHaveAttribute("aria-pressed", "true");
    expect(list.getAllByRole("button").map((b) => b.textContent)).toEqual([
      expect.stringContaining("Master Services Agreement"),
      expect.stringContaining("Software / SaaS Agreement"),
    ]);

    fireEvent.click(attention); // pressing it again clears it
    expect(attention).toHaveAttribute("aria-pressed", "false");
    expect(list.getAllByRole("button")).toHaveLength(5);

    fireEvent.click(s.getByRole("button", { name: "Has findings (1)" }));
    expect(list.getAllByRole("button")).toHaveLength(1);
    expect(list.getByRole("button", { name: /^Master Services Agreement/ })).toBeInTheDocument();
  });

  it("keeps the open template open when a filter hides it", async () => {
    mount();
    const s = await strip();
    await heading("Non-Disclosure Agreement");
    fireEvent.click(s.getByRole("button", { name: "Needs attention (2)" }));
    expect(screen.getByRole("heading", { level: 2, name: "Non-Disclosure Agreement" })).toBeInTheDocument();
  });

  it("says so when nothing needs attention, and offers the whole list back", async () => {
    mount({ list: [nda(), vendor()] });
    const s = await strip();
    fireEvent.click(s.getByRole("button", { name: "Needs attention (0)" }));
    expect(screen.getByText("No templates need attention")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show all templates" }));
    expect((await nav()).getAllByRole("button")).toHaveLength(2);
  });
});

// ---- FR-10: what the page must say ----------------------------------------------------------------------------
describe("what the page must say about a check (FR-10)", () => {
  it("states the three things on the page itself, and when and against what the last check ran", async () => {
    mount();
    const header = await headerOf("Non-Disclosure Agreement");
    for (const sentence of [
      /can report different findings on identical wording/,
      /Passes means the latest review found nothing\. It is not a guarantee about any contract/,
      /filled in with example details, as a draft would read\. It does not judge the answers a requester picks/,
    ]) {
      expect(screen.getAllByText(sentence)[0]).toBeVisible();
    }
    expect(within(header).getByText(/^Last checked .+ against NDA Playbook \(version 2\)\.$/)).toBeInTheDocument();
    expect(within(header).getByText("Passes")).toBeInTheDocument();
  });

  it("says plainly when no check has finished", async () => {
    mount();
    const header = await open(/^Software \/ SaaS Agreement/, SAAS).then((el) => el.closest("header") as HTMLElement);
    expect(within(header).getByText("No check has finished yet.")).toBeInTheDocument();
  });
});

// ---- the playbook line, Change playbook (D-11, FR-51) -----------------------------------------------------------
describe("which playbook reviews a template (D-11, FR-51)", () => {
  it("says whether the playbook is locked to the template or matched by name, or why there is none", async () => {
    mount({ list: [...ALL(), gone()] });
    let header = await headerOf("Non-Disclosure Agreement");
    expect(within(header).getByText("Reviewed by NDA Playbook (version 2) · locked to this template")).toBeInTheDocument();
    expect(within(header).getByText("Drafts New agreement · NDA · Shipped wording")).toBeInTheDocument();

    header = (await open(/^Master Services Agreement/, "Master Services Agreement")).closest("header") as HTMLElement;
    expect(
      within(header).getByText("Reviewed by MSA Playbook (version 1) · matched by name; locked at its first check or draft"),
    ).toBeInTheDocument();
    expect(within(header).getByText(/Drafts New agreement · MSA · Edited · v3 · changed by Priya Shah/)).toBeInTheDocument();

    header = (await open(/^Vendor Agreement/, "Vendor Agreement")).closest("header") as HTMLElement;
    expect(within(header).getByText("No playbook reviews it.")).toBeInTheDocument();

    header = (await open(/^Retired-Playbook Agreement/, "Retired-Playbook Agreement")).closest("header") as HTMLElement;
    expect(
      within(header).getByText("Reviewed by Old Playbook, which is deleted or has no published version · locked to this template"),
    ).toBeInTheDocument();
  });

  it("changes the playbook only after saying a paid review starts and old drafts keep theirs", async () => {
    const better = pb("p9", "Better NDA Playbook", 4, true);
    h.options.mockResolvedValue({
      bindings: [],
      playbooks: [{ id: "p1", name: "NDA Playbook", version_number: 2 }, { id: "p9", name: "Better NDA Playbook", version_number: 4 }],
    });
    mount();
    const header = await headerOf("Non-Disclosure Agreement");
    fireEvent.click(within(header).getByRole("button", { name: "Change playbook" }));

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("A paid AI review against the new playbook starts.");
    expect(dialog).toHaveTextContent("Contracts already drafted keep the playbook they were drafted with.");
    const select = await within(dialog).findByRole("combobox");
    expect(select).toHaveValue("p1"); // the one in force
    const confirm = within(dialog).getByRole("button", { name: "Change playbook and start the check" });
    expect(confirm).toBeDisabled(); // already pinned to it: nothing to change
    fireEvent.change(select, { target: { value: "p9" } });
    expect(confirm).toBeEnabled();
    expect(h.setPlaybook).not.toHaveBeenCalled();

    const changed = { ...nda(), playbook: better };
    h.setPlaybook.mockResolvedValue({ template: changed, changed: true, check: run({ id: "n1", state: "queued" }) } satisfies TemplateMutationResult);
    h.list.mockResolvedValue([changed, msa(), saas(), vendor(), dpa()]);
    fireEvent.click(confirm);

    await waitFor(() => expect(h.setPlaybook).toHaveBeenCalledWith("nda", { playbook_id: "p9" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(await within(header).findByText("Reviewed by Better NDA Playbook (version 4) · locked to this template")).toBeInTheDocument();
    expect(h.list.mock.calls.length).toBeGreaterThanOrEqual(2); // the list was read again
    expect(h.notify).toHaveBeenCalledWith(expect.stringContaining("Playbook changed"), "success");
  });

  it("shows the server's reason and keeps the modal open when the change is refused", async () => {
    h.options.mockResolvedValue({ bindings: [], playbooks: [{ id: "p9", name: "Better NDA Playbook", version_number: 4 }] });
    h.setPlaybook.mockRejectedValue(new Error("That playbook has no published version."));
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Change playbook" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(await within(dialog).findByRole("combobox"), { target: { value: "p9" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Change playbook and start the check" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("That playbook has no published version.");
  });
});

// ---- FR-64: who is offered what -----------------------------------------------------------------------------------
describe("who is offered what (FR-64)", () => {
  it("offers change controls to a person who may edit playbooks", async () => {
    mount();
    const header = await headerOf("Non-Disclosure Agreement");
    expect(within(header).getByRole("button", { name: "Change playbook" })).toBeInTheDocument();
    expect(within(header).getByRole("button", { name: /Reset to shipped wording/ })).toBeDisabled(); // already on it
    expect(within(header).queryByText(/needs the permission/)).toBeNull();
    expect(screen.getByRole("button", { name: "Check all" })).toBeInTheDocument();
    expect(editor("Non-Disclosure Agreement")).not.toHaveAttribute("readonly");
  });

  it("gives a viewer who may run reviews no change controls and says why, but keeps checks and history", async () => {
    h.versions.mockResolvedValue([]);
    mount({ perms: ["playbook:read", "playbook:run"] });
    const header = await headerOf("Non-Disclosure Agreement");
    expect(within(header).queryByRole("button", { name: "Change playbook" })).toBeNull();
    expect(within(header).queryByRole("button", { name: /Reset to shipped wording/ })).toBeNull();
    expect(
      within(header).getByText("You can view and check templates; changing them needs the permission to edit playbooks."),
    ).toBeInTheDocument();
    expect(editor("Non-Disclosure Agreement")).toHaveAttribute("readonly");
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
    expect(screen.getByRole("button", { name: "Check all" })).toBeInTheDocument();
    tab("Checks");
    expect(screen.getByRole("button", { name: "Check now (1 paid AI review)" })).toBeEnabled();
    tab("History");
    expect(await screen.findByText("Never edited")).toBeInTheDocument();
  });

  it("gives a viewer who may not run reviews no Check all and no Check now, and says why", async () => {
    mount({ perms: ["playbook:read"] });
    const header = await headerOf("Non-Disclosure Agreement");
    expect(screen.queryByRole("button", { name: "Check all" })).toBeNull();
    expect(within(header).getByText(/checking them needs the permission to run playbook reviews/)).toBeInTheDocument();
    tab("Checks");
    expect(screen.queryByRole("button", { name: /Check now/ })).toBeNull();
    expect(screen.getByText("Running a check needs the permission to run playbook reviews.")).toBeInTheDocument();
    // the statuses and the open template are still shown
    expect(within(header).getByText("Passes")).toBeInTheDocument();
  });
});

// ---- FR-17: Check all --------------------------------------------------------------------------------------------
describe("Check all (FR-17)", () => {
  const started: CheckAllResult = {
    started: [{ key: "nda", check_id: "k1" }, { key: "msa", check_id: "k2" }, { key: "saas", check_id: "k3" }],
    skipped: [{ key: "dpa", reason: "checking" }, { key: "vendor", reason: "no_playbook" }],
  };

  it("states how many paid reviews it runs, names them, and calls nothing until confirmed", async () => {
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Check all" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("This runs 3 paid AI reviews, one for each of these templates:");
    for (const name of ["Non-Disclosure Agreement", "Master Services Agreement", "Software / SaaS Agreement"]) {
      expect(within(dialog).getByText(name)).toBeInTheDocument();
    }
    expect(within(dialog).queryByText("Vendor Agreement")).toBeNull(); // no playbook: left out
    expect(within(dialog).queryByText("Data Processing Agreement")).toBeNull(); // already being checked: left out
    expect(dialog).toHaveTextContent("Templates already being checked or with no playbook are left out.");
    expect(h.checkAll).not.toHaveBeenCalled();

    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(h.checkAll).not.toHaveBeenCalled();
  });

  it("shows k of N finished from the list's own statuses, names a failure, and carries on past it", async () => {
    h.checkAll.mockResolvedValue(started);
    const { qc } = mount();
    fireEvent.click(await screen.findByRole("button", { name: "Check all" }));
    const dialog = await screen.findByRole("dialog");

    const checking = (t: DraftingTemplate) => ({ ...t, status: status("checking", { active_check: run({ id: "x", state: "running" }) }) });
    h.list.mockResolvedValue([checking(nda()), checking(msa()), checking(saas()), vendor(), dpa()]);
    fireEvent.click(within(dialog).getByRole("button", { name: "Run 3 paid AI reviews" }));

    expect(await screen.findByText("Check all: 0 of 3 finished")).toBeInTheDocument();
    expect(h.checkAll).toHaveBeenCalledTimes(1);
    expect(screen.getByText("2 templates left out: already being checked, or no playbook.")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).toBeNull();

    h.list.mockResolvedValue([
      nda(), msa(),
      { ...saas(), status: status("couldnt_check") },
      vendor(), dpa(),
    ]);
    await act(async () => { await qc.invalidateQueries({ queryKey: ["drafting-templates"] }); });

    expect(await screen.findByText("Check all: 3 of 3 finished")).toBeInTheDocument();
    expect(screen.getByText(/Couldn't check: Software \/ SaaS Agreement\. The others carried on\./)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByText(/Check all: 3 of 3 finished/)).toBeNull();
  });

  it("keeps the confirm open and says why when it cannot start", async () => {
    h.checkAll.mockRejectedValue(new Error("The AI spending limit was reached."));
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Check all" }));
    await confirmDialog("Run 3 paid AI reviews");
    expect(await within(await screen.findByRole("dialog")).findByRole("alert")).toHaveTextContent("The AI spending limit was reached.");
  });

  it("offers nothing to run when every template is already being checked or has no playbook", async () => {
    mount({ list: [vendor(), dpa()] });
    fireEvent.click(await screen.findByRole("button", { name: "Check all" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("No template can be checked right now");
    expect(within(dialog).getByRole("button", { name: "Run 0 paid AI reviews" })).toBeDisabled();
  });
});

// ---- FR-5: following a check in the background -------------------------------------------------------------------
describe("polling (FR-5)", () => {
  it("reads the list again every 3 s while any template is being checked, and stops when none is", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mount();
    await heading("Non-Disclosure Agreement");
    expect(h.list).toHaveBeenCalledTimes(1);

    await act(async () => { await vi.advanceTimersByTimeAsync(CHECK_POLL_MS); });
    await waitFor(() => expect(h.list).toHaveBeenCalledTimes(2)); // the Data Processing Agreement is Checking

    h.list.mockResolvedValue([nda(), msa(), saas(), vendor(), { ...dpa(), status: status("passes") }]);
    await act(async () => { await vi.advanceTimersByTimeAsync(CHECK_POLL_MS); });
    await waitFor(() => expect(h.list).toHaveBeenCalledTimes(3));
    expect((await strip()).queryByRole("button", { name: /^Checking/ })).toBeNull();
    await act(async () => { await vi.advanceTimersByTimeAsync(CHECK_POLL_MS * 3); });
    expect(h.list).toHaveBeenCalledTimes(3); // nothing is checking: no more reads
  });

  it("does not poll when nothing is being checked", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mount({ list: [nda(), msa()] });
    await heading("Non-Disclosure Agreement");
    await act(async () => { await vi.advanceTimersByTimeAsync(CHECK_POLL_MS * 3); });
    expect(h.list).toHaveBeenCalledTimes(1);
  });
});

// ---- the tabs, wired to one editor ---------------------------------------------------------------------------------
describe("the tabs share one editor", () => {
  it("moves between tabs with the arrow keys", async () => {
    mount();
    await heading("Non-Disclosure Agreement");
    const tablist = screen.getByRole("tablist", { name: "Template sections" });
    expect(within(tablist).getAllByRole("tab").map((t) => t.textContent)).toEqual(["Wording", "Preview", "Checks", "History"]);
    fireEvent.keyDown(tablist, { key: "ArrowRight" });
    expect(screen.getByRole("tab", { name: "Preview" })).toHaveAttribute("aria-selected", "true");
  });

  it("previews the editor's wording with example values and lists the fields it uses by their plain names", async () => {
    mount();
    await heading("Non-Disclosure Agreement");
    edit("Non-Disclosure Agreement", "«The other party» keeps secrets.");
    tab("Preview");
    expect(await screen.findByText("Filled in text.")).toBeInTheDocument();
    expect(h.preview).toHaveBeenCalledWith("nda", "{counterparty} keeps secrets.");
    expect(screen.getByText(/It shows your unsaved changes\./)).toBeInTheDocument();
    const used = screen.getByText("Fields used in this wording").parentElement as HTMLElement;
    expect(within(used).getByText("The other party")).toBeInTheDocument();
    expect(within(used).queryByText("Our entity")).toBeNull();
  });

  it("shows the server's reason when the wording can't be previewed", async () => {
    h.preview.mockRejectedValue(new Error("Unknown placeholder {nope} (line 1)."));
    mount();
    await heading("Non-Disclosure Agreement");
    tab("Preview");
    expect(await screen.findByRole("alert")).toHaveTextContent("Unknown placeholder {nope} (line 1).");
  });
});

describe("a check and the editor", () => {
  beforeEach(() => {
    h.checks.mockResolvedValue([saasCheck()]);
  });

  it("offers Apply suggestion once, puts it in the editor as an unsaved change, and Discard clears it", async () => {
    mount({ list: [nda(), saasWithFinding()] });
    await openSaas();
    tab("Checks");
    fireEvent.click(await screen.findByRole("button", { name: "Apply suggestion" }));
    expect(h.notify).toHaveBeenCalledWith(expect.stringContaining("Suggestion applied in the editor"), "info");
    expect(screen.queryByRole("button", { name: "Apply suggestion" })).toBeNull(); // once
    expect(screen.getByText(/Applied in the editor\. It is not saved yet/)).toBeInTheDocument();

    tab("Wording");
    expect(editor(SAAS)).toHaveValue(
      "Fees: the Provider shall invoice quarterly in arrears, for «Our entity» and «The other party».",
    );
    expect(screen.getByText("1 suggestion applied from a check; saving records it.")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Discard changes" }));
    expect(editor(SAAS)).toHaveValue("Fees: the Provider shall invoice annually in advance, for «Our entity» and «The other party».");
    tab("Checks");
    expect(await screen.findByRole("button", { name: "Apply suggestion" })).toBeInTheDocument(); // offered again
  });

  it("does not offer Apply suggestion when the passage appears twice", async () => {
    const twice = `${SAAS_BODY} Again: ${QUOTE}.`;
    details.saas = { ...saasWithFinding(), body: twice };
    mount({ list: [nda(), saasWithFinding()] });
    await openSaas();
    tab("Checks");
    expect(await screen.findByText(/The passage appears 2 times in the wording/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Apply suggestion" })).toBeNull();
  });

  it("jumps to the passage in the wording when Show in template is pressed", async () => {
    mount({ list: [nda(), saasWithFinding()] });
    await openSaas();
    tab("Checks");
    fireEvent.click(await screen.findByRole("button", { name: "Show in template" }));
    expect(screen.getByRole("tab", { name: "Wording" })).toHaveAttribute("aria-selected", "true");
    const area = editor(SAAS);
    await waitFor(() => expect(area.selectionStart).toBe(area.value.indexOf(QUOTE)));
    expect(area.selectionEnd).toBe(area.value.indexOf(QUOTE) + QUOTE.length);

    // Coming back to the Wording tab later does not jump again to a place the person has long left.
    tab("Preview");
    tab("Wording");
    expect(editor(SAAS).selectionStart).toBe(editor(SAAS).selectionEnd);
  });

  it("checks the saved wording when the editor is clean: sends no body", async () => {
    h.check.mockResolvedValue(saasCheck({ id: "n1", state: "queued", findings: [], finding_count: null }));
    h.checkDetail.mockResolvedValue(saasCheck({ id: "n1", state: "running", findings: [], finding_count: null }));
    mount({ list: [nda(), saasWithFinding()] });
    await openSaas();
    tab("Checks");
    fireEvent.click(await screen.findByRole("button", { name: "Check now (1 paid AI review)" }));
    await waitFor(() => expect(h.check).toHaveBeenCalledWith("saas", undefined));
  });

  it("checks the editor's wording when it holds unsaved changes, and labels the result unsaved", async () => {
    h.check.mockResolvedValue(saasCheck({ id: "u1", unsaved: true, findings: [], finding_count: 0, highest_severity: null }));
    mount({ list: [nda(), saasWithFinding()] });
    await openSaas();
    edit(SAAS, "Fees: «Our entity» pays «The other party» monthly.");
    tab("Checks");
    fireEvent.click(await screen.findByRole("button", { name: "Check the unsaved wording (1 paid AI review)" }));
    await waitFor(() =>
      expect(h.check).toHaveBeenCalledWith("saas", "Fees: {company} pays {counterparty} monthly."), // codes, not chips
    );
    expect(await screen.findByText("Unsaved wording")).toBeInTheDocument();
  });

  it("offers Try again on Couldn't check, only to a person who may run reviews", async () => {
    const failed = () => ({ ...saas(), status: status("couldnt_check", { error_code: "ai_unavailable" }) });
    h.check.mockResolvedValue(saasCheck({ id: "r1", state: "queued", findings: [], finding_count: null }));
    h.checkDetail.mockResolvedValue(saasCheck({ id: "r1", state: "running", findings: [], finding_count: null }));
    mount({ list: [nda(), failed()] });
    await openSaas();
    tab("Checks");
    fireEvent.click(await screen.findByRole("button", { name: "Try again" }));
    await waitFor(() => expect(h.check).toHaveBeenCalledWith("saas", undefined));
  });

  it("hides Try again from a person who may not run reviews", async () => {
    mount({ perms: ["playbook:read"], list: [nda(), { ...saas(), status: status("couldnt_check") }] });
    await openSaas();
    tab("Checks");
    await screen.findByText(/Status: Couldn't check/);
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
  });
});

// ---- saving, resetting, loading back, conflicts -----------------------------------------------------------------------
describe("changing the wording", () => {
  const saved = (over: Partial<DraftingTemplate> = {}): TemplateMutationResult => ({
    template: { ...saas(), version: 4, body: "Fees: monthly for {company} and {counterparty}.", ...over },
    changed: true,
    check: run({ id: "s1", state: "queued" }),
  });

  it("saves only after saying a paid review starts, sends the version it opened, and is then clean", async () => {
    h.save.mockResolvedValue(saved());
    mount();
    await openSaas();
    edit(SAAS, "Fees: monthly for «Our entity» and «The other party».");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("Saving starts one paid AI review");
    expect(h.save).not.toHaveBeenCalled();
    h.list.mockResolvedValue([nda(), msa(), { ...saas(), version: 4 }, vendor(), dpa()]);
    fireEvent.click(within(dialog).getByRole("button", { name: "Save and start the check" }));

    await waitFor(() =>
      expect(h.save).toHaveBeenCalledWith(
        "saas",
        expect.objectContaining({
          body: "Fees: monthly for {company} and {counterparty}.",
          expected_version: 3,
          applied_findings: [],
        }),
      ),
    );
    const header = await headerOf(SAAS);
    expect(await within(header).findByText(/Edited · v4/)).toBeInTheDocument();
    expect(editor(SAAS)).toHaveValue("Fees: monthly for «Our entity» and «The other party».");
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled(); // clean
    await waitFor(() => expect(h.list.mock.calls.length).toBeGreaterThanOrEqual(2));
  });

  it("sends the suggestions applied from a check with the save (FR-25)", async () => {
    h.checks.mockResolvedValue([saasCheck()]);
    h.save.mockResolvedValue(saved());
    mount({ list: [nda(), saasWithFinding()] });
    await openSaas();
    tab("Checks");
    fireEvent.click(await screen.findByRole("button", { name: "Apply suggestion" }));
    tab("Wording");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await confirmDialog("Save and start the check");
    await waitFor(() =>
      expect(h.save).toHaveBeenCalledWith(
        "saas",
        expect.objectContaining({ applied_findings: [{ check_id: "c1", finding_index: 0 }], expected_version: 3 }),
      ),
    );
  });

  it("shows a colleague's change instead of overwriting it, and Load theirs leaves the editor clean (FR-59)", async () => {
    h.save.mockRejectedValue(Object.assign(new Error("Priya Shah saved version 4 after you opened it."), { status: 409 }));
    mount();
    await openSaas();
    details.saas = { ...saas(), version: 4, body: "Their wording for {company} and {counterparty}." }; // what the 409 fetches
    edit(SAAS, "My wording for «Our entity» and «The other party».");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await confirmDialog("Save and start the check");
    expect(await screen.findByText(/Priya Shah saved version 4 after you opened it\./)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled(); // not until they choose

    fireEvent.click(screen.getByRole("button", { name: "Load theirs into the editor" }));
    expect(editor(SAAS)).toHaveValue("Their wording for «Our entity» and «The other party».");
    expect(h.save).toHaveBeenCalledTimes(1);
    const unload = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(unload);
    expect(unload.defaultPrevented).toBe(false); // theirs is the saved wording now: nothing to lose
  });

  it("resets a shipped template to its shipped wording after confirming, and the editor follows", async () => {
    const reset: TemplateMutationResult = {
      template: { ...msa(), version: 4, source: "default", body: "Shipped MSA for {company}." },
      changed: true,
      check: run({ id: "r1", state: "queued" }),
    };
    h.reset.mockResolvedValue(reset);
    mount();
    await open(/^Master Services Agreement/, "Master Services Agreement");
    edit("Master Services Agreement", "scribbles");
    const header = await headerOf("Master Services Agreement");
    fireEvent.click(within(header).getByRole("button", { name: "Reset to shipped wording" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("Your edited versions stay in History.");
    expect(dialog).toHaveTextContent("The unsaved changes in the editor will be lost.");
    expect(h.reset).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("button", { name: "Reset and start the check" }));

    await waitFor(() => expect(h.reset).toHaveBeenCalledWith("msa", { expected_version: 3 }));
    await waitFor(() => expect(editor("Master Services Agreement")).toHaveValue("Shipped MSA for «Our entity»."));
    expect(h.notify).toHaveBeenCalledWith(expect.stringContaining("Back to the shipped wording"), "success");
  });

  it("loads an earlier version back into the editor and drops what was unsaved", async () => {
    const version = (v: number, body: string): DraftingTemplateVersion => ({
      version: v, note: null, source: "text", is_default: false, body, created_at: "2026-10-10T09:00:00Z", created_by: "Priya Shah",
      original_file: null, applied_findings: [], restored_from_version: null, ocr_confirmed_by: null, ocr_confirmed_at: null, checks: [],
    });
    h.versions.mockResolvedValue([version(3, SAAS_BODY), version(2, "Old wording for {company}.")]);
    h.restore.mockResolvedValue({
      template: { ...saas(), version: 4, body: "Old wording for {company}." }, changed: true, check: run({ id: "b1", state: "queued" }),
    } satisfies TemplateMutationResult);
    mount();
    await openSaas();
    edit(SAAS, "Unsaved scribbles.");
    tab("History");
    fireEvent.click(await screen.findByRole("button", { name: "Load this version back" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("The unsaved changes in the editor will be lost.");
    fireEvent.click(within(dialog).getByRole("button", { name: "Load back and start the check" }));
    await waitFor(() => expect(h.restore).toHaveBeenCalledWith("saas", 2, { expected_version: 3 }));
    tab("Wording");
    expect(editor(SAAS)).toHaveValue("Old wording for «Our entity».");
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });
});

// ---- FR-61: not losing unsaved work -----------------------------------------------------------------------------------
describe("leaving with unsaved changes (FR-61)", () => {
  const WARNING = "You have unsaved changes to this template.";
  const beforeUnload = () => {
    const e = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(e);
    return e.defaultPrevented;
  };

  it("asks before switching to another template, keeps the edit on Cancel, and drops it on confirm", async () => {
    mount();
    await heading("Non-Disclosure Agreement");
    edit("Non-Disclosure Agreement", "My edit.");
    fireEvent.click((await (await nav()).findByRole("button", { name: /^Master Services Agreement/ })));

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent(WARNING);
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByRole("heading", { level: 2, name: "Non-Disclosure Agreement" })).toBeInTheDocument();
    expect(editor("Non-Disclosure Agreement")).toHaveValue("My edit.");

    fireEvent.click((await (await nav()).findByRole("button", { name: /^Master Services Agreement/ })));
    await confirmDialog("Discard and leave");
    expect(await heading("Master Services Agreement")).toBeInTheDocument();
    expect(editor("Master Services Agreement")).toHaveValue("MSA wording for «Our entity».");

    fireEvent.click((await (await nav()).findByRole("button", { name: /^Non-Disclosure Agreement/ })));
    expect(screen.queryByRole("dialog")).toBeNull(); // nothing unsaved now: no question
    await heading("Non-Disclosure Agreement");
    expect(editor("Non-Disclosure Agreement")).toHaveValue("The «The other party» agrees to keep «Our entity» secrets.");
  });

  it("does not ask when nothing is unsaved", async () => {
    mount();
    await heading("Non-Disclosure Agreement");
    fireEvent.click((await (await nav()).findByRole("button", { name: /^Master Services Agreement/ })));
    expect(await heading("Master Services Agreement")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("warns on refresh and close only while there are unsaved changes", async () => {
    mount();
    await heading("Non-Disclosure Agreement");
    expect(beforeUnload()).toBe(false);
    edit("Non-Disclosure Agreement", "My edit.");
    await waitFor(() => expect(beforeUnload()).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "Discard changes" }));
    await waitFor(() => expect(beforeUnload()).toBe(false));
  });

  it("asks before following a link, and goes there only on confirm", async () => {
    mount({ extra: <a href="/contracts">Contracts</a> });
    await heading("Non-Disclosure Agreement");
    const link = screen.getByRole("link", { name: "Contracts" });

    // Nothing unsaved: the click is left alone (the page's own handler here swallows the navigation).
    const seen: boolean[] = [];
    const swallow = (e: Event) => {
      seen.push(e.defaultPrevented);
      e.preventDefault();
    };
    document.addEventListener("click", swallow);
    fireEvent.click(link);
    expect(seen).toEqual([false]);
    expect(screen.queryByRole("dialog")).toBeNull();

    edit("Non-Disclosure Agreement", "My edit.");
    await waitFor(() => expect(beforeUnload()).toBe(true));
    seen.length = 0;
    fireEvent.click(link, { ctrlKey: true }); // open in a new tab: not ours to stop
    expect(seen).toEqual([false]);
    document.removeEventListener("click", swallow);

    expect(fireEvent.click(link)).toBe(false); // stopped
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent(WARNING);
    expect(h.router.replace).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(h.router.replace).not.toHaveBeenCalled();

    fireEvent.click(link);
    await confirmDialog("Discard and leave");
    await waitFor(() => expect(h.router.replace).toHaveBeenCalledWith("/contracts"));
    expect(h.router.push).not.toHaveBeenCalled();
  });

  it("asks when Back is pressed, stays on the page with its text, and goes back two entries only on confirm", async () => {
    const go = vi.spyOn(window.history, "go").mockImplementation(() => {});
    mount();
    await heading("Non-Disclosure Agreement");
    edit("Non-Disclosure Agreement", "My edit.");
    await waitFor(() => expect(window.history.state).toEqual({ tplGuard: expect.any(Number) }));
    const url = window.location.href;

    window.history.back(); // the browser's Back button
    let dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent(WARNING);
    expect(window.location.href).toBe(url);
    expect(window.history.state).toEqual({ tplGuard: expect.any(Number) }); // the entry is back on top: the page stays
    expect(editor("Non-Disclosure Agreement")).toHaveValue("My edit.");
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(go).not.toHaveBeenCalled();

    window.history.back();
    dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Discard and leave" }));
    await waitFor(() => expect(go).toHaveBeenCalledWith(-2)); // over our entry and the page's own
  });

  it("does not trap Back when nothing is unsaved", async () => {
    mount();
    await heading("Non-Disclosure Agreement");
    expect(window.history.state).toBeNull();
    window.history.back();
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("takes its history entry back out when the work is discarded, without asking anything", async () => {
    mount();
    await heading("Non-Disclosure Agreement");
    edit("Non-Disclosure Agreement", "My edit.");
    await waitFor(() => expect(window.history.state).toEqual({ tplGuard: expect.any(Number) }));
    const back = vi.spyOn(window.history, "back");
    fireEvent.click(screen.getByRole("button", { name: "Discard changes" }));
    await waitFor(() => expect(back).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(window.history.state).toBeNull()); // the entry is gone
    expect(screen.queryByRole("dialog")).toBeNull(); // the popstate that caused was not the person pressing Back
    expect(editor("Non-Disclosure Agreement")).toHaveValue("The «The other party» agrees to keep «Our entity» secrets.");
  });

  it("guards Back again on the next edit after an earlier edit was discarded", async () => {
    vi.spyOn(window.history, "go").mockImplementation(() => {});
    mount();
    await heading("Non-Disclosure Agreement");
    edit("Non-Disclosure Agreement", "First edit.");
    await waitFor(() => expect(window.history.state).toEqual({ tplGuard: expect.any(Number) }));
    fireEvent.click(screen.getByRole("button", { name: "Discard changes" }));
    await waitFor(() => expect(window.history.state).toBeNull()); // the first entry is gone, its popstate has landed

    edit("Non-Disclosure Agreement", "Second edit.");
    await waitFor(() => expect(window.history.state).toEqual({ tplGuard: expect.any(Number) }));
    window.history.back();
    expect(await screen.findByRole("dialog")).toHaveTextContent(WARNING); // not swallowed by the first round
    expect(editor("Non-Disclosure Agreement")).toHaveValue("Second edit.");
  });

  it("is not armed by an edit that is typed back to the saved wording", async () => {
    mount();
    await heading("Non-Disclosure Agreement");
    const original = editor("Non-Disclosure Agreement").value;
    edit("Non-Disclosure Agreement", "x");
    await waitFor(() => expect(beforeUnload()).toBe(true));
    edit("Non-Disclosure Agreement", original);
    await waitFor(() => expect(beforeUnload()).toBe(false));
  });
});
