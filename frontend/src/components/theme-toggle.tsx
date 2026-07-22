"use client";

import { useEffect, useState } from "react";
import { Moon, Sun } from "lucide-react";

type Theme = "light" | "dark";

/**
 * Light/dark theme toggle. The actual class is applied pre-paint by the inline
 * script in app/layout.tsx (Fluent is light-first, so default light); this
 * button reflects + flips it and persists the choice under "aegis-theme".
 */
export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>("light");
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    const isDark = document.documentElement.classList.contains("dark");
    setTheme(isDark ? "dark" : "light");
    setMounted(true);
  }, []);

  function toggle() {
    const next: Theme = theme === "dark" ? "light" : "dark";
    setTheme(next);
    document.documentElement.classList.toggle("dark", next === "dark");
    try {
      localStorage.setItem("aegis-theme", next);
    } catch {
      /* ignore storage errors (private mode, etc.) */
    }
  }

  const nextLabel = theme === "dark" ? "Switch to light mode" : "Switch to dark mode";

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={nextLabel}
      title={nextLabel}
      className="flex h-9 w-9 shrink-0 items-center justify-center rounded border border-slate-200 text-slate-600 transition-colors hover:bg-slate-100 hover:text-slate-900"
    >
      {/* Render a stable icon until mounted to avoid a hydration mismatch. */}
      {!mounted ? (
        <Sun className="h-4 w-4" />
      ) : theme === "dark" ? (
        <Sun className="h-4 w-4" />
      ) : (
        <Moon className="h-4 w-4" />
      )}
    </button>
  );
}
