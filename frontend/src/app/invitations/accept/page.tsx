"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Scale, Loader2, AlertTriangle } from "lucide-react";
import { useAuth } from "@/lib/auth";
import { authApi } from "@/lib/endpoints";
import { tokenStore } from "@/lib/api";
import { Button, Input, Field } from "@/components/ui";
import { useToast } from "@/components/toast";

function AcceptInvitationForm() {
  const params = useSearchParams();
  const token = params.get("token") ?? "";
  const router = useRouter();
  const { refreshUser } = useAuth();
  const { notify } = useToast();

  const [fullName, setFullName] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);

  const canSubmit =
    !!token && fullName.trim().length >= 2 && password.length >= 10;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    setLoading(true);
    try {
      // Same TokenResponse shape as login — redeem the invite, store the
      // session, hydrate the user, then drop them into the workspace.
      const tokens = await authApi.acceptInvitation(
        token,
        fullName.trim(),
        password,
      );
      tokenStore.set(tokens);
      await refreshUser();
      notify("Welcome to AEGIS", "success");
      router.push("/");
    } catch (err) {
      notify(
        err instanceof Error
          ? err.message
          : "Could not accept the invitation",
        "error",
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="w-full max-w-sm">
      <div className="mb-8 lg:hidden">
        <div className="flex items-center gap-2.5">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-brand-600 text-white">
            <Scale className="h-5 w-5" />
          </div>
          <span className="text-lg font-semibold">AEGIS</span>
        </div>
      </div>

      <h2 className="text-lg font-semibold text-slate-900">
        Accept your invitation
      </h2>
      <p className="mb-6 mt-1 text-sm text-slate-500">
        Set your name and a password to join your organization on AEGIS.
      </p>

      {!token ? (
        <div className="flex items-start gap-3 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>
            This invite link is invalid or incomplete. Ask your administrator
            to resend the invitation.
          </span>
        </div>
      ) : (
        <form onSubmit={submit} className="space-y-4">
          <Field label="Full name">
            <Input
              autoFocus
              required
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              placeholder="Jane Counsel"
            />
          </Field>
          <Field label="Password" hint="Minimum 10 characters">
            <Input
              type="password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••••"
            />
          </Field>
          <Button
            type="submit"
            className="w-full"
            size="lg"
            disabled={loading || !canSubmit}
          >
            {loading && <Loader2 className="h-4 w-4 animate-spin" />}
            Accept &amp; create account
          </Button>
        </form>
      )}

      <p className="mt-5 text-center text-xs text-slate-400">
        Already have an account?{" "}
        <a href="/login" className="text-brand-600 hover:underline">
          Sign in
        </a>
      </p>
    </div>
  );
}

export default function AcceptInvitationPage() {
  return (
    <div className="flex min-h-screen">
      {/* Brand panel — mirrors the login screen for a consistent entry point. */}
      <div className="relative hidden w-1/2 flex-col justify-between bg-slate-900 p-12 text-white lg:flex">
        <div className="flex items-center gap-2.5">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-brand-600">
            <Scale className="h-5 w-5" />
          </div>
          <span className="text-lg font-semibold tracking-tight">AEGIS</span>
        </div>
        <div className="space-y-5">
          <h1 className="text-4xl font-semibold leading-tight tracking-tight">
            You&apos;ve been invited to AEGIS.
          </h1>
          <p className="max-w-md text-slate-300">
            Upload, review, redline, approve, sign and renew — with an AI
            assistant, governed playbooks, and a portfolio-wide Contract Brain.
          </p>
        </div>
        <p className="text-xs text-slate-500">Clean-room Legal CLM platform</p>
      </div>

      {/* Form panel */}
      <div className="flex flex-1 items-center justify-center p-6">
        <Suspense
          fallback={
            <Loader2 className="h-5 w-5 animate-spin text-slate-400" />
          }
        >
          <AcceptInvitationForm />
        </Suspense>
      </div>
    </div>
  );
}
