"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { User } from "@/lib/types";
import { useSession } from "./AppShell";
import { CloudIcon } from "./icons";
import { Button, ErrorBanner, Field, Spinner } from "./ui";

type Config = { app_name: string; allow_signup: boolean };

export function AuthForm({ mode }: { mode: "login" | "register" }) {
  const { appName, setUser } = useSession();
  const next = useSearchParams().get("next");
  const [config, setConfig] = useState<Config | null>(null);
  const [values, setValues] = useState({ login: "", email: "", username: "", display_name: "", password: "" });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [forgot, setForgot] = useState(false);

  useEffect(() => {
    api<Config>("/auth/config").then(setConfig).catch(() => setConfig({ app_name: appName, allow_signup: false }));
  }, [appName]);

  const set = (key: keyof typeof values) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setValues((v) => ({ ...v, [key]: e.target.value }));

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(undefined);
    setErrors({});
    try {
      const user =
        mode === "login"
          ? await api<User>("/auth/login", { method: "POST", json: { login: values.login, password: values.password } })
          : await api<User>("/auth/register", {
              method: "POST",
              json: {
                email: values.email,
                username: values.username,
                display_name: values.display_name,
                password: values.password,
              },
            });
      setUser(user);
      // a full navigation: every part of the app starts from the new session
      window.location.href = next && next.startsWith("/") && !next.startsWith("//") ? next : "/drive";
    } catch (err) {
      if (err instanceof ApiError && err.field) setErrors({ [err.field]: err.message });
      else setError(err instanceof Error ? err.message : "Something went wrong");
      setBusy(false);
    }
  }

  const signupClosed = mode === "register" && config && !config.allow_signup;

  return (
    <div className="flex min-h-screen items-center justify-center bg-surface px-4 py-10">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex flex-col items-center gap-3 text-center">
          <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-brand text-brand-contrast">
            <CloudIcon size={28} />
          </span>
          <h1 className="text-2xl font-semibold tracking-tight">{config?.app_name ?? appName}</h1>
          <p className="text-sm text-muted">{mode === "login" ? "Sign in to your files" : "Create your account"}</p>
        </div>
        <div className="rounded-2xl border border-line bg-bg p-6 shadow-sm">
          {!config ? (
            <div className="flex justify-center py-8">
              <Spinner />
            </div>
          ) : signupClosed ? (
            <p className="text-sm text-muted">
              New accounts are switched off on this cloud. Ask the person who runs it, or{" "}
              <Link href="/login" className="text-brand underline">
                sign in
              </Link>
              .
            </p>
          ) : (
            <form onSubmit={submit} className="space-y-4">
              {error && <ErrorBanner message={error} />}
              {mode === "login" ? (
                <Field label="Email or username" autoComplete="username" autoFocus required value={values.login} onChange={set("login")} error={errors.login} />
              ) : (
                <>
                  <Field label="Your name" autoComplete="name" autoFocus required value={values.display_name} onChange={set("display_name")} error={errors.display_name} />
                  <Field label="Email" type="email" autoComplete="email" required value={values.email} onChange={set("email")} error={errors.email} />
                  <Field
                    label="Username"
                    autoComplete="username"
                    required
                    value={values.username}
                    onChange={(e) => setValues((v) => ({ ...v, username: e.target.value.toLowerCase() }))}
                    error={errors.username}
                    hint="For signing in. Letters, numbers, dots, dashes."
                  />
                </>
              )}
              <Field
                label="Password"
                type="password"
                autoComplete={mode === "login" ? "current-password" : "new-password"}
                required
                minLength={mode === "register" ? 10 : undefined}
                value={values.password}
                onChange={set("password")}
                error={errors.password}
                hint={mode === "register" ? "At least 10 characters." : undefined}
              />
              <Button type="submit" variant="primary" className="w-full" disabled={busy}>
                {busy ? <Spinner size={16} /> : mode === "login" ? "Sign in" : "Create account"}
              </Button>
              {mode === "login" && (
                <div className="text-center">
                  <button type="button" className="text-sm text-muted hover:text-fg" onClick={() => setForgot((v) => !v)}>
                    Forgot your password?
                  </button>
                  {forgot && (
                    <p className="mt-2 rounded-lg bg-surface p-3 text-left text-xs text-muted">
                      There's no email here. Whoever runs this cloud can give you a temporary password by running{" "}
                      <code className="font-mono text-fg">make reset-password LOGIN=you</code> on the server. You'll choose your
                      own after signing in.
                    </p>
                  )}
                </div>
              )}
            </form>
          )}
        </div>
        {config?.allow_signup && (
          <p className="mt-4 text-center text-sm text-muted">
            {mode === "login" ? (
              <>
                New here?{" "}
                <Link href="/register" className="font-medium text-brand hover:underline">
                  Create an account
                </Link>
              </>
            ) : (
              <>
                Already have an account?{" "}
                <Link href="/login" className="font-medium text-brand hover:underline">
                  Sign in
                </Link>
              </>
            )}
          </p>
        )}
      </div>
    </div>
  );
}
