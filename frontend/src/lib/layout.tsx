"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";

interface LayoutState {
  /** Desktop: render the nav as a slim icon rail instead of full width. */
  collapsed: boolean;
  setCollapsed: (v: boolean) => void;
  /** Mobile: off-canvas nav drawer open. */
  mobileOpen: boolean;
  setMobileOpen: (v: boolean) => void;
  /** Pages (e.g. the assistant workspace) force the chrome collapsed while mounted. */
  forceCollapsed: boolean;
  setForceCollapsed: (v: boolean) => void;
}

const LayoutContext = createContext<LayoutState | null>(null);

const KEY = "aegis.nav_collapsed";

export function LayoutProvider({ children }: { children: ReactNode }) {
  const [collapsed, setCollapsedState] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [forceCollapsed, setForceCollapsed] = useState(false);

  useEffect(() => {
    if (typeof window === "undefined") return;
    setCollapsedState(window.localStorage.getItem(KEY) === "1");
  }, []);

  const setCollapsed = useCallback((v: boolean) => {
    setCollapsedState(v);
    if (typeof window !== "undefined")
      window.localStorage.setItem(KEY, v ? "1" : "0");
  }, []);

  return (
    <LayoutContext.Provider
      value={{
        collapsed,
        setCollapsed,
        mobileOpen,
        setMobileOpen,
        forceCollapsed,
        setForceCollapsed,
      }}
    >
      {children}
    </LayoutContext.Provider>
  );
}

export function useLayout() {
  const ctx = useContext(LayoutContext);
  if (!ctx) throw new Error("useLayout must be used within LayoutProvider");
  return ctx;
}

/**
 * Mount-scoped helper: collapses the desktop nav to an icon rail while a
 * document/workspace page is open, then restores it on leave.
 */
export function useCollapseChrome() {
  const { setForceCollapsed } = useLayout();
  useEffect(() => {
    setForceCollapsed(true);
    return () => setForceCollapsed(false);
  }, [setForceCollapsed]);
}
