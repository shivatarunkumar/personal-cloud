"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { GearIcon, LogoutIcon } from "./icons";
import { useSession } from "./AppShell";

export function AccountMenu() {
  const { user } = useSession();
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (!box.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  if (!user) return null;
  const initials = user.display_name
    .split(" ")
    .map((w) => w[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();

  async function signOut() {
    await fetch("/api/v1/auth/logout", { method: "POST" });
    window.location.href = "/login";
  }

  return (
    <div className="relative" ref={box}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex h-9 w-9 items-center justify-center rounded-full bg-brand text-sm font-semibold text-brand-contrast"
        aria-label="Account"
        aria-expanded={open}
      >
        {initials || "?"}
      </button>
      {open && (
        <div className="absolute right-0 mt-2 w-64 overflow-hidden rounded-xl border border-line bg-bg shadow-xl">
          <div className="border-b border-line px-4 py-3">
            <div className="truncate font-medium">{user.display_name}</div>
            <div className="truncate text-sm text-muted">{user.email}</div>
          </div>
          <Link href="/settings" onClick={() => setOpen(false)} className="flex items-center gap-3 px-4 py-2.5 text-sm hover:bg-surface-hover">
            <GearIcon size={18} /> Settings
          </Link>
          <button type="button" onClick={signOut} className="flex w-full items-center gap-3 px-4 py-2.5 text-sm hover:bg-surface-hover">
            <LogoutIcon size={18} /> Sign out
          </button>
        </div>
      )}
    </div>
  );
}
