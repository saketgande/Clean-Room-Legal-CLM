// Sample data for the "set up a new company" screens. There is no server side for company
// requests yet (the app is single-tenant), so requests live in this browser's localStorage.
// Swap these functions for GET /options, GET /slug-check, POST /requests and the admin
// /requests endpoints when the server pieces exist; the screens only call this file.

export const DOMAIN = "aegis.app";
export const SECTORS = ["Healthcare", "Financial services", "Technology", "Manufacturing", "Retail", "Education", "Government", "Other"];

export type RequestStatus = "waiting" | "creating" | "ready" | "declined" | "failed";
export interface CompanyRequest {
  id: string;
  company: string;
  slug: string;
  sector: string;
  name: string;
  email: string;
  note: string;
  status: RequestStatus;
  createdAt: number;
  approvedAt?: number;
  declineReason?: string;
  setupCode?: string;
  adminSignedUp?: boolean;
}

const KEY = "aegis.onboarding.requests";
const DAILY_LIMIT = 3;
const RESERVED = ["admin", "www", "app", "api", "aegis", "mail", "support"];

function read(): CompanyRequest[] {
  try { return JSON.parse(localStorage.getItem(KEY) ?? "[]"); } catch { return []; }
}
function write(list: CompanyRequest[]) {
  try { localStorage.setItem(KEY, JSON.stringify(list)); } catch { /* storage blocked: the list just won't persist */ }
}

export const slugFrom = (company: string) => company.toLowerCase().replace(/[^a-z0-9]/g, "").slice(0, 24);

export async function slugCheck(slug: string): Promise<{ ok: boolean; reason?: string }> {
  await new Promise((r) => setTimeout(r, 250));
  if (slug.length < 2) return { ok: false, reason: "use at least 2 letters or numbers" };
  if (!/^[a-z0-9]+$/.test(slug)) return { ok: false, reason: "use lowercase letters and numbers only" };
  if (RESERVED.includes(slug)) return { ok: false, reason: "this name is reserved" };
  if (read().some((r) => r.slug === slug && r.status !== "declined")) return { ok: false, reason: "already taken" };
  return { ok: true };
}

export async function submitRequest(v: Pick<CompanyRequest, "company" | "slug" | "sector" | "name" | "email" | "note"> & { website?: string }) {
  await new Promise((r) => setTimeout(r, 400));
  if (v.website) return; // honeypot: bots fill it, people never see it. Pretend it worked.
  const list = read();
  const today = Date.now() - 864e5;
  if (list.filter((r) => r.createdAt > today).length >= DAILY_LIMIT) throw new Error("Too many requests today. Please try again tomorrow.");
  const { website: _w, ...rest } = v;
  write([{ ...rest, id: crypto.randomUUID(), status: "waiting", createdAt: Date.now() }, ...list]);
}

// An approved company takes a few seconds to become ready; reading the list moves it along.
export function listRequests(): CompanyRequest[] {
  const list = read();
  let changed = false;
  for (const r of list) {
    if (r.status === "creating" && Date.now() - (r.approvedAt ?? 0) > 6000) {
      r.status = "ready";
      r.setupCode = Array.from({ length: 3 }, () => Math.random().toString(36).slice(2, 6).toUpperCase()).join("-");
      changed = true;
    }
  }
  if (changed) write(list);
  return list;
}

function patch(id: string, p: Partial<CompanyRequest>) {
  write(read().map((r) => (r.id === id ? { ...r, ...p } : r)));
}
export const approveRequest = (id: string) => patch(id, { status: "creating", approvedAt: Date.now() });
export const declineRequest = (id: string, reason: string) => patch(id, { status: "declined", declineReason: reason });
