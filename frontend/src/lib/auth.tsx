"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { HttpError, NO_REPLY_STATUS, tokenStore } from "./api";
import { authApi } from "./endpoints";
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

/** How long to wait before asking "who am I" again when the server did not answer. */
const AUTH_RETRY_MS = 4000;

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<UserResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const router = useRouter();
  const queryClient = useQueryClient();

  const retryTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const refreshUser = useCallback(async () => {
    if (!tokenStore.access) {
      setUser(null);
      setLoading(false);
      return;
    }
    try {
      setUser(await authApi.me());
      setLoading(false);
    } catch (err) {
      if (err instanceof HttpError && err.status === NO_REPLY_STATUS) {
        // The server did not answer at all. That says nothing about whether this person is signed in, so do
        // not send them to the login page: keep waiting (the banner explains) and ask again shortly.
        retryTimer.current = setTimeout(() => void refreshUser(), AUTH_RETRY_MS);
        return;
      }
      setUser(null);
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refreshUser();
    return () => {
      if (retryTimer.current) clearTimeout(retryTimer.current);
    };
  }, [refreshUser]);

  const login = useCallback(
    async (email: string, password: string) => {
      queryClient.clear();
      const tokens = await authApi.login(email, password);
      tokenStore.set(tokens);
      const me = await authApi.me();
      setUser(me);
      router.push("/intake");
    },
    [queryClient, router],
  );

  const logout = useCallback(async () => {
    queryClient.clear();
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
  }, [queryClient, router]);

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
