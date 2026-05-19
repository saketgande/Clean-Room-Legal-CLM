"use client";

import { useEffect } from "react";

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div className="flex min-h-[70vh] flex-col items-center justify-center px-6 text-center">
      <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-400">
        Something went wrong
      </p>
      <h1 className="mt-4 font-serif text-[32px] font-medium tracking-tight text-slate-900">
        This page hit an error
      </h1>
      <p className="mt-3 max-w-md text-[15px] leading-relaxed text-slate-500">
        An unexpected error occurred while loading this view. You can retry, or
        head back to the workspace.
      </p>
      <div className="mt-7 flex items-center gap-3">
        <button
          onClick={reset}
          className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-brand-700"
        >
          Try again
        </button>
        <a
          href="/"
          className="rounded-lg border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-700 transition hover:bg-slate-50"
        >
          Go to assistant
        </a>
      </div>
      {error.digest && (
        <p className="mt-6 text-xs text-slate-400">Reference: {error.digest}</p>
      )}
    </div>
  );
}
