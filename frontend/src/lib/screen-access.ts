// Pure, dependency-free screen-access helpers + one React hook — FR-9's
// "UI-layer control gating" support. This is a usability aid only (FR-13):
// the API independently re-verifies every ADD/EDIT/DELETE request (FR-10);
// nothing here is, or should ever be treated as, the actual security
// boundary.
//
// NOTE on types: `MyScreenAccessResponse` / `ScreenAccessEntry` /
// `ActionLevelCode` were expected to land in `src/lib/types.ts` via a
// concurrent task (T005). They have landed by the time this was written, so
// they're imported directly rather than re-declared locally.
import { useQuery } from "@tanstack/react-query";
import type { ActionLevelCode, MyScreenAccessResponse, ScreenAccessEntry } from "./types";

// Strict VIEW < ADD < EDIT < DELETE hierarchy (FR-4). Holding a given level
// implies holding every level below it.
export const LEVEL_RANK: Record<ActionLevelCode, number> = {
  VIEW: 1,
  ADD: 2,
  EDIT: 3,
  DELETE: 4,
};

// True iff `resolvedLevel` is high enough to satisfy `required` per the
// fixed hierarchy above. `null`/`undefined` (no grant at all) never
// satisfies any requirement.
export function hasAtLeast(
  resolvedLevel: ActionLevelCode | null | undefined,
  required: ActionLevelCode,
): boolean {
  if (!resolvedLevel) return false;
  return LEVEL_RANK[resolvedLevel] >= LEVEL_RANK[required];
}

// Finds a specific screen's resolved action level out of a
// `MyScreenAccessResponse`-shaped list. Returns null if the screen is
// absent (no grant — below VIEW, per FR-7's "included iff >= VIEW").
export function getScreenLevel(
  access: MyScreenAccessResponse | undefined | null,
  screenCode: string,
): ActionLevelCode | null {
  const entry = access?.screens.find((s) => s.screen_code === screenCode);
  return entry?.action_level ?? null;
}

// Builds the { screenCode -> ActionLevelCode } lookup map used by the hook
// below, exported separately so callers needing the full map (rather than a
// single screen) don't have to re-derive it.
export function buildScreenLevelMap(
  entries: ScreenAccessEntry[] | undefined | null,
): Map<string, ActionLevelCode> {
  const map = new Map<string, ActionLevelCode>();
  for (const entry of entries ?? []) {
    map.set(entry.screen_code, entry.action_level);
  }
  return map;
}

export interface UseScreenAccessResult {
  level: ActionLevelCode | null;
  canView: boolean;
  canAdd: boolean;
  canEdit: boolean;
  canDelete: boolean;
  isLoading: boolean;
}

// React-query-backed hook resolving the caller's own action level for a
// single screen (FR-9). Internal fetch call is `menuApi.myScreenAccess`
// (see plan.md's interface freeze / src/lib/endpoints.ts) — T005 owns
// `endpoints.ts`; if the exact export name there ever changes, only this
// one call site needs reconciling, the public shape below does not.
export function useScreenAccess(screenCode: string): UseScreenAccessResult {
  const { data, isLoading } = useQuery({
    queryKey: ["screen-access", "me", screenCode],
    queryFn: async () => {
      // Imported lazily (dynamic import) so this module never hard-fails to
      // load if `menuApi` hasn't landed in endpoints.ts yet at build time in
      // some intermediate state of a parallel wave — resolves at call time.
      const { menuApi } = await import("./endpoints");
      return menuApi.myScreenAccess({ screen_code: screenCode });
    },
  });

  const level = getScreenLevel(data, screenCode);

  return {
    level,
    canView: hasAtLeast(level, "VIEW"),
    canAdd: hasAtLeast(level, "ADD"),
    canEdit: hasAtLeast(level, "EDIT"),
    canDelete: hasAtLeast(level, "DELETE"),
    isLoading,
  };
}
