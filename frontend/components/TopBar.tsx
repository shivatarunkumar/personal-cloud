"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import type { Theme } from "@/lib/theme";
import { AccountMenu } from "./AccountMenu";
import { CloudIcon, MenuIcon, SearchIcon } from "./icons";
import { ThemeToggle } from "./ThemeToggle";

function SearchBox() {
  const router = useRouter();
  const params = useSearchParams();
  const [q, setQ] = useState(params.get("q") ?? "");
  useEffect(() => setQ(params.get("q") ?? ""), [params]);
  return (
    <form
      role="search"
      className="relative w-full max-w-xl"
      onSubmit={(e) => {
        e.preventDefault();
        if (q.trim()) router.push(`/search?q=${encodeURIComponent(q.trim())}`);
      }}
    >
      <SearchIcon size={18} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
      <input
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="Search your files"
        aria-label="Search your files"
        className="w-full rounded-full border border-transparent bg-surface py-2 pl-10 pr-4 text-sm outline-none transition focus:border-brand focus:bg-bg"
      />
    </form>
  );
}

export function TopBar({ appName, theme, onMenu }: { appName: string; theme: Theme; onMenu: () => void }) {
  return (
    <header className="fixed inset-x-0 top-0 z-40 flex h-14 items-center gap-3 border-b border-line bg-bg px-3 sm:px-4">
      <button type="button" onClick={onMenu} className="rounded-lg p-2 hover:bg-surface-hover lg:hidden" aria-label="Menu">
        <MenuIcon />
      </button>
      <Link href="/drive" className="flex shrink-0 items-center gap-2 lg:w-52">
        <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand text-brand-contrast">
          <CloudIcon size={20} />
        </span>
        <span className="hidden text-lg font-semibold tracking-tight sm:inline">{appName}</span>
      </Link>
      <div className="flex flex-1 justify-center">
        <Suspense>
          <SearchBox />
        </Suspense>
      </div>
      <ThemeToggle initial={theme} />
      <AccountMenu />
    </header>
  );
}
