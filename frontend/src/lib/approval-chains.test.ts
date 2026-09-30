import { describe, it, expect } from "vitest";
import {
  OPERATOR_LABELS,
  formatConditionText,
  requirementTone,
  stepProgress,
  nextActionableSequence,
  type ChainRequirementResponseLike,
  type ChainInstanceStepLike,
} from "./approval-chains";

function requirement(
  overrides: Partial<ChainRequirementResponseLike> = {},
): ChainRequirementResponseLike {
  return {
    id: "req-1",
    sequence_order: 1,
    is_base_requirement: true,
    triggered_by_rule_ids: [],
    condition_explanations: [],
    explanation: null,
    status: "pending",
    counts_toward_completion: true,
    superseded_at: null,
    ...overrides,
  };
}

describe("OPERATOR_LABELS", () => {
  it("has a human-readable label for all five fixed FR-2 operators", () => {
    expect(Object.keys(OPERATOR_LABELS).sort()).toEqual(
      ["contains", "eq", "gt", "in", "lt"].sort(),
    );
    expect(OPERATOR_LABELS.gt).toBe("greater than");
    expect(OPERATOR_LABELS.lt).toBe("less than");
    expect(OPERATOR_LABELS.eq).toBe("equal to");
    expect(OPERATOR_LABELS.in).toBe("is one of");
    expect(OPERATOR_LABELS.contains).toBe("contains");
  });
});

describe("formatConditionText", () => {
  it("renders gt with a formatted numeric value (AC-1's worked example)", () => {
    expect(
      formatConditionText({ field: "contract_value", operator: "gt", value: 1000000 }),
    ).toBe("contract_value greater than 1,000,000");
  });

  it("renders lt", () => {
    expect(formatConditionText({ field: "risk_score", operator: "lt", value: 5 })).toBe(
      "risk_score less than 5",
    );
  });

  it("renders eq with a string value", () => {
    expect(
      formatConditionText({ field: "jurisdiction", operator: "eq", value: "EU" }),
    ).toBe("jurisdiction equal to EU");
  });

  it("renders in with a list value", () => {
    expect(
      formatConditionText({
        field: "contract_type",
        operator: "in",
        value: ["NDA", "MSA"],
      }),
    ).toBe("contract_type is one of NDA, MSA");
  });

  it("renders contains", () => {
    expect(
      formatConditionText({ field: "tags", operator: "contains", value: "urgent" }),
    ).toBe("tags contains urgent");
  });
});

describe("requirementTone", () => {
  it("maps approved to green", () => {
    expect(requirementTone(requirement({ status: "approved" }))).toBe("green");
  });

  it("maps pending to amber", () => {
    expect(requirementTone(requirement({ status: "pending" }))).toBe("amber");
  });

  it("maps rejected to red", () => {
    expect(requirementTone(requirement({ status: "rejected" }))).toBe("red");
  });

  it("maps cancelled to neutral", () => {
    expect(requirementTone(requirement({ status: "cancelled" }))).toBe("neutral");
  });
});

describe("stepProgress", () => {
  function step(requirements: ChainRequirementResponseLike[]): ChainInstanceStepLike {
    return { step_id: "step-1", approval_mode: "sequential", requirements };
  }

  it("counts only requirements that count toward completion and are not superseded", () => {
    const s = step([
      requirement({ id: "r1", status: "approved", counts_toward_completion: true, superseded_at: null }),
      requirement({ id: "r2", status: "pending", counts_toward_completion: true, superseded_at: null }),
      // superseded: not counted even though counts_toward_completion is true
      requirement({ id: "r3", status: "approved", counts_toward_completion: true, superseded_at: "2026-01-01T00:00:00Z" }),
      // does not count toward completion at all
      requirement({ id: "r4", status: "rejected", counts_toward_completion: false, superseded_at: null }),
    ]);

    expect(stepProgress(s)).toEqual({ decided: 1, total: 2 });
  });

  it("returns zero/zero for a step with no counted requirements", () => {
    const s = step([
      requirement({ id: "r1", counts_toward_completion: false }),
      requirement({ id: "r2", superseded_at: "2026-01-01T00:00:00Z" }),
    ]);
    expect(stepProgress(s)).toEqual({ decided: 0, total: 0 });
  });

  it("counts rejected as decided too", () => {
    const s = step([requirement({ id: "r1", status: "rejected" })]);
    expect(stepProgress(s)).toEqual({ decided: 1, total: 1 });
  });
});

describe("nextActionableSequence", () => {
  it("returns the lowest sequence_order among pending, counted requirements on a sequential step", () => {
    const s: ChainInstanceStepLike = {
      step_id: "step-1",
      approval_mode: "sequential",
      requirements: [
        requirement({ id: "r1", sequence_order: 1, status: "approved" }),
        requirement({ id: "r2", sequence_order: 2, status: "pending" }),
        requirement({ id: "r3", sequence_order: 3, status: "pending" }),
      ],
    };
    expect(nextActionableSequence(s)).toBe(2);
  });

  it("returns null on a sequential step when nothing is pending", () => {
    const s: ChainInstanceStepLike = {
      step_id: "step-1",
      approval_mode: "sequential",
      requirements: [
        requirement({ id: "r1", sequence_order: 1, status: "approved" }),
        requirement({ id: "r2", sequence_order: 2, status: "rejected" }),
      ],
    };
    expect(nextActionableSequence(s)).toBeNull();
  });

  it("ignores superseded/non-counted pending requirements", () => {
    const s: ChainInstanceStepLike = {
      step_id: "step-1",
      approval_mode: "sequential",
      requirements: [
        requirement({ id: "r1", sequence_order: 1, status: "pending", superseded_at: "2026-01-01T00:00:00Z" }),
        requirement({ id: "r2", sequence_order: 2, status: "pending", counts_toward_completion: false }),
        requirement({ id: "r3", sequence_order: 3, status: "pending" }),
      ],
    };
    expect(nextActionableSequence(s)).toBe(3);
  });

  it("returns null for a parallel step regardless of pending requirements", () => {
    const s: ChainInstanceStepLike = {
      step_id: "step-1",
      approval_mode: "parallel",
      requirements: [requirement({ id: "r1", sequence_order: 1, status: "pending" })],
    };
    expect(nextActionableSequence(s)).toBeNull();
  });
});
