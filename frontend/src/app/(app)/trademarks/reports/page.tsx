"use client";

import { Card, CardBody, CardHeader, CardTitle, MessageBar, PageHeader, Select, Table, TD, TH, THead, TR } from "@/components/ui";

// Illustrative portfolio analytics — no live analytics backend exists for
// this yet. Mirrors the source POC's Reports page.
const STATUS_DISTRIBUTION = [
  { status: "Registered", count: 182, share: "58%" },
  { status: "Filed / pending", count: 71, share: "23%" },
  { status: "Opposed", count: 19, share: "6%" },
  { status: "Abandoned", count: 24, share: "8%" },
  { status: "Renewed", count: 16, share: "5%" },
];

const ENFORCEMENT_ROI = [
  { action: "Oppositions filed", count: 12, outcome: "9 favorable", cost: "$46,200" },
  { action: "Cancellations pursued", count: 4, outcome: "2 favorable", cost: "$18,900" },
  { action: "Infringement actions", count: 6, outcome: "5 favorable", cost: "$61,400" },
];

export default function TrademarkReportsPage() {
  return (
    <div className="space-y-4">
      <PageHeader
        title="Reports"
        description="Portfolio analytics and enforcement ROI. Illustrative — connect a reporting pipeline for live figures."
      />

      <MessageBar intent="info">
        This page shows illustrative sample data. It is not wired to live
        portfolio analytics.
      </MessageBar>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Status distribution</CardTitle>
          </CardHeader>
          <CardBody className="p-0">
            <Table>
              <THead>
                <tr>
                  <TH>Status</TH>
                  <TH>Count</TH>
                  <TH>Share</TH>
                </tr>
              </THead>
              <tbody>
                {STATUS_DISTRIBUTION.map((row) => (
                  <TR key={row.status}>
                    <TD className="font-medium text-slate-900">{row.status}</TD>
                    <TD>{row.count}</TD>
                    <TD>{row.share}</TD>
                  </TR>
                ))}
              </tbody>
            </Table>
          </CardBody>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Enforcement ROI</CardTitle>
          </CardHeader>
          <CardBody className="p-0">
            <Table>
              <THead>
                <tr>
                  <TH>Action</TH>
                  <TH>Count</TH>
                  <TH>Outcome</TH>
                  <TH>Cost</TH>
                </tr>
              </THead>
              <tbody>
                {ENFORCEMENT_ROI.map((row) => (
                  <TR key={row.action}>
                    <TD className="font-medium text-slate-900">{row.action}</TD>
                    <TD>{row.count}</TD>
                    <TD>{row.outcome}</TD>
                    <TD>{row.cost}</TD>
                  </TR>
                ))}
              </tbody>
            </Table>
          </CardBody>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Compliance export</CardTitle>
        </CardHeader>
        <CardBody className="flex items-center gap-3">
          <Select className="max-w-xs" defaultValue="all">
            <option value="all">All jurisdictions</option>
            <option value="IN">India</option>
            <option value="US">United States</option>
            <option value="EU">European Union</option>
          </Select>
          <span className="text-[13px] text-slate-500">Export not yet available.</span>
        </CardBody>
      </Card>
    </div>
  );
}
