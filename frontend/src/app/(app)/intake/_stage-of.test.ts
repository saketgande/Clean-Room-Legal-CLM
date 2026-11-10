import { describe, expect, it } from "vitest";
import { stageOf } from "./_legal-intake";
import type { IntakeRequest } from "@/lib/types";

const req = (workflow: { label: string; stage: string; done: boolean; active: boolean }[], status = "open") =>
  ({ workflow, status, stage: "x" }) as unknown as IntakeRequest;

describe("stageOf", () => {
  it("is the stage of the step running now", () => {
    expect(stageOf(req([{ label: "a", stage: "drafting", done: true, active: false }, { label: "b", stage: "review", done: false, active: true }]))).toBe("review");
  });
  it("is Intake while no workflow has been chosen, so the Stage filter finds new requests", () => {
    expect(stageOf(req([]))).toBe("intake");
  });
  it("is Active once every step is finished on an open request, not the stage of the last step it happened to do", () => {
    expect(stageOf(req([{ label: "a", stage: "review", done: true, active: false }]))).toBe("active");
  });
});
