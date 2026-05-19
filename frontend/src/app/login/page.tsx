"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Scale, Loader2 } from "lucide-react";
import { useAuth } from "@/lib/auth";
import { authApi } from "@/lib/endpoints";
import { tokenStore } from "@/lib/api";
import { enableDemo } from "@/lib/demo";
import { Button, Input, Field } from "@/components/ui";
import { useToast } from "@/components/toast";
import { cn } from "@/lib/utils";

type Mode = "login" | "register" | "setup";

export default function LoginPage() {
  const { login, refreshUser } = useAuth();
  const { notify } = useToast();
  const router = useRouter();

  async function exploreDemo() {
    enableDemo();
    await refreshUser();
    router.push("/");
  }
  const [mode, setMode] = useState<Mode>("login");
  const [loading, setLoading] = useState(false);

  // shared
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  // setup
  const [orgName, setOrgName] = useState("");
  const [orgSlug, setOrgSlug] = useState("");
  const [setupToken, setSetupToken] = useState("local-setup-token");
  const [allowedDomains, setAllowedDomains] = useState("");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    try {
      if (mode === "login") {
        await login(email, password);
      } else if (mode === "register") {
        const res = await authApi.register(email, fullName, password);
        notify(res.message || "Registration submitted", "success");
        setMode("login");
      } else {
        await authApi.setupFirstAdmin({
          setup_token: setupToken,
          organization_name: orgName,
          organization_slug: orgSlug,
          allowed_domains: allowedDomains
            ? allowedDomains.split(",").map((d) => d.trim()).filter(Boolean)
            : undefined,
          email,
          full_name: fullName,
          password,
        });
        const tokens = await authApi.login(email, password);
        tokenStore.set(tokens);
        notify("Organization created", "success");
        router.push("/");
      }
    } catch (err) {
      notify(err instanceof Error ? err.message : "Request failed", "error");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex min-h-screen">
      {/* Brand panel */}
      <div className="relative hidden w-1/2 flex-col justify-between bg-slate-900 p-12 text-white lg:flex">
        <div className="flex items-center gap-2.5">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-brand-600">
            <Scale className="h-5 w-5" />
          </div>
          <span className="text-lg font-semibold tracking-tight">AEGIS</span>
        </div>
        <div className="space-y-5">
          <h1 className="text-4xl font-semibold leading-tight tracking-tight">
            Contract-first legal AI &amp; lifecycle management.
          </h1>
          <p className="max-w-md text-slate-300">
            Upload, review, redline, approve, sign and renew — with an AI
            assistant, governed playbooks, and a portfolio-wide Contract Brain.
          </p>
          <div className="flex flex-wrap gap-2 pt-2 text-xs text-slate-400">
            {[
              "Contract Hub",
              "AI Assistant",
              "Playbooks",
              "Tabular Review",
              "Contract Brain",
            ].map((f) => (
              <span
                key={f}
                className="rounded-full border border-slate-700 px-3 py-1"
              >
                {f}
              </span>
            ))}
          </div>
        </div>
        <p className="text-xs text-slate-500">
          Clean-room Legal CLM platform
        </p>
      </div>

      {/* Form panel */}
      <div className="flex flex-1 items-center justify-center p-6">
        <div className="w-full max-w-sm">
          <div className="mb-8 lg:hidden">
            <div className="flex items-center gap-2.5">
              <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-brand-600 text-white">
                <Scale className="h-5 w-5" />
              </div>
              <span className="text-lg font-semibold">AEGIS</span>
            </div>
          </div>

          <div className="mb-6 flex gap-1 rounded-lg bg-slate-100 p-1">
            {(["login", "register", "setup"] as Mode[]).map((m) => (
              <button
                key={m}
                onClick={() => setMode(m)}
                className={cn(
                  "flex-1 rounded-md px-3 py-1.5 text-sm font-medium capitalize transition-colors",
                  mode === m
                    ? "bg-white text-slate-900 shadow-sm"
                    : "text-slate-500 hover:text-slate-800",
                )}
              >
                {m === "setup" ? "First admin" : m}
              </button>
            ))}
          </div>

          <h2 className="text-lg font-semibold text-slate-900">
            {mode === "login"
              ? "Sign in to your workspace"
              : mode === "register"
                ? "Request access"
                : "Create your organization"}
          </h2>
          <p className="mb-6 mt-1 text-sm text-slate-500">
            {mode === "login"
              ? "Enter your credentials to continue."
              : mode === "register"
                ? "Self-registration may require admin approval."
                : "Bootstrap the first admin with a setup token."}
          </p>

          <form onSubmit={submit} className="space-y-4">
            {mode === "setup" && (
              <>
                <Field label="Organization name">
                  <Input
                    required
                    value={orgName}
                    onChange={(e) => setOrgName(e.target.value)}
                    placeholder="Acme Legal"
                  />
                </Field>
                <Field label="Organization slug">
                  <Input
                    required
                    value={orgSlug}
                    onChange={(e) => setOrgSlug(e.target.value)}
                    placeholder="acme-legal"
                  />
                </Field>
                <Field label="Setup token">
                  <Input
                    required
                    value={setupToken}
                    onChange={(e) => setSetupToken(e.target.value)}
                  />
                </Field>
                <Field label="Allowed email domains" hint="Comma-separated, optional">
                  <Input
                    value={allowedDomains}
                    onChange={(e) => setAllowedDomains(e.target.value)}
                    placeholder="acme.com, acme.io"
                  />
                </Field>
              </>
            )}
            {mode !== "login" && (
              <Field label="Full name">
                <Input
                  required
                  value={fullName}
                  onChange={(e) => setFullName(e.target.value)}
                  placeholder="Jane Counsel"
                />
              </Field>
            )}
            <Field label="Email">
              <Input
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@company.com"
              />
            </Field>
            <Field
              label="Password"
              hint={mode !== "login" ? "Minimum 10 characters" : undefined}
            >
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
              disabled={loading}
            >
              {loading && <Loader2 className="h-4 w-4 animate-spin" />}
              {mode === "login"
                ? "Sign in"
                : mode === "register"
                  ? "Request access"
                  : "Create organization"}
            </Button>
          </form>

          <div className="my-5 flex items-center gap-3">
            <div className="h-px flex-1 bg-slate-200" />
            <span className="text-xs text-slate-400">or</span>
            <div className="h-px flex-1 bg-slate-200" />
          </div>

          <Button
            type="button"
            variant="outline"
            size="lg"
            className="w-full"
            onClick={exploreDemo}
          >
            Explore demo — no backend needed
          </Button>
          <p className="mt-2 text-center text-xs text-slate-400">
            Loads the full UI populated with sample data.
          </p>
        </div>
      </div>
    </div>
  );
}
