"use client";

import { useState } from "react";
import { formatBytes, formatDuration } from "@/lib/format";
import { CheckIcon, ChevronRight, RestoreIcon, XIcon } from "./icons";
import { type UploadItem, useUploads } from "./UploadProvider";

// in progress first, then waiting, then problems; finished ones sink to the bottom
const ORDER: Record<UploadItem["status"], number> = { uploading: 0, queued: 1, error: 2, cancelled: 3, done: 4, skipped: 5 };

function Row({ item }: { item: UploadItem }) {
  const { cancel, retry } = useUploads();
  const pct = item.size ? Math.round((item.uploaded / item.size) * 100) : 0;
  const left = item.bytesPerSecond > 0 ? (item.size - item.uploaded) / item.bytesPerSecond : null;
  return (
    <li className={`px-4 py-2.5 ${item.status === "done" || item.status === "skipped" ? "opacity-70" : ""}`}>
      <div className="flex items-center gap-2 text-sm">
        <span className="min-w-0 flex-1 truncate" title={item.group ? `${item.group}/${item.name}` : item.name}>
          {item.name}
          {item.group && <span className="ml-1.5 text-xs text-muted">in {item.group}</span>}
        </span>
        {item.status === "done" && <CheckIcon size={16} className="shrink-0 text-ok" />}
        {item.status === "error" && (
          <button type="button" onClick={() => retry(item.key)} className="shrink-0 text-brand" title="Retry">
            <RestoreIcon size={16} />
          </button>
        )}
        {(item.status === "queued" || item.status === "uploading") && (
          <button type="button" onClick={() => cancel(item.key)} className="shrink-0 text-muted hover:text-fg" title="Cancel">
            <XIcon size={16} />
          </button>
        )}
      </div>
      {item.status === "uploading" && (
        <>
          <div className="relative mt-1.5 h-1.5 overflow-hidden rounded-full bg-surface-hover">
            <div className="upload-sheen relative h-full overflow-hidden rounded-full bg-brand transition-all" style={{ width: `${pct}%` }} />
          </div>
          <div className="mt-1 text-xs text-muted tabular">
            {pct}% · {formatBytes(item.uploaded)} of {formatBytes(item.size)}
            {item.bytesPerSecond > 0 && ` · ${formatBytes(item.bytesPerSecond)}/s · ${formatDuration(left)} left`}
          </div>
        </>
      )}
      {item.status === "queued" && <div className="mt-0.5 text-xs text-muted">Waiting · {formatBytes(item.size)}</div>}
      {item.status === "error" && <div className="mt-0.5 text-xs text-bad">{item.error}</div>}
      {item.status === "cancelled" && <div className="mt-0.5 text-xs text-muted">Cancelled</div>}
      {item.status === "done" && (
        <div className="mt-0.5 text-xs text-muted">
          {formatBytes(item.size)} · {item.onConflict === "replace" ? "replaced (old version in the trash)" : "uploaded"}
        </div>
      )}
      {item.status === "skipped" && <div className="mt-0.5 text-xs text-muted">Skipped · already there</div>}
    </li>
  );
}

export function UploadTray() {
  const { items, clearFinished, cancelAll } = useUploads();
  const [collapsed, setCollapsed] = useState(false);
  if (items.length === 0) return null;

  // the whole batch counts, finished files included, so the percentage only ever goes up
  const batch = items.filter((i) => i.status !== "cancelled" && i.status !== "skipped");
  const skipped = items.filter((i) => i.status === "skipped").length;
  const active = items.filter((i) => i.status === "queued" || i.status === "uploading");
  const done = items.filter((i) => i.status === "done").length;
  const failed = items.filter((i) => i.status === "error").length;
  const totalBytes = batch.reduce((s, i) => s + i.size, 0);
  const sentBytes = batch.reduce((s, i) => s + (i.status === "done" ? i.size : i.uploaded), 0);
  const pct = totalBytes > 0 ? Math.floor((sentBytes / totalBytes) * 100) : batch.length ? Math.floor((done / batch.length) * 100) : 0;
  const speed = active.reduce((s, i) => s + (i.status === "uploading" ? i.bytesPerSecond : 0), 0);
  const left = speed > 0 ? (totalBytes - sentBytes) / speed : null;

  const title = active.length
    ? `Uploading · ${done} of ${batch.length} done · ${pct}%`
    : `${done} upload${done === 1 ? "" : "s"} complete${skipped ? ` · ${skipped} skipped` : ""}${failed ? ` · ${failed} failed` : ""}`;

  const sorted = [...items].sort((a, b) => ORDER[a.status] - ORDER[b.status]);

  return (
    <section className="fixed bottom-4 right-4 z-40 w-[min(400px,calc(100vw-2rem))] overflow-hidden rounded-xl border border-line bg-bg shadow-2xl">
      <header className="bg-surface px-4 py-3">
        <div className="flex items-center gap-2">
          <button type="button" onClick={() => setCollapsed((v) => !v)} className="text-muted" aria-label={collapsed ? "Expand" : "Collapse"}>
            <ChevronRight size={18} className={`transition ${collapsed ? "" : "rotate-90"}`} />
          </button>
          <span className="flex-1 text-sm font-semibold tabular">{title}</span>
          {active.length > 0 ? (
            <button type="button" onClick={cancelAll} className="text-xs text-muted hover:text-fg">
              Cancel all
            </button>
          ) : (
            <button type="button" onClick={clearFinished} className="text-muted hover:text-fg" aria-label="Close">
              <XIcon size={18} />
            </button>
          )}
        </div>
        {active.length > 0 && (
          <>
            <div className="relative mt-2 h-2 overflow-hidden rounded-full bg-surface-hover">
              <div className="upload-sheen relative h-full overflow-hidden rounded-full bg-brand transition-all" style={{ width: `${pct}%` }} />
            </div>
            <div className="mt-1 text-xs text-muted tabular">
              {formatBytes(sentBytes)} of {formatBytes(totalBytes)}
              {speed > 0 && ` · ${formatBytes(speed)}/s · about ${formatDuration(left)} left`}
            </div>
          </>
        )}
      </header>
      {!collapsed && (
        <ul className="max-h-80 divide-y divide-line overflow-y-auto">
          {sorted.map((item) => (
            <Row key={item.key} item={item} />
          ))}
        </ul>
      )}
    </section>
  );
}
