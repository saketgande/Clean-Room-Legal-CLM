"use client";

import {
  createContext,
  useCallback,
  useContext,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { CheckCircle2, AlertCircle, Info, X } from "lucide-react";
import { cn } from "@/lib/utils";

type ToastKind = "success" | "error" | "info";
interface Toast {
  id: number;
  kind: ToastKind;
  message: string;
}

const ToastContext = createContext<{
  notify: (message: string, kind?: ToastKind) => void;
} | null>(null);

let counter = 0;
const AUTO_DISMISS_MS = 4500;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const timers = useRef<Map<number, ReturnType<typeof setTimeout>>>(new Map());

  const remove = useCallback((id: number) => {
    setToasts((t) => t.filter((x) => x.id !== id));
    const timer = timers.current.get(id);
    if (timer) clearTimeout(timer);
    timers.current.delete(id);
  }, []);

  const schedule = useCallback(
    (id: number, ms: number) => {
      timers.current.set(
        id,
        setTimeout(() => remove(id), ms),
      );
    },
    [remove],
  );

  const notify = useCallback(
    (message: string, kind: ToastKind = "info") => {
      const id = ++counter;
      setToasts((t) => [...t, { id, kind, message }]);
      schedule(id, AUTO_DISMISS_MS);
    },
    [schedule],
  );

  // Pause auto-dismiss while the user is reading/interacting (WCAG 2.2.1).
  const pause = (id: number) => {
    const timer = timers.current.get(id);
    if (timer) clearTimeout(timer);
    timers.current.delete(id);
  };
  const resume = (id: number) => {
    if (!timers.current.has(id)) schedule(id, AUTO_DISMISS_MS);
  };

  const icons = {
    success: <CheckCircle2 className="h-4 w-4 text-success" aria-hidden="true" />,
    error: <AlertCircle className="h-4 w-4 text-danger" aria-hidden="true" />,
    info: <Info className="h-4 w-4 text-brand-600" aria-hidden="true" />,
  };

  return (
    <ToastContext.Provider value={{ notify }}>
      {children}
      {/* Live region: polite announcements for success/info; errors get
          role="alert" per toast so they interrupt (WCAG 4.1.3). */}
      <div
        aria-live="polite"
        aria-label="Notifications"
        className="fixed bottom-4 right-4 z-[100] flex flex-col gap-2"
      >
        {toasts.map((t) => (
          <div
            key={t.id}
            role={t.kind === "error" ? "alert" : "status"}
            onMouseEnter={() => pause(t.id)}
            onMouseLeave={() => resume(t.id)}
            onFocus={() => pause(t.id)}
            onBlur={() => resume(t.id)}
            className={cn(
              "flex w-80 animate-fade-in items-start gap-3 rounded-md border bg-slate-100 px-4 py-3 shadow-pop",
              t.kind === "error"
                ? "border-danger/30"
                : t.kind === "success"
                  ? "border-success/30"
                  : "border-slate-200",
            )}
          >
            <div className="mt-0.5">{icons[t.kind]}</div>
            <p className="flex-1 text-[13px] text-slate-700">{t.message}</p>
            <button
              onClick={() => remove(t.id)}
              aria-label="Dismiss notification"
              className="-m-2 rounded p-2 text-slate-500 hover:text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-200"
            >
              <X className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used within ToastProvider");
  return ctx;
}
