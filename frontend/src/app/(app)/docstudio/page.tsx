import Link from "next/link";
import { FileText } from "lucide-react";
import { MOCK_DOCUMENTS } from "@/features/docstudio/api";
import { PageHeader } from "@/components/ui";

export default function DocstudioIndex() {
  return (
    <div className="p-6">
      <PageHeader
        title="Docstudio"
        description="The document workspace. These five are real client documents, parsed — their clause ids, pages and boxes come from the ingest that read them."
      />
      <div className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {MOCK_DOCUMENTS.map((doc) => (
          <Link
            key={doc.slug}
            href={`/docstudio/${doc.slug}`}
            className="group flex items-start gap-3 rounded-lg border border-slate-200 bg-slate-100 p-4 transition-colors hover:border-brand-300 hover:bg-brand-50"
          >
            <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded bg-slate-200 text-slate-600 group-hover:bg-brand-100 group-hover:text-brand-700">
              <FileText className="h-4 w-4" />
            </span>
            <span className="min-w-0">
              <span className="block truncate text-sm font-medium text-slate-900">{doc.label}</span>
              <span className="block text-xs text-slate-500">{doc.note}</span>
            </span>
          </Link>
        ))}
      </div>
    </div>
  );
}
