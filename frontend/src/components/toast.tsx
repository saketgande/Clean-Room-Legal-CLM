"use client";

import {
  createContext,
  useCallback,
  useContext,
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

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const notify = useCallback((message: string, kind: ToastKind = "info") => {
    const id = ++counter;
    setToasts((t) => [...t, { id, kind, message }]);
    setTimeout(
      () => setToasts((t) => t.filter((x) => x.id !== id)),
      4500,
    );
  }, []);

  const dismiss = (id: number) =>
    setToasts((t) => t.filter((x) => x.id !== id));

  const icons = {
    success: <CheckCircle2 className="h-4 w-4 text-emerald-600" />,
    error: <AlertCircle className="h-4 w-4 text-red-600" />,
    info: <Info className="h-4 w-4 text-brand-600" />,
  };

  return (
    <ToastContext.Provider value={{ notify }}>
      {children}
      <div className="fixed bottom-4 right-4 z-[100] flex flex-col gap-2">
        {toasts.map((t) => (
          <div
            key={t.id}
            className={cn(
              "flex w-80 animate-fade-in items-start gap-3 rounded-lg border bg-white px-4 py-3 shadow-pop",
              t.kind === "error"
                ? "border-red-200"
                : t.kind === "success"
                  ? "border-emerald-200"
                  : "border-slate-200",
            )}
          >
            <div className="mt-0.5">{icons[t.kind]}</div>
            <p className="flex-1 text-sm text-slate-700">{t.message}</p>
            <button
              onClick={() => dismiss(t.id)}
              className="text-slate-400 hover:text-slate-600"
            >
              <X className="h-3.5 w-3.5" />
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
