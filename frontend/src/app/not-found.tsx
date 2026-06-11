import Link from "next/link";

export default function NotFound() {
  return (
    <div className="flex min-h-[70vh] flex-col items-center justify-center px-6 text-center">
      <p className="font-serif text-[56px] font-medium leading-none tracking-tight text-slate-900">
        404
      </p>
      <h1 className="mt-4 text-[15px] font-semibold uppercase tracking-[0.16em] text-slate-400">
        Page not found
      </h1>
      <p className="mt-3 max-w-md text-[15px] leading-relaxed text-slate-500">
        The page you’re looking for doesn’t exist or may have been moved.
      </p>
      <Link
        href="/"
        className="mt-7 rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-brand-700"
      >
        Back to assistant
      </Link>
    </div>
  );
}
