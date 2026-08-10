"use client";

import {
  type ButtonHTMLAttributes,
  type HTMLAttributes,
  type InputHTMLAttributes,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactElement,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
  type TdHTMLAttributes,
  type ThHTMLAttributes,
  Children,
  cloneElement,
  forwardRef,
  isValidElement,
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";
import Link from "next/link";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Info,
  Loader2,
  X,
  XCircle,
} from "lucide-react";
import { cn } from "@/lib/utils";

// ---- Button --------------------------------------------------------------
type ButtonVariant =
  | "primary"
  | "secondary"
  | "outline"
  | "ghost"
  | "danger";
type ButtonSize = "sm" | "md" | "lg" | "icon";

const buttonVariants: Record<ButtonVariant, string> = {
  primary:
    "bg-brand-600 text-white hover:bg-brand-700 active:bg-brand-800 shadow-sm",
  secondary:
    "bg-slate-900 text-slate-50 hover:bg-slate-800 active:bg-slate-950",
  outline:
    "border border-slate-300 bg-slate-100 text-slate-800 hover:bg-slate-50 hover:border-slate-400",
  ghost: "text-slate-600 hover:bg-slate-100 hover:text-slate-900",
  danger: "bg-danger text-white hover:opacity-90 active:opacity-100",
};

const buttonSizes: Record<ButtonSize, string> = {
  sm: "h-7 px-2.5 text-xs gap-1.5",
  md: "h-8 px-3 text-[13px] gap-2",
  lg: "h-9 px-4 text-[13px] gap-2",
  icon: "h-7 w-7",
};

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  (
    { className, variant = "primary", size = "md", loading, children, disabled, ...props },
    ref,
  ) => (
    <button
      ref={ref}
      disabled={disabled || loading}
      className={cn(
        "inline-flex items-center justify-center rounded-lg font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/35 focus-visible:ring-offset-1 disabled:pointer-events-none disabled:opacity-50",
        buttonVariants[variant],
        buttonSizes[size],
        className,
      )}
      {...props}
    >
      {loading && <Loader2 className="h-4 w-4 animate-spin" />}
      {children}
    </button>
  ),
);
Button.displayName = "Button";

// ---- Card ----------------------------------------------------------------
export function Card({
  className,
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "rounded-xl border border-slate-200 bg-slate-100 shadow-card",
        className,
      )}
      {...props}
    />
  );
}

export function CardHeader({
  className,
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "flex items-center justify-between border-b border-slate-200 px-5 py-4",
        className,
      )}
      {...props}
    />
  );
}

export function CardTitle({
  className,
  ...props
}: HTMLAttributes<HTMLHeadingElement>) {
  return (
    <h3
      className={cn("text-[13px] font-semibold text-slate-900", className)}
      {...props}
    />
  );
}

export function CardBody({
  className,
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("p-5", className)} {...props} />;
}

// ---- Badge ---------------------------------------------------------------
type Tone =
  | "slate"
  | "blue"
  | "green"
  | "amber"
  | "red"
  | "violet"
  | "cyan";

const badgeTones: Record<Tone, string> = {
  slate: "bg-slate-50 text-slate-600",
  blue: "bg-info-subtle text-info",
  green: "bg-success-subtle text-success",
  amber: "bg-warning-subtle text-warning",
  red: "bg-danger-subtle text-danger",
  violet: "bg-brand-50 text-brand-700",
  cyan: "bg-info-subtle text-info",
};

