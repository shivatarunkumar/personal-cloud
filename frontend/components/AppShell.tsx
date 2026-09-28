"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createContext, type ReactNode, useCallback, useContext, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Theme } from "@/lib/theme";
import type { User } from "@/lib/types";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";
import { Spinner } from "./ui";
import { UploadProvider } from "./UploadProvider";
import { UploadTray } from "./UploadTray";

type SessionCtx = { user: User | null; appName: string; reload: () => Promise<void>; setUser: (u: User | null) => void };
const Session = createContext<SessionCtx | null>(null);

export function useSession() {
  const ctx = useContext(Session);
  if (!ctx) throw new Error("useSession outside AppShell");
  return ctx;
}

const PUBLIC = ["/login", "/register"];

// The session is checked in the browser (where an expired access cookie can be refreshed),
// so the server never renders a page for someone who isn't signed in.
export function AppShell({ appName, theme, children }: { appName: string; theme: Theme; children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  const [checked, setChecked] = useState(false);
  const [drawer, setDrawer] = useState(false);
  const isPublic = PUBLIC.some((p) => pathname.startsWith(p));

  const reload = useCallback(async () => {
    try {
      setUser(await api<User>("/auth/me"));
    } catch {
      setUser(null);
    } finally {
      setChecked(true);
    }
  }, []);

  useEffect(() => {
    // /auth/me doesn't refresh on its own (auth paths never do); try the refresh cookie first
    (async () => {
      const me = await fetch("/api/v1/auth/me");
      if (me.ok) {
        setUser(await me.json());
        setChecked(true);
        return;
      }
      const refreshed = await fetch("/api/v1/auth/refresh", { method: "POST" });
      setUser(refreshed.ok ? await refreshed.json() : null);
      setChecked(true);
    })();
  }, []);

  useEffect(() => {
    if (!checked) return;
    if (!user && !isPublic) router.replace(`/login?next=${encodeURIComponent(pathname)}`);
    if (user && isPublic) router.replace("/drive");
  }, [checked, user, isPublic, pathname, router]);

  useEffect(() => setDrawer(false), [pathname]);

  const value = { user, appName, reload, setUser };

  if (isPublic) {
    return <Session.Provider value={value}>{children}</Session.Provider>;
  }
  if (!checked || !user) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Spinner size={28} />
      </div>
    );
  }

  return (
    <Session.Provider value={value}>
      <UploadProvider>
        <TopBar appName={appName} theme={theme} onMenu={() => setDrawer((v) => !v)} />
        <aside className="fixed bottom-0 left-0 top-14 z-30 hidden w-60 overflow-y-auto border-r border-line bg-bg lg:block">
          <Sidebar />
        </aside>
        {drawer && (
          <div className="fixed inset-0 z-50 lg:hidden">
            <button type="button" aria-label="Close navigation" className="absolute inset-0 bg-scrim" onClick={() => setDrawer(false)} />
            <aside className="absolute bottom-0 left-0 top-0 w-64 overflow-y-auto bg-bg pt-3">
              <Sidebar />
            </aside>
          </div>
        )}
        <main className="min-h-screen pt-14 lg:pl-60">
          {user.must_change_password && !pathname.startsWith("/settings") && (
            <div className="border-b border-warn/40 bg-warn/10 px-4 py-2 text-sm">
              You signed in with a temporary password.{" "}
              <Link href="/settings#password" className="font-medium text-brand underline">
                Choose your own now
              </Link>
              .
            </div>
          )}
          {children}
        </main>
        <UploadTray />
      </UploadProvider>
    </Session.Provider>
  );
}
