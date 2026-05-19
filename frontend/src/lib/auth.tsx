"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { useRouter } from "next/navigation";
import { tokenStore } from "./api";
import { authApi } from "./endpoints";
import { disableDemo, isDemo } from "./demo";
import type { UserResponse } from "./types";

interface AuthState {
  user: UserResponse | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  refreshUser: () => Promise<void>;
  switchRole: (roleId?: string, roleName?: string) => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<UserResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const router = useRouter();

  const refreshUser = useCallback(async () => {
    if (!isDemo() && !tokenStore.access) {
      setUser(null);
      setLoading(false);
      return;
    }
    try {
      setUser(await authApi.me());
    } catch {
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refreshUser();
  }, [refreshUser]);

  const login = useCallback(
    async (email: string, password: string) => {
      const tokens = await authApi.login(email, password);
      tokenStore.set(tokens);
      const me = await authApi.me();
      setUser(me);
      router.push("/");
    },
    [router],
  );

  const logout = useCallback(async () => {
    if (isDemo()) {
      disableDemo();
      setUser(null);
      router.push("/login");
      return;
    }
    try {
      // Refresh token now lives in an HttpOnly cookie; the backend reads it
      // directly from the request, so we don't pass it explicitly any more.
      await authApi.logout();
    } catch {
      /* ignore */
    }
    tokenStore.clear();
    setUser(null);
    router.push("/login");
  }, [router]);

  const switchRole = useCallback(
    async (roleId?: string, roleName?: string) => {
      const updated = await authApi.switchRole(roleId, roleName);
      setUser(updated);
    },
    [],
  );

  return (
    <AuthContext.Provider
      value={{ user, loading, login, logout, refreshUser, switchRole }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
