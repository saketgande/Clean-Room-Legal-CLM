"use client";

import { useEffect, useState } from "react";

/**
 * Reactive dark-mode flag. The theme is the `dark` class on <html> (set
 * pre-paint by the inline script in app/layout.tsx and flipped by
 * ThemeToggle), so charts and other JS-colored surfaces subscribe to class
 * changes rather than reading it once.
 */
export function useIsDark(): boolean {
  const [dark, setDark] = useState(() =>
    typeof document === "undefined"
      ? true
      : document.documentElement.classList.contains("dark"),
  );

  useEffect(() => {
    const el = document.documentElement;
    const read = () => setDark(el.classList.contains("dark"));
    read();
    const obs = new MutationObserver(read);
    obs.observe(el, { attributes: true, attributeFilter: ["class"] });
    return () => obs.disconnect();
  }, []);

  return dark;
}
