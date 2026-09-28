"use client";

import { createContext, type ReactNode, useCallback, useContext, useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { type OnConflict, type PickedFile, type Progress, uploadFile } from "@/lib/upload";
import { type Clash, ConflictDialog, type Decision } from "./ConflictDialog";

/** Something in a folder changed: listings showing it reload. */
export const DRIVE_CHANGED = "drive:changed";
export function driveChanged(folderId: string | null = null) {
  window.dispatchEvent(new CustomEvent(DRIVE_CHANGED, { detail: { folderId } }));
}

export type UploadItem = {
  key: string;
  name: string;
  size: number;
  folderId: string | null;
  /** the top folder of a dropped folder, to group the tray */
  group: string | null;
  status: "queued" | "uploading" | "done" | "skipped" | "error" | "cancelled";
  /** the name was taken: new version (old one to the trash) or keep both */
  onConflict: OnConflict;
  uploaded: number;
  bytesPerSecond: number;
  error?: string;
  file: File;
};

type Ctx = {
  items: UploadItem[];
  add: (picked: PickedFile[], parentId: string | null) => Promise<void>;
  cancel: (key: string) => void;
  retry: (key: string) => void;
  clearFinished: () => void;
  cancelAll: () => void;
};

const UploadContext = createContext<Ctx | null>(null);
const PARALLEL = 3;
// names checked per request (the server takes up to 20,000)
const CHECK_BATCH = 5000;

type ServerClash = {
  folder_id: string | null;
  name: string;
  type: "file" | "folder";
  size_bytes: number | null;
  updated_at: string;
};

const clashKey = (folderId: string | null, name: string) => `${folderId ?? "root"}|${name.toLowerCase()}`;

export function useUploads() {
  const ctx = useContext(UploadContext);
  if (!ctx) throw new Error("useUploads outside UploadProvider");
  return ctx;
}

let counter = 0;

export function UploadProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<UploadItem[]>([]);
  const controllers = useRef(new Map<string, AbortController>());
  const booked = useRef(new Map<string, string>());
  const running = useRef(0);
  const [asking, setAsking] = useState<{
    clashes: Clash[];
    total: number;
    resolve: (d: Map<string, Decision> | null) => void;
  } | null>(null);
  const itemsRef = useRef(items);
  itemsRef.current = items;

  const patch = useCallback((key: string, change: Partial<UploadItem>) => {
    setItems((all) => all.map((item) => (item.key === key ? { ...item, ...change } : item)));
  }, []);

  const pump = useCallback(() => {
    while (running.current < PARALLEL) {
      const next = itemsRef.current.find((i) => i.status === "queued" && !controllers.current.has(i.key));
      if (!next) return;
      const controller = new AbortController();
      controllers.current.set(next.key, controller);
      running.current++;
      // mark it right away so the next loop iteration doesn't pick it again
      itemsRef.current = itemsRef.current.map((i) => (i.key === next.key ? { ...i, status: "uploading" } : i));
      patch(next.key, { status: "uploading", error: undefined });
      uploadFile(
        next.file,
        next.folderId,
        (p: Progress) => patch(next.key, { uploaded: p.uploaded, bytesPerSecond: p.bytesPerSecond }),
        controller.signal,
        (fileId) => booked.current.set(next.key, fileId),
        next.onConflict,
      )
        .then(() => {
          patch(next.key, { status: "done", uploaded: next.size });
          driveChanged(next.folderId);
        })
        .catch((err: unknown) => {
          if (controller.signal.aborted) {
            patch(next.key, { status: "cancelled" });
            const fileId = booked.current.get(next.key);
            if (fileId) api(`/uploads/${fileId}`, { method: "DELETE" }).catch(() => {});
          } else {
            patch(next.key, { status: "error", error: err instanceof Error ? err.message : "Upload failed" });
            // free the name it reserved, so Retry gets the same name again
            const fileId = booked.current.get(next.key);
            if (fileId) api(`/uploads/${fileId}`, { method: "DELETE" }).catch(() => {});
          }
        })
        .finally(() => {
          controllers.current.delete(next.key);
          booked.current.delete(next.key);
          running.current--;
          setTimeout(pump, 0);
        });
    }
  }, [patch]);

  useEffect(() => {
    if (items.some((i) => i.status === "queued")) pump();
  }, [items, pump]);

  // leaving the page mid-upload loses it: ask first
  useEffect(() => {
    const busy = items.some((i) => i.status === "queued" || i.status === "uploading");
    if (!busy) return;
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [items]);

  const add = useCallback(async (picked: PickedFile[], parentId: string | null) => {
    // folders first (one call for the whole tree), so every file knows where it goes
    const dirs = new Set<string>();
    for (const p of picked) {
      const parts = p.relPath.split("/").slice(0, -1);
      for (let i = 1; i <= parts.length; i++) dirs.add(parts.slice(0, i).join("/"));
    }
    let mapping: Record<string, string> = {};
    if (dirs.size) {
      const res = await api<{ folders: Record<string, string> }>("/drive/folders/ensure", {
        method: "POST",
        json: { parent_id: parentId, paths: [...dirs] },
      });
      mapping = res.folders;
      driveChanged(parentId);
    }
    const files = picked
      .filter((p) => p.file)
      .map((p) => {
        const dir = p.relPath.split("/").slice(0, -1).join("/");
        return { p, dir, folderId: dir ? (mapping[dir] ?? parentId) : parentId };
      });

    // anything already there? ask once for the whole batch: replace, skip or keep both
    const found: ServerClash[] = [];
    for (let i = 0; i < files.length; i += CHECK_BATCH) {
      const batch = files.slice(i, i + CHECK_BATCH).map((f) => ({ folder_id: f.folderId, name: f.p.file.name }));
      found.push(...(await api<ServerClash[]>("/uploads/conflicts", { method: "POST", json: { items: batch } })));
    }
    let decisions = new Map<string, Decision>();
    if (found.length) {
      const existing = new Map(found.map((c) => [clashKey(c.folder_id, c.name), c]));
      const clashes: Clash[] = [];
      const seen = new Set<string>();
      for (const f of files) {
        const key = clashKey(f.folderId, f.p.file.name);
        const old = existing.get(key);
        if (old && !seen.has(key)) {
          seen.add(key);
          clashes.push({
            key,
            name: f.p.file.name,
            where: f.dir,
            newSize: f.p.file.size,
            type: old.type,
            oldSize: old.size_bytes,
            oldDate: old.updated_at,
          });
        }
      }
      const answer = await new Promise<Map<string, Decision> | null>((resolve) =>
        setAsking({ clashes, total: files.length, resolve }),
      );
      setAsking(null);
      if (!answer) return; // "Cancel upload": nothing is sent (the folders stay, empty)
      decisions = answer;
    }

    const fresh: UploadItem[] = files.map(({ p, dir, folderId }) => {
      const decision = decisions.get(clashKey(folderId, p.file.name));
      return {
        key: `u${++counter}`,
        name: p.file.name,
        size: p.file.size,
        folderId,
        group: dir ? dir.split("/")[0] : null,
        status: decision === "skip" ? "skipped" : "queued",
        onConflict: decision === "replace" ? "replace" : "rename",
        uploaded: 0,
        bytesPerSecond: 0,
        file: p.file,
      };
    });
    // nothing running any more: this is a new batch, so the tray's percentage starts at 0
    setItems((all) => {
      const busy = all.some((i) => i.status === "queued" || i.status === "uploading");
      return [...(busy ? all : all.filter((i) => i.status === "error")), ...fresh];
    });
  }, []);

  const cancel = useCallback((key: string) => {
    const controller = controllers.current.get(key);
    if (controller) controller.abort();
    else setItems((all) => all.map((i) => (i.key === key && i.status === "queued" ? { ...i, status: "cancelled" } : i)));
  }, []);

  const retry = useCallback(
    (key: string) => patch(key, { status: "queued", uploaded: 0, error: undefined }),
    [patch],
  );

  const clearFinished = useCallback(() => {
    setItems((all) => all.filter((i) => i.status === "queued" || i.status === "uploading"));
  }, []);

  const cancelAll = useCallback(() => {
    for (const c of controllers.current.values()) c.abort();
    setItems((all) => all.map((i) => (i.status === "queued" ? { ...i, status: "cancelled" } : i)));
  }, []);

  return (
    <UploadContext.Provider value={{ items, add, cancel, retry, clearFinished, cancelAll }}>
      {children}
      {asking && <ConflictDialog clashes={asking.clashes} total={asking.total} onDone={asking.resolve} />}
    </UploadContext.Provider>
  );
}

export function explain(err: unknown): string {
  if (err instanceof ApiError || err instanceof Error) return err.message;
  return "Something went wrong";
}
