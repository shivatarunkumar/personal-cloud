"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { formatBytes } from "@/lib/format";
import type { Usage } from "@/lib/types";
import { ClockIcon, HomeFolderIcon, ImageIcon, TrashIcon, VideoIcon } from "./icons";
import { DRIVE_CHANGED } from "./UploadProvider";

const NAV = [
  { href: "/drive", label: "My files", Icon: HomeFolderIcon },
  { href: "/recent", label: "Recent", Icon: ClockIcon },
  { href: "/photos", label: "Photos", Icon: ImageIcon },
  { href: "/videos", label: "Videos", Icon: VideoIcon },
  { href: "/trash", label: "Trash", Icon: TrashIcon },
];

function StorageMeter() {
  const [usage, setUsage] = useState<Usage | null>(null);
  const load = useCallback(() => {
    api<Usage>("/drive/usage").then(setUsage).catch(() => {});
  }, []);
  useEffect(() => {
    load();
    window.addEventListener(DRIVE_CHANGED, load);
    return () => window.removeEventListener(DRIVE_CHANGED, load);
  }, [load]);
  if (!usage) return null;
  const pct = usage.quota_bytes ? Math.min(100, (usage.used_bytes / usage.quota_bytes) * 100) : null;
  return (
    <div className="px-5 py-4 text-sm">
      <div className="mb-1 font-medium">Storage</div>
      {pct != null && (
        <div className="mb-1.5 h-1.5 overflow-hidden rounded-full bg-surface-hover">
          <div className={`h-full rounded-full ${pct > 90 ? "bg-bad" : "bg-brand"}`} style={{ width: `${pct}%` }} />
        </div>
      )}
      <div className="text-muted">
        {formatBytes(usage.used_bytes)} used{usage.quota_bytes ? ` of ${formatBytes(usage.quota_bytes)}` : ""}
      </div>
      {usage.trash_bytes > 0 && <div className="text-xs text-muted">{formatBytes(usage.trash_bytes)} in the trash</div>}
    </div>
  );
}

export function Sidebar() {
  const pathname = usePathname();
  return (
    <nav className="flex h-full flex-col py-3">
      <ul className="flex-1 space-y-0.5 px-3">
        {NAV.map(({ href, label, Icon }) => {
          const active = pathname === href || pathname.startsWith(`${href}/`);
          return (
            <li key={href}>
              <Link
                href={href}
                className={`flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition ${
                  active ? "bg-selected font-semibold text-brand" : "hover:bg-surface-hover"
                }`}
              >
                <Icon size={19} />
                {label}
              </Link>
            </li>
          );
        })}
      </ul>
      <StorageMeter />
    </nav>
  );
}
