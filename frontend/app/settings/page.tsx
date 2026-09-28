"use client";

import { useCallback, useEffect, useState } from "react";
import { useSession } from "@/components/AppShell";
import { Button, ErrorBanner, Field, Modal, Spinner, Toast } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { describeAgent, formatBytes, formatDate } from "@/lib/format";
import type { DeviceSession, Usage, User } from "@/lib/types";

const KIND_NAMES: Record<string, string> = {
  image: "Photos",
  video: "Videos",
  audio: "Audio",
  pdf: "PDFs",
  text: "Text",
  archive: "Archives",
  document: "Documents",
  other: "Other",
};

function Section({ id, title, children }: { id?: string; title: string; children: React.ReactNode }) {
  return (
    <section id={id} className="rounded-2xl border border-line p-5">
      <h2 className="mb-4 text-base font-semibold">{title}</h2>
      {children}
    </section>
  );
}

function Profile() {
  const { user, setUser } = useSession();
  const [name, setName] = useState(user?.display_name ?? "");
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string>();
  if (!user) return null;
  return (
    <Section title="Profile">
      <form
        className="space-y-4"
        onSubmit={async (e) => {
          e.preventDefault();
          try {
            setUser(await api<User>("/account/me", { method: "PATCH", json: { display_name: name } }));
            setMsg("Saved");
          } catch (err) {
            setError(err instanceof Error ? err.message : "Could not save");
          }
        }}
      >
        <Field label="Your name" value={name} onChange={(e) => setName(e.target.value)} error={error} />
        <div className="grid gap-1 text-sm sm:grid-cols-2">
          <div>
            <span className="text-muted">Email:</span> {user.email}
          </div>
          <div>
            <span className="text-muted">Username:</span> {user.username}
          </div>
        </div>
        <Button type="submit" variant="primary" disabled={!name.trim() || name === user.display_name}>
          Save
        </Button>
      </form>
      {msg && <Toast message={msg} onDone={() => setMsg(null)} />}
    </Section>
  );
}

function Password() {
  const { setUser } = useSession();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  return (
    <Section id="password" title="Password">
      <form
        className="space-y-4"
        onSubmit={async (e) => {
          e.preventDefault();
          if (next !== again) {
            setErrors({ again: "The two new passwords don't match" });
            return;
          }
          setBusy(true);
          setErrors({});
          try {
            setUser(await api<User>("/account/password", { method: "POST", json: { current_password: current, new_password: next } }));
            setCurrent("");
            setNext("");
            setAgain("");
            setMsg("Password changed. Your other devices were signed out.");
          } catch (err) {
            setErrors({ [err instanceof ApiError && err.field ? err.field : "current_password"]: err instanceof Error ? err.message : "Failed" });
          } finally {
            setBusy(false);
          }
        }}
      >
        <Field label="Current password" type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} error={errors.current_password} />
        <Field label="New password" type="password" autoComplete="new-password" minLength={10} value={next} onChange={(e) => setNext(e.target.value)} error={errors.new_password} hint="At least 10 characters." />
        <Field label="New password again" type="password" autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} error={errors.again} />
        <Button type="submit" variant="primary" disabled={busy || !current || !next}>
          {busy ? <Spinner size={14} /> : "Change password"}
        </Button>
      </form>
      {msg && <Toast message={msg} onDone={() => setMsg(null)} />}
    </Section>
  );
}

function Devices() {
  const [sessions, setSessions] = useState<DeviceSession[] | null>(null);
  const load = useCallback(() => api<DeviceSession[]>("/account/sessions").then(setSessions), []);
  useEffect(() => {
    load().catch(() => setSessions([]));
  }, [load]);
  return (
    <Section title="Signed-in devices">
      {!sessions ? (
        <Spinner />
      ) : (
        <ul className="divide-y divide-line">
          {sessions.map((s) => (
            <li key={s.id} className="flex items-center gap-3 py-2.5 text-sm">
              <div className="min-w-0 flex-1">
                <div className="font-medium">
                  {describeAgent(s.user_agent)} {s.current && <span className="ml-1 rounded bg-selected px-1.5 py-0.5 text-xs text-brand">This device</span>}
                </div>
                <div className="text-xs text-muted">
                  {s.ip ?? "unknown address"} · active {formatDate(s.last_used_at ?? s.created_at)}
                </div>
              </div>
              {!s.current && (
                <Button
                  onClick={async () => {
                    await api(`/account/sessions/${s.id}`, { method: "DELETE" });
                    load();
                  }}
                >
                  Sign out
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

function StorageSummary() {
  const [usage, setUsage] = useState<Usage | null>(null);
  useEffect(() => {
    api<Usage>("/drive/usage").then(setUsage).catch(() => {});
  }, []);
  if (!usage) return null;
  const kinds = Object.entries(usage.by_kind).sort((a, b) => b[1].bytes - a[1].bytes);
  return (
    <Section title="Storage">
      <p className="mb-3 text-sm">
        <span className="text-2xl font-semibold">{formatBytes(usage.used_bytes)}</span>{" "}
        <span className="text-muted">used{usage.quota_bytes ? ` of ${formatBytes(usage.quota_bytes)}` : ""}</span>
      </p>
      <ul className="grid gap-2 text-sm sm:grid-cols-2">
        {kinds.map(([kind, v]) => (
          <li key={kind} className="flex justify-between rounded-lg bg-surface px-3 py-2">
            <span>
              {KIND_NAMES[kind] ?? kind} <span className="text-muted">({v.count})</span>
            </span>
            <span className="tabular text-muted">{formatBytes(v.bytes)}</span>
          </li>
        ))}
        {usage.trash_bytes > 0 && (
          <li className="flex justify-between rounded-lg bg-surface px-3 py-2">
            <span>In the trash</span>
            <span className="tabular text-muted">{formatBytes(usage.trash_bytes)}</span>
          </li>
        )}
      </ul>
    </Section>
  );
}

function DeleteAccount() {
  const [open, setOpen] = useState(false);
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);
  return (
    <Section title="Delete account">
      <p className="mb-3 text-sm text-muted">Deletes your account and every file in it from the cloud storage. This can't be undone.</p>
      <Button variant="danger" onClick={() => setOpen(true)}>
        Delete my account
      </Button>
      {open && (
        <Modal
          title="Delete your account?"
          onClose={() => setOpen(false)}
          footer={
            <>
              <Button onClick={() => setOpen(false)}>Cancel</Button>
              <Button
                variant="danger"
                disabled={!password || busy}
                onClick={async () => {
                  setBusy(true);
                  try {
                    await api("/account/me", { method: "DELETE", json: { password } });
                    window.location.href = "/login";
                  } catch (err) {
                    setError(err instanceof Error ? err.message : "Failed");
                    setBusy(false);
                  }
                }}
              >
                {busy ? <Spinner size={14} /> : "Delete everything"}
              </Button>
            </>
          }
        >
          <p className="mb-3 text-sm text-muted">All your files and folders are deleted from the bucket. Enter your password to confirm.</p>
          {error && <ErrorBanner message={error} />}
          <Field label="Password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </Modal>
      )}
    </Section>
  );
}

export default function Settings() {
  return (
    <div className="mx-auto max-w-3xl space-y-5 px-4 py-6 sm:px-6">
      <h1 className="text-xl font-semibold">Settings</h1>
      <Profile />
      <Password />
      <StorageSummary />
      <Devices />
      <DeleteAccount />
    </div>
  );
}