export function Badge({
  tone = "slate",
  className,
  children,
}: {
  tone?: Tone;
  className?: string;
  children: ReactNode;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[11px] font-medium uppercase tracking-[0.04em]",
        badgeTones[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

// ---- Form fields ---------------------------------------------------------
export const Input = forwardRef<
  HTMLInputElement,
  InputHTMLAttributes<HTMLInputElement>
>(({ className, ...props }, ref) => (
  <input
    ref={ref}
    className={cn(
      "h-8 w-full rounded-lg border border-slate-300 bg-slate-100 px-3 text-[13px] text-slate-900 placeholder:text-slate-400 transition-colors focus:border-brand-600 focus:outline-none focus:ring-2 focus:ring-brand-500/35 disabled:bg-slate-50 disabled:text-slate-400",
      className,
    )}
    {...props}
  />
));
Input.displayName = "Input";

export const Textarea = forwardRef<
  HTMLTextAreaElement,
  TextareaHTMLAttributes<HTMLTextAreaElement>
>(({ className, ...props }, ref) => (
  <textarea
    ref={ref}
    className={cn(
      "w-full rounded-lg border border-slate-300 bg-slate-100 px-3 py-2 text-[13px] text-slate-900 placeholder:text-slate-400 transition-colors focus:border-brand-600 focus:outline-none focus:ring-2 focus:ring-brand-500/35",
      className,
    )}
    {...props}
  />
));
Textarea.displayName = "Textarea";

export const Select = forwardRef<
  HTMLSelectElement,
  SelectHTMLAttributes<HTMLSelectElement>
>(({ className, children, ...props }, ref) => (
  <select
    ref={ref}
    className={cn(
      "h-8 w-full rounded-lg border border-slate-300 bg-slate-100 px-3 text-[13px] text-slate-900 transition-colors focus:border-brand-600 focus:outline-none focus:ring-2 focus:ring-brand-500/35",
      className,
    )}
    {...props}
  >
    {children}
  </select>
));
Select.displayName = "Select";

export function Field({
  label,
  hint,
  children,
  className,
}: {
  label?: string;
  hint?: string;
  children: ReactNode;
  className?: string;
}) {
  const autoId = useId();
  const hintId = useId();

  // Programmatically associate label + hint with the control (WCAG 1.3.1 /
  // 3.3.2). When Field wraps a single element, give it an id (unless it has
  // one) and point the hint at it via aria-describedby.
  let controlId: string | undefined;
  let content: ReactNode = children;
  const items = Children.toArray(children);
  if (items.length === 1 && isValidElement(items[0])) {
    const el = items[0] as ReactElement<{
      id?: string;
      "aria-describedby"?: string;
    }>;
    controlId = el.props.id ?? autoId;
    content = cloneElement(el, {
      id: controlId,
      "aria-describedby": hint
        ? (el.props["aria-describedby"] ?? hintId)
        : el.props["aria-describedby"],
    });
  }

  return (
    <div className={cn("space-y-1.5", className)}>
      {label && (
        <label
          htmlFor={controlId}
          className="block text-xs font-medium text-slate-700"
        >
          {label}
        </label>
      )}
      {content}
      {hint && (
        <p id={hintId} className="text-xs text-slate-500">
          {hint}
        </p>
      )}
    </div>
  );
}

// ---- Table ---------------------------------------------------------------
export function Table({
  className,
  ...props
}: HTMLAttributes<HTMLTableElement>) {
  return (
    <div className="overflow-x-auto">
      <table
        className={cn("w-full border-collapse text-[13px]", className)}
        {...props}
      />
    </div>
  );
}

export function THead(props: HTMLAttributes<HTMLTableSectionElement>) {
  return <thead className="bg-slate-50" {...props} />;
}

export function TH({
  className,
  ...props
}: ThHTMLAttributes<HTMLTableCellElement>) {
  return (
    <th
      className={cn(
        "border-b border-slate-300 px-3 py-2 text-left text-[11px] font-medium uppercase tracking-[0.04em] text-slate-500",
        className,
      )}
      {...props}
    />
  );
}

export function TR({
  className,
  ...props
}: HTMLAttributes<HTMLTableRowElement>) {
  return (
    <tr
      className={cn(
        "border-b border-slate-200 last:border-0 hover:bg-slate-100 transition-colors",
        className,
      )}
      {...props}
    />
  );
}

export function TD({
  className,
  ...props
}: TdHTMLAttributes<HTMLTableCellElement>) {
  return (
    <td
      className={cn("px-3 py-2 text-slate-600 align-middle", className)}
      {...props}
    />
  );
}

// ---- Pagination ------------------------------------------------------------
// Client-side page bar: caller owns the current page + slices its own
// already-fetched rows; this just renders the numbered controls.
function pageNumbers(current: number, total: number): (number | "…")[] {
  const delta = 1;
  const start = Math.max(2, current - delta);
  const end = Math.min(total - 1, current + delta);
  const range: (number | "…")[] = [1];
  if (start > 2) range.push("…");
  for (let i = start; i <= end; i++) range.push(i);
  if (end < total - 1) range.push("…");
  if (total > 1) range.push(total);
  return range;
}

export function Pagination({
  page,
  pageCount,
  onPageChange,
  totalItems,
  pageSize,
}: {
  page: number;
  pageCount: number;
  onPageChange: (page: number) => void;
  totalItems?: number;
  pageSize?: number;
}) {
  if (pageCount <= 1) return null;
  return (
    <div className="flex flex-col gap-2 border-t border-slate-100 px-3 py-2.5 sm:flex-row sm:items-center sm:justify-between">
      {totalItems != null && pageSize != null && (
        <div className="text-[11px] text-slate-400">
          Showing {(page - 1) * pageSize + 1}–{Math.min(page * pageSize, totalItems)} of {totalItems}
        </div>
      )}
      <div className="flex items-center gap-1 sm:ml-auto">
        <button
          type="button"
          disabled={page <= 1}
          onClick={() => onPageChange(page - 1)}
          aria-label="Previous page"
          className="rounded-md p-1.5 text-slate-500 hover:bg-slate-100 disabled:pointer-events-none disabled:opacity-30"
        >
          <ChevronLeft className="h-4 w-4" />
        </button>
        {pageNumbers(page, pageCount).map((p, i) =>
          p === "…" ? (
            <span key={`ellipsis-${i}`} className="px-1 text-[11px] text-slate-400">…</span>
          ) : (
            <button
              key={p}
              type="button"
              onClick={() => onPageChange(p)}
              className={cn(
                "min-w-[1.75rem] rounded-md px-1.5 py-1 text-[11px] font-medium tabular-nums transition-colors",
                p === page
                  ? "bg-brand-50 text-brand-700 ring-1 ring-brand-200"
                  : "text-slate-500 hover:bg-slate-100",
              )}
            >
              {p}
            </button>
          ),
        )}
        <button
          type="button"
          disabled={page >= pageCount}
          onClick={() => onPageChange(page + 1)}
          aria-label="Next page"
          className="rounded-md p-1.5 text-slate-500 hover:bg-slate-100 disabled:pointer-events-none disabled:opacity-30"
        >
          <ChevronRight className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}

// ---- Spinner / states ----------------------------------------------------
export function Spinner({ className }: { className?: string }) {
  return (
    <Loader2
      aria-hidden="true"
      className={cn("h-5 w-5 animate-spin text-brand-600", className)}
    />
  );
}

export function CenterSpinner({ label }: { label?: string }) {
  return (
    <div
      role="status"
      className="flex flex-col items-center justify-center gap-3 py-12 text-slate-500"
    >
      <Spinner className="h-6 w-6" />
      {label ? (
        <p className="text-sm">{label}</p>
      ) : (
        <span className="sr-only">Loading</span>
      )}
    </div>
  );
}

export function EmptyState({
  icon,
  title,
  description,
  action,
}: {
  icon?: ReactNode;
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center rounded-md border border-dashed border-slate-300 bg-slate-50 py-10 text-center">
      {icon && (
        <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-lg bg-slate-100 text-slate-400">
          {icon}
        </div>
      )}
      <p className="text-base font-semibold text-slate-900">{title}</p>
      {description && (
        <p className="mt-1.5 max-w-sm text-sm text-slate-500">{description}</p>
      )}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function ErrorState({ error }: { error: unknown }) {
  const message =
    error instanceof Error ? error.message : "Something went wrong";
  return (
    <div
      role="alert"
      className="rounded-md border border-danger/30 bg-danger-subtle p-4 text-[13px] text-danger"
    >
      {message}
    </div>
  );
}

// ---- MessageBar ------------------------------------------------------------
type MessageBarIntent = "info" | "success" | "warning" | "error";

const messageBarStyles: Record<MessageBarIntent, string> = {
  info: "border-info/30 bg-info-subtle text-info",
  success: "border-success/30 bg-success-subtle text-success",
  warning: "border-warning/30 bg-warning-subtle text-warning",
  error: "border-danger/30 bg-danger-subtle text-danger",
};

const messageBarIcons: Record<MessageBarIntent, ReactNode> = {
  info: <Info className="h-4 w-4 shrink-0" />,
  success: <CheckCircle2 className="h-4 w-4 shrink-0" />,
  warning: <AlertTriangle className="h-4 w-4 shrink-0" />,
  error: <XCircle className="h-4 w-4 shrink-0" />,
};

export function MessageBar({
  intent = "info",
  className,
  children,
}: {
  intent?: MessageBarIntent;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div
      role={intent === "error" ? "alert" : "status"}
      className={cn(
        "flex items-start gap-2 rounded-md border p-3 text-[13px] leading-5",
        messageBarStyles[intent],
        className,
      )}
    >
      {messageBarIcons[intent]}
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        "animate-pulse rounded-md bg-slate-200",
        className,
      )}
    />
  );
}

/** Content-shaped loading placeholder for list/table screens — reads as "the
 * rows are coming" instead of a blank pane with a spinner. */
export function SkeletonRows({ rows = 6 }: { rows?: number }) {
  return (
    <div className="space-y-2" role="status" aria-label="Loading">
      {Array.from({ length: rows }).map((_, i) => (
        <div
          key={i}
          className="flex items-center gap-4 rounded-md border border-slate-200 bg-slate-100 px-4 py-3.5"
        >
          <Skeleton className="h-4 w-4 rounded-full" />
          <Skeleton className="h-4 max-w-[28rem] flex-1" />
          <Skeleton className="hidden h-4 w-24 sm:block" />
          <Skeleton className="hidden h-4 w-16 md:block" />
          <Skeleton className="h-5 w-14 rounded-full" />
        </div>
      ))}
    </div>
  );
}

// ---- Breadcrumbs -----------------------------------------------------------
/** Fluent 2 breadcrumb trail for detail routes. The last item is the current
 * page (aria-current); earlier items link back up the hierarchy. */
export function Breadcrumbs({
  items,
  className,
}: {
  items: { label: string; href?: string }[];
  className?: string;
}) {
  return (
    <nav aria-label="Breadcrumb" className={cn("mb-3", className)}>
      <ol className="flex flex-wrap items-center gap-1 text-sm">
        {items.map((item, i) => {
          const last = i === items.length - 1;
          return (
            <li key={`${item.label}-${i}`} className="flex min-w-0 items-center gap-1">
              {item.href && !last ? (
                <Link
                  href={item.href}
                  className="rounded px-1 py-0.5 text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
                >
                  {item.label}
                </Link>
              ) : (
                <span
                  aria-current={last ? "page" : undefined}
                  className={cn(
                    "truncate px-1 py-0.5",
                    last ? "font-medium text-slate-900" : "text-slate-500",
                  )}
                >
                  {item.label}
                </span>
              )}
              {!last && (
                <ChevronRight
                  aria-hidden="true"
                  className="h-3.5 w-3.5 flex-none text-slate-400"
                />
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

// ---- Page header ---------------------------------------------------------
export function PageHeader({
  title,
  description,
  eyebrow,
  actions,
}: {
  title: string;
  description?: string;
  eyebrow?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
      <div>
        {eyebrow && (
          <p className="mb-1 text-[11px] font-medium uppercase tracking-[0.08em] text-brand-700">
            {eyebrow}
          </p>
        )}
        <h1 className="text-[20px] font-semibold leading-tight tracking-[-0.01em] text-slate-900">
          {title}
        </h1>
        {description && (
          <p className="mt-1 text-[13px] text-slate-500">{description}</p>
        )}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}

// ---- Modal ---------------------------------------------------------------
export function Modal({
  open,
  onClose,
  title,
  children,
  footer,
  size = "md",
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  footer?: ReactNode;
  size?: "sm" | "md" | "lg" | "xl";
}) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const titleId = useId();

  // Focus management (WCAG 2.4.3 / 2.1.2): move focus into the dialog on
  // open, trap Tab inside it, and return focus to the trigger on close.
  useEffect(() => {
    if (!open) return;
    const previouslyFocused = document.activeElement as HTMLElement | null;
    const dialog = dialogRef.current;
    const focusables = () =>
      Array.from(
        dialog?.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ) ?? [],
      );
    (focusables()[0] ?? dialog)?.focus();

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onClose();
        return;
      }
      if (e.key !== "Tab") return;
      const els = focusables();
      if (els.length === 0) return;
      const first = els[0];
      const last = els[els.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      previouslyFocused?.focus?.();
    };
  }, [open, onClose]);

  if (!open) return null;
  const widths = {
    sm: "max-w-sm",
    md: "max-w-lg",
    lg: "max-w-2xl",
    xl: "max-w-4xl",
  };
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div
        className="absolute inset-0 bg-slate-950/40 backdrop-blur-sm"
        onClick={onClose}
        aria-hidden="true"
      />
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className={cn(
          "relative z-10 w-full animate-fade-in rounded-2xl border border-slate-200 bg-slate-100 shadow-modal focus:outline-none",
          widths[size],
        )}
      >
        <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
          <h2 id={titleId} className="text-sm font-semibold text-slate-900">
            {title}
          </h2>
          <button
            onClick={onClose}
            aria-label="Close dialog"
            className="rounded p-2 text-slate-500 hover:bg-slate-100 hover:text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>
        <div className="max-h-[70vh] overflow-y-auto px-5 py-4">{children}</div>
        {footer && (
          <div className="flex items-center justify-end gap-2 border-t border-slate-200 px-4 py-3">
            {footer}
          </div>
        )}
      </div>
    </div>
  );
}

// ---- Confirm dialog --------------------------------------------------------
// An in-app replacement for `window.confirm()` — a destructive action gated
// only by the native dialog silently fails closed for any session with
// native dialogs suppressed (locked-down browsers, some extensions), with no
// visible error. `confirm()` is a drop-in swap for `window.confirm()` at each
// call site (same `if (!(await confirm(...))) return;` shape); render
// `dialog` once per component to mount it.
export function useConfirm() {
  const [state, setState] = useState<{
    title: string;
    message: string;
    confirmLabel?: string;
    tone?: "danger" | "primary";
    resolve: (ok: boolean) => void;
  } | null>(null);

  const confirm = useCallback(
    (opts: {
      title: string;
      message: string;
      confirmLabel?: string;
      tone?: "danger" | "primary";
    }) => new Promise<boolean>((resolve) => setState({ ...opts, resolve })),
    [],
  );

  const settle = (ok: boolean) => {
    state?.resolve(ok);
    setState(null);
  };

  const dialog = state && (
    <Modal
      open
      onClose={() => settle(false)}
      title={state.title}
      footer={
        <>
          <Button variant="outline" onClick={() => settle(false)}>
            Cancel
          </Button>
          <Button
            variant={state.tone === "danger" ? "danger" : "primary"}
            onClick={() => settle(true)}
          >
            {state.confirmLabel ?? "Confirm"}
          </Button>
        </>
      }
    >
      <p className="text-sm text-slate-600">{state.message}</p>
    </Modal>
  );

  return { confirm, dialog };
}

// ---- Stat card -----------------------------------------------------------
export function StatCard({
  label,
  value,
  hint,
  icon,
  tone = "blue",
}: {
  label: string;
  value: ReactNode;
  hint?: string;
  icon?: ReactNode;
  tone?: Tone;
}) {
  const iconTones: Record<Tone, string> = {
    slate: "bg-slate-50 text-slate-600",
    blue: "bg-brand-50 text-brand-600",
    green: "bg-success-subtle text-success",
    amber: "bg-warning-subtle text-warning",
    red: "bg-danger-subtle text-danger",
    violet: "bg-brand-50 text-brand-600",
    cyan: "bg-info-subtle text-info",
  };
  return (
    <Card className="p-4">
      <div className="flex items-start justify-between">
        <div>
          <p className="text-[11px] font-medium uppercase tracking-[0.06em] text-slate-500">
            {label}
          </p>
          <p className="mt-1.5 text-[28px] font-semibold tracking-tight tabular-nums text-slate-900">
            {value}
          </p>
          {hint && <p className="mt-1 text-xs text-slate-500">{hint}</p>}
        </div>
        {icon && (
          <div
            className={cn(
              "flex h-9 w-9 items-center justify-center rounded-md",
              iconTones[tone],
            )}
          >
            {icon}
          </div>
        )}
      </div>
    </Card>
  );
}

// ---- Tabs ----------------------------------------------------------------
export function Tabs({
  tabs,
  active,
  onChange,
  label,
}: {
  tabs: { id: string; label: string; count?: number }[];
  active: string;
  onChange: (id: string) => void;
  label?: string;
}) {
  // Roving tabindex + arrow-key navigation per the WAI-ARIA tabs pattern
  // (WCAG 2.1.1 / 4.1.2).
  const onKeyDown = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    e.preventDefault();
    const idx = tabs.findIndex((t) => t.id === active);
    const delta = e.key === "ArrowRight" ? 1 : -1;
    const next = tabs[(idx + delta + tabs.length) % tabs.length];
    onChange(next.id);
    const el = e.currentTarget.querySelector<HTMLElement>(
      `[data-tab-id="${next.id}"]`,
    );
    el?.focus();
  };

  return (
    <div
      role="tablist"
      aria-label={label}
      onKeyDown={onKeyDown}
      className="flex gap-1 border-b border-slate-200"
    >
      {tabs.map((t) => (
        <button
          key={t.id}
          data-tab-id={t.id}
          role="tab"
          aria-selected={active === t.id}
          tabIndex={active === t.id ? 0 : -1}
          onClick={() => onChange(t.id)}
          className={cn(
            "relative -mb-px px-3 py-2 text-[13px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/35",
            active === t.id
              ? "border-b-2 border-brand-600 text-slate-900"
              : "border-b-2 border-transparent text-slate-500 hover:text-slate-800",
          )}
        >
          {t.label}
          {t.count !== undefined && (
            <span className="ml-1.5 rounded-full bg-slate-50 px-1.5 py-0.5 text-xs text-slate-500">
              {t.count}
            </span>
          )}
        </button>
      ))}
    </div>
  );
}
