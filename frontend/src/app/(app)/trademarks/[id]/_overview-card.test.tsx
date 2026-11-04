import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import type { Trademark } from "@/lib/types";
import { OverviewCard } from "./_overview-card";

// One wide card: the mark, where it is filed, who asked, and the record's dates.

const trademark = {
  id: "t-1", name: "ZQ", description: "Insect repellent spray", goods_services: "Repellent preparations",
  nice_class: "5", jurisdiction: "IN", jurisdictions: ["IN", "US"], trademark_type: "word_mark",
  filed_on: null, renewal_due_on: null, created_at: "2026-10-12T10:00:00Z", updated_at: "2026-10-13T10:00:00Z",
  filing_context: { business_unit: "Consumer Health", business_unit_spoc: "Ravi Kumar" },
  case: { request_id: "q-1", request_ref: "REQ-4016", assignee_name: "Legal One" },
} as unknown as Trademark;

describe("OverviewCard", () => {
  it("shows the mark's details and the requisition together", () => {
    render(<OverviewCard trademark={trademark} />);
    for (const text of ["Insect repellent spray", "Repellent preparations", "IN, US", "Consumer Health", "Ravi Kumar", "Word Mark"]) {
      expect(screen.getByText(text)).toBeInTheDocument();
    }
  });

  it("names the Legal Intake request and the step owner as plain text, not a link back to the request", () => {
    render(<OverviewCard trademark={trademark} />);
    expect(screen.getByText("REQ-4016")).toBeInTheDocument();
    expect(screen.getByText("Legal One")).toBeInTheDocument();
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("shows the next date and what is due beside it, once the LTE has set one", () => {
    render(
      <OverviewCard
        trademark={{ ...trademark, next_action_on: "2026-12-01T00:00:00Z", next_action_note: "Hearing" } as unknown as Trademark}
      />,
    );
    expect(screen.getByText("Next date")).toBeInTheDocument();
    expect(screen.getByText(/· Hearing/)).toBeInTheDocument();
  });

  it("shows a dash for what is not filled in, and leaves out the request fields for a mark with no case", () => {
    render(<OverviewCard trademark={{ ...trademark, case: null, filing_context: null, goods_services: null } as unknown as Trademark} />);
    expect(screen.queryByText("Legal Intake request")).toBeNull();
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });
});
