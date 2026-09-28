"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, download } from "@/lib/api";
import { formatBytes, formatDate, monthLabel } from "@/lib/format";
import type { FileItem, Folder, Listing, Outcome } from "@/lib/types";
import { collectDropped, collectPicked, type PickedFile } from "@/lib/upload";
import { ConfirmDialog, MoveCopyDialog, NameDialog } from "./Dialogs";
import {
  ChevronRight,
  CopyIcon,
  DownloadIcon,
  FileGlyph,
  FolderGlyph,
  FolderPlusIcon,
  GridIcon,
  ListIcon,
  MoreIcon,
  MoveIcon,
  PencilIcon,
  PlusIcon,
  RestoreIcon,
  TrashIcon,
  UploadIcon,
  XIcon,
} from "./icons";
import { Button, ErrorBanner, Modal, Spinner, Toast } from "./ui";
import { DRIVE_CHANGED, driveChanged, useUploads } from "./UploadProvider";
import { Viewer } from "./Viewer";

export type Mode = "folder" | "flat" | "trash";
type Row = { type: "folder"; item: Folder } | { type: "file"; item: FileItem };
type SortKey = "name" | "modified" | "size";
type Dialog =
  | { kind: "new-folder" }
  | { kind: "rename"; row: Row }
  | { kind: "move" | "copy" }
  | { kind: "delete-forever" }
  | { kind: "empty-trash" }
  | null;

const VIEW_KEY = "pc:view";
const DRAG_TYPE = "application/x-pc-items";

const rowKey = (r: Row) => `${r.type}:${r.item.id}`;
const modified = (r: Row) => r.item.trashed_at ?? r.item.updated_at;

function sortRows(rows: Row[], key: SortKey, desc: boolean): Row[] {
  const sorted = [...rows].sort((a, b) => {
    if (a.type !== b.type) return a.type === "folder" ? -1 : 1; // folders first, always
    let cmp = 0;
    if (key === "name") cmp = a.item.name.localeCompare(b.item.name, undefined, { numeric: true, sensitivity: "base" });
    if (key === "modified") cmp = modified(a).localeCompare(modified(b));
    if (key === "size") {
      const size = (r: Row) => (r.type === "file" ? r.item.size_bytes : (r.item.item_count ?? 0));
      cmp = size(a) - size(b);
    }
    return desc ? -cmp : cmp;
  });
  return sorted;
}

function location(r: Row): string {
  if (r.type === "folder") {
    const parent = r.item.path.split("/").slice(0, -1).join("/");
    return parent ? `My files / ${parent}` : "My files";
  }
  return r.item.folder_path ? `My files / ${r.item.folder_path}` : "My files";
}

export function Browser({
  mode,
  folderId = null,
  load,
  title,
  groupByMonth = false,
  empty,
  defaultView = "list",
}: {
  mode: Mode;
  folderId?: string | null;
  load: () => Promise<Listing>;
  title?: string;
  groupByMonth?: boolean;
  empty: { title: string; hint?: string };
  defaultView?: "list" | "grid";
}) {
  const router = useRouter();
  const uploads = useUploads();
  const [data, setData] = useState<Listing | null>(null);
  const [error, setError] = useState<string>();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [anchor, setAnchor] = useState<string | null>(null);
  const [sort, setSort] = useState<{ key: SortKey; desc: boolean }>(
    groupByMonth || mode === "trash" ? { key: "modified", desc: true } : { key: "name", desc: false },
  );
  const [view, setView] = useState<"list" | "grid">(defaultView);
  const [dialog, setDialog] = useState<Dialog>(null);
  const [viewer, setViewer] = useState<number | null>(null);
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null);
  const [newMenu, setNewMenu] = useState(false);
  const [dropping, setDropping] = useState(false);
  const [dropTarget, setDropTarget] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [pendingDrop, setPendingDrop] = useState<PickedFile[] | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);
  const dragDepth = useRef(0);

  // ------------------------------------------------------------ data
  const refresh = useCallback(async () => {
    try {
      setData(await load());
      setError(undefined);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404 && mode === "folder") {
        router.replace("/drive");
        return;
      }
      setError(err instanceof Error ? err.message : "Could not load");
    }
  }, [load, mode, router]);

  useEffect(() => {
    setData(null);
    setSelected(new Set());
    refresh();
  }, [refresh]);

  useEffect(() => {
    const onChange = () => refresh();
    window.addEventListener(DRIVE_CHANGED, onChange);
    return () => window.removeEventListener(DRIVE_CHANGED, onChange);
  }, [refresh]);

  useEffect(() => {
    try {
      const saved = window.localStorage.getItem(`${VIEW_KEY}:${mode}:${groupByMonth}`);
      if (saved === "list" || saved === "grid") setView(saved);
    } catch {}
  }, [mode, groupByMonth]);

  function chooseView(v: "list" | "grid") {
    setView(v);
    try {
      window.localStorage.setItem(`${VIEW_KEY}:${mode}:${groupByMonth}`, v);
    } catch {}
  }

  const rows: Row[] = useMemo(() => {
    if (!data) return [];
    const all: Row[] = [
      ...data.folders.map((item) => ({ type: "folder" as const, item })),
      ...data.files.map((item) => ({ type: "file" as const, item })),
    ];
    return sortRows(all, sort.key, sort.desc);
  }, [data, sort]);

  const files = useMemo(() => rows.filter((r) => r.type === "file").map((r) => r.item as FileItem), [rows]);
  const chosen = rows.filter((r) => selected.has(rowKey(r)));
  const selection = {
    file_ids: chosen.filter((r) => r.type === "file").map((r) => r.item.id),
    folder_ids: chosen.filter((r) => r.type === "folder").map((r) => r.item.id),
  };

  // ------------------------------------------------------------ actions
  async function run(action: () => Promise<Outcome | void>, done?: (o: Outcome | void) => string | null) {
    try {
      const outcome = await action();
      const message = done?.(outcome);
      if (message) setToast(message);
      if (outcome && outcome.renamed?.length) setToast(`Done. Renamed to avoid clashes: ${outcome.renamed.join(", ")}`);
      setSelected(new Set());
      driveChanged(folderId);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    }
  }

  const count = (o: Outcome | void) => (o ? o.files + o.folders : 0);
  const plural = (n: number) => `${n} item${n === 1 ? "" : "s"}`;

  const trash = () =>
    run(
      () => api<Outcome>("/drive/trash", { method: "POST", json: selection }),
      (o) => `Moved ${plural(count(o))} to the trash`,
    );
  const restore = () =>
    run(
      () => api<Outcome>("/drive/restore", { method: "POST", json: selection }),
      (o) => `Restored ${plural(count(o))}`,
    );
  async function downloadSelected() {
    const ids = selection.file_ids;
    for (const [n, id] of ids.entries()) {
      await download(id);
      if (n < ids.length - 1) await new Promise((r) => setTimeout(r, 400)); // browsers throttle bursts
    }
    if (selection.folder_ids.length) setToast("Folders can't be downloaded yet: open them and select the files.");
  }

  function open(row: Row) {
    if (mode === "trash") return;
    if (row.type === "folder") router.push(`/drive/${row.item.id}`);
    else setViewer(files.findIndex((f) => f.id === row.item.id));
  }

  // ------------------------------------------------------------ selection
  function click(row: Row, e: React.MouseEvent) {
    const key = rowKey(row);
    if (e.shiftKey && anchor) {
      const keys = rows.map(rowKey);
      const [a, b] = [keys.indexOf(anchor), keys.indexOf(key)].sort((x, y) => x - y);
      setSelected(new Set(keys.slice(a, b + 1)));
      return;
    }
    if (e.metaKey || e.ctrlKey) {
      setSelected((s) => {
        const next = new Set(s);
        if (next.has(key)) next.delete(key);
        else next.add(key);
        return next;
      });
    } else {
      setSelected(new Set([key]));
    }
    setAnchor(key);
  }

  function toggle(row: Row) {
    const key = rowKey(row);
    setSelected((s) => {
      const next = new Set(s);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
    setAnchor(key);
  }

  // keyboard: Delete, F2, Enter, ⌘A, Esc
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (dialog || viewer !== null) return;
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA") return;
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "a") {
        e.preventDefault();
        setSelected(new Set(rows.map(rowKey)));
      } else if (e.key === "Escape") {
        setSelected(new Set());
        setMenu(null);
      } else if ((e.key === "Delete" || (e.key === "Backspace" && e.metaKey)) && chosen.length && mode !== "trash") {
        trash();
      } else if (e.key === "F2" && chosen.length === 1 && mode !== "trash") {
        setDialog({ kind: "rename", row: chosen[0] });
      } else if (e.key === "Enter" && chosen.length === 1) {
        open(chosen[0]);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  useEffect(() => {
    if (!menu && !newMenu) return;
    const close = () => {
      setMenu(null);
      setNewMenu(false);
    };
    window.addEventListener("click", close);
    window.addEventListener("scroll", close, true);
    return () => {
      window.removeEventListener("click", close);
      window.removeEventListener("scroll", close, true);
    };
  }, [menu, newMenu]);

  // ------------------------------------------------------------ drag and drop
  const canUpload = mode === "folder";

  function onDragEnter(e: React.DragEvent) {
    if (!canUpload || !e.dataTransfer.types.includes("Files")) return;
    dragDepth.current++;
    setDropping(true);
  }
  function onDragLeave(e: React.DragEvent) {
    if (!canUpload || !e.dataTransfer.types.includes("Files")) return;
    dragDepth.current = Math.max(0, dragDepth.current - 1);
    if (dragDepth.current === 0) setDropping(false);
  }
  async function onDrop(e: React.DragEvent) {
    if (!canUpload || !e.dataTransfer.types.includes("Files")) return;
    e.preventDefault();
    dragDepth.current = 0;
    setDropping(false);
    try {
      const picked = await collectDropped(e.dataTransfer);
      if (!picked.length) return;
      // a dropped folder can hold a lot: say what's about to go up, and where, first
      if (picked.some((p) => p.relPath.includes("/"))) setPendingDrop(picked);
      else await uploads.add(picked, folderId);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not read what was dropped");
    }
  }

  // rows dragged onto a folder (or a breadcrumb) move there
  function dragStart(row: Row, e: React.DragEvent) {
    if (mode === "trash") return;
    const key = rowKey(row);
    const moving = selected.has(key) ? chosen : [row];
    if (!selected.has(key)) setSelected(new Set([key]));
    e.dataTransfer.setData(
      DRAG_TYPE,
      JSON.stringify({
        file_ids: moving.filter((r) => r.type === "file").map((r) => r.item.id),
        folder_ids: moving.filter((r) => r.type === "folder").map((r) => r.item.id),
      }),
    );
    e.dataTransfer.effectAllowed = "move";
  }
  function dropOnFolder(targetId: string | null, e: React.DragEvent) {
    const raw = e.dataTransfer.getData(DRAG_TYPE);
    setDropTarget(null);
    if (!raw) return;
    e.preventDefault();
    e.stopPropagation();
    const items = JSON.parse(raw) as { file_ids: string[]; folder_ids: string[] };
    if (targetId && items.folder_ids.includes(targetId)) return;
    run(
      () => api<Outcome>("/drive/move", { method: "POST", json: { ...items, target_folder_id: targetId } }),
      (o) => `Moved ${plural(count(o))}`,
    );
  }
  const folderDropProps = (targetId: string | null, key: string) => ({
    onDragOver: (e: React.DragEvent) => {
      if (e.dataTransfer.types.includes(DRAG_TYPE)) {
        e.preventDefault();
        setDropTarget(key);
      }
    },
    onDragLeave: () => setDropTarget((t) => (t === key ? null : t)),
    onDrop: (e: React.DragEvent) => dropOnFolder(targetId, e),
  });

  // ------------------------------------------------------------ pieces
  const actions = (() => {
    const list: { label: string; Icon: typeof TrashIcon; onClick: () => void; danger?: boolean }[] = [];
    if (!chosen.length) return list;
    if (mode === "trash") {
      list.push({ label: "Restore", Icon: RestoreIcon, onClick: restore });
      list.push({ label: "Delete forever", Icon: TrashIcon, onClick: () => setDialog({ kind: "delete-forever" }), danger: true });
      return list;
    }
    if (selection.file_ids.length) list.push({ label: "Download", Icon: DownloadIcon, onClick: downloadSelected });
    if (chosen.length === 1) list.push({ label: "Rename", Icon: PencilIcon, onClick: () => setDialog({ kind: "rename", row: chosen[0] }) });
    list.push({ label: "Move to", Icon: MoveIcon, onClick: () => setDialog({ kind: "move" }) });
    list.push({ label: "Copy to", Icon: CopyIcon, onClick: () => setDialog({ kind: "copy" }) });
    list.push({ label: "Delete", Icon: TrashIcon, onClick: trash, danger: true });
    return list;
  })();

  const header = (
    <div className="flex min-h-12 flex-wrap items-center gap-2 px-4 pt-4 sm:px-6">
      {mode === "folder" && data ? (
        <nav aria-label="Folder path" className="flex min-w-0 flex-1 items-center gap-1 text-lg">
          {data.breadcrumbs.map((c, n) => {
            const last = n === data.breadcrumbs.length - 1;
            const key = `crumb:${c.id ?? "root"}`;
            return (
              <span key={key} className="flex min-w-0 items-center gap-1">
                {n > 0 && <ChevronRight size={16} className="shrink-0 text-muted" />}
                {last ? (
                  <span className="truncate font-semibold">{c.name}</span>
                ) : (
                  <Link
                    href={c.id ? `/drive/${c.id}` : "/drive"}
                    {...folderDropProps(c.id, key)}
                    className={`truncate rounded-md px-1.5 py-0.5 text-muted hover:bg-surface-hover hover:text-fg ${
                      dropTarget === key ? "bg-selected ring-2 ring-brand" : ""
                    }`}
                  >
                    {c.name}
                  </Link>
                )}
              </span>
            );
          })}
        </nav>
      ) : (
        <h1 className="flex-1 text-lg font-semibold">{title}</h1>
      )}
      <div className="flex items-center gap-1 rounded-lg border border-line p-0.5">
        <button
          type="button"
          onClick={() => chooseView("list")}
          className={`rounded-md p-1.5 ${view === "list" ? "bg-surface-hover" : "text-muted"}`}
          aria-label="List view"
        >
          <ListIcon size={18} />
        </button>
        <button
          type="button"
          onClick={() => chooseView("grid")}
          className={`rounded-md p-1.5 ${view === "grid" ? "bg-surface-hover" : "text-muted"}`}
          aria-label="Grid view"
        >
          <GridIcon size={18} />
        </button>
      </div>
    </div>
  );

  const commandBar = (
    <div className="sticky top-14 z-20 flex min-h-12 flex-wrap items-center gap-1 border-b border-line bg-bg px-3 py-1.5 sm:px-5">
      {chosen.length > 0 ? (
        <>
          <button type="button" onClick={() => setSelected(new Set())} className="rounded-lg p-2 hover:bg-surface-hover" aria-label="Clear selection">
            <XIcon size={18} />
          </button>
          <span className="mr-2 text-sm font-medium">{chosen.length} selected</span>
          {actions.map(({ label, Icon, onClick, danger }) => (
            <button
              key={label}
              type="button"
              onClick={onClick}
              className={`inline-flex items-center gap-2 rounded-lg px-3 py-2 text-sm hover:bg-surface-hover ${danger ? "text-bad" : ""}`}
            >
              <Icon size={18} />
              <span className="hidden sm:inline">{label}</span>
            </button>
          ))}
        </>
      ) : mode === "folder" ? (
        <>
          <div className="relative">
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                setNewMenu((v) => !v);
              }}
              className="inline-flex items-center gap-2 rounded-lg bg-brand px-3.5 py-2 text-sm font-medium text-brand-contrast hover:bg-brand-hover"
            >
              <PlusIcon size={18} /> New
            </button>
            {newMenu && (
              <div className="absolute left-0 z-30 mt-1 w-52 overflow-hidden rounded-xl border border-line bg-bg py-1 shadow-xl">
                <MenuItem Icon={FolderPlusIcon} label="Folder" onClick={() => setDialog({ kind: "new-folder" })} />
                <MenuItem Icon={UploadIcon} label="Upload files" onClick={() => fileInput.current?.click()} />
                <MenuItem Icon={UploadIcon} label="Upload folder" onClick={() => folderInput.current?.click()} />
              </div>
            )}
          </div>
          <button type="button" onClick={() => fileInput.current?.click()} className="inline-flex items-center gap-2 rounded-lg px-3 py-2 text-sm hover:bg-surface-hover">
            <UploadIcon size={18} /> <span className="hidden sm:inline">Upload</span>
          </button>
          <SortPicker sort={sort} setSort={setSort} />
        </>
      ) : mode === "trash" ? (
        <>
          <span className="text-sm text-muted">Items in the trash are deleted for good after 30 days.</span>
          {rows.length > 0 && (
            <button type="button" onClick={() => setDialog({ kind: "empty-trash" })} className="ml-auto inline-flex items-center gap-2 rounded-lg px-3 py-2 text-sm text-bad hover:bg-surface-hover">
              <TrashIcon size={18} /> Empty trash
            </button>
          )}
        </>
      ) : (
        <SortPicker sort={sort} setSort={setSort} />
      )}
    </div>
  );

  function contextMenu(row: Row, e: React.MouseEvent) {
    e.preventDefault();
    const key = rowKey(row);
    if (!selected.has(key)) setSelected(new Set([key]));
    setMenu({ x: Math.min(e.clientX, window.innerWidth - 220), y: Math.min(e.clientY, window.innerHeight - 280) });
  }

  const icon = (row: Row, size: number) =>
    row.type === "folder" ? <FolderGlyph size={size} /> : <FileGlyph kind={row.item.kind} size={size} />;

  const detail = (row: Row) =>
    row.type === "folder"
      ? row.item.item_count != null
        ? `${row.item.item_count} item${row.item.item_count === 1 ? "" : "s"}`
        : "Folder"
      : formatBytes(row.item.size_bytes);

  function renderList(list: Row[]) {
    return (
      <table className="w-full table-fixed text-sm">
        <thead className="text-left text-xs text-muted">
          <tr className="border-b border-line">
            <th className="w-10 py-2 pl-4 sm:pl-6">
              <input
                type="checkbox"
                aria-label="Select all"
                checked={list.length > 0 && list.every((r) => selected.has(rowKey(r)))}
                onChange={(e) => setSelected(e.target.checked ? new Set(list.map(rowKey)) : new Set())}
              />
            </th>
            <th className="py-2 font-medium">Name</th>
            {mode !== "folder" && <th className="hidden w-1/4 py-2 font-medium lg:table-cell">Location</th>}
            <th className="hidden w-36 py-2 font-medium sm:table-cell">{mode === "trash" ? "Deleted" : "Modified"}</th>
            <th className="hidden w-28 py-2 pr-4 text-right font-medium md:table-cell">Size</th>
            <th className="w-12" />
          </tr>
        </thead>
        <tbody>
          {list.map((row) => {
            const key = rowKey(row);
            const isSel = selected.has(key);
            const isFolder = row.type === "folder";
            return (
              <tr
                key={key}
                draggable={mode !== "trash"}
                onDragStart={(e) => dragStart(row, e)}
                {...(isFolder && mode !== "trash" ? folderDropProps(row.item.id, key) : {})}
                onClick={(e) => click(row, e)}
                onDoubleClick={() => open(row)}
                onContextMenu={(e) => contextMenu(row, e)}
                className={`group cursor-default border-b border-line/60 ${
                  dropTarget === key ? "bg-selected ring-2 ring-inset ring-brand" : isSel ? "bg-selected" : "hover:bg-surface"
                }`}
              >
                <td className="py-2 pl-4 sm:pl-6" onClick={(e) => e.stopPropagation()}>
                  <input type="checkbox" aria-label={`Select ${row.item.name}`} checked={isSel} onChange={() => toggle(row)} />
                </td>
                <td className="py-2">
                  <div className="flex min-w-0 items-center gap-3">
                    {icon(row, 26)}
                    <button
                      type="button"
                      className="truncate text-left hover:underline"
                      onClick={(e) => {
                        e.stopPropagation();
                        open(row);
                      }}
                      title={row.item.name}
                    >
                      {row.item.name}
                    </button>
                  </div>
                </td>
                {mode !== "folder" && <td className="hidden truncate py-2 text-muted lg:table-cell">{location(row)}</td>}
                <td className="hidden py-2 text-muted sm:table-cell">{formatDate(modified(row))}</td>
                <td className="hidden py-2 pr-4 text-right text-muted tabular md:table-cell">{detail(row)}</td>
                <td className="py-2 pr-2 text-right">
                  <button
                    type="button"
                    aria-label="More actions"
                    className="rounded-md p-1.5 text-muted opacity-0 hover:bg-surface-hover group-hover:opacity-100 max-sm:opacity-100"
                    onClick={(e) => {
                      e.stopPropagation();
                      contextMenu(row, e);
                    }}
                  >
                    <MoreIcon size={18} />
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    );
  }

  function renderGrid(list: Row[]) {
    return (
      <ul className="grid grid-cols-[repeat(auto-fill,minmax(140px,1fr))] gap-3 px-4 py-3 sm:px-6">
        {list.map((row) => {
          const key = rowKey(row);
          const isSel = selected.has(key);
          return (
            <li
              key={key}
              draggable={mode !== "trash"}
              onDragStart={(e) => dragStart(row, e)}
              {...(row.type === "folder" && mode !== "trash" ? folderDropProps(row.item.id, key) : {})}
              onClick={(e) => click(row, e)}
              onDoubleClick={() => open(row)}
              onContextMenu={(e) => contextMenu(row, e)}
              className={`group relative flex cursor-default flex-col items-center gap-2 rounded-xl border p-3 text-center ${
                dropTarget === key
                  ? "border-brand bg-selected"
                  : isSel
                    ? "border-brand/50 bg-selected"
                    : "border-transparent hover:bg-surface"
              }`}
            >
              <input
                type="checkbox"
                aria-label={`Select ${row.item.name}`}
                checked={isSel}
                onClick={(e) => e.stopPropagation()}
                onChange={() => toggle(row)}
                className={`absolute left-2 top-2 ${isSel ? "" : "opacity-0 group-hover:opacity-100"}`}
              />
              <div className="flex h-20 items-center justify-center">{icon(row, 64)}</div>
              <div className="w-full truncate text-sm" title={row.item.name}>
                {row.item.name}
              </div>
              <div className="text-xs text-muted">{detail(row)}</div>
            </li>
          );
        })}
      </ul>
    );
  }

  function renderRows(list: Row[]) {
    return view === "grid" ? renderGrid(list) : renderList(list);
  }

  // photos and videos: grouped by month (by upload date: no bytes are read to find the date taken)
  const groups = useMemo(() => {
    if (!groupByMonth) return null;
    const map = new Map<string, Row[]>();
    for (const row of rows) {
      const label = monthLabel(row.item.created_at);
      map.set(label, [...(map.get(label) ?? []), row]);
    }
    return [...map.entries()];
  }, [rows, groupByMonth]);

  return (
    <div
      className="relative min-h-[calc(100vh-3.5rem)]"
      onDragEnter={onDragEnter}
      onDragLeave={onDragLeave}
      onDragOver={(e) => canUpload && e.dataTransfer.types.includes("Files") && e.preventDefault()}
      onDrop={onDrop}
      onClick={(e) => {
        if (e.target === e.currentTarget) setSelected(new Set());
      }}
    >
      {header}
      {commandBar}
      <div className="pt-2">
        {error && (
          <div className="px-4 sm:px-6">
            <ErrorBanner message={error} onClose={() => setError(undefined)} />
          </div>
        )}
        {!data ? (
          <div className="flex justify-center py-20">
            <Spinner size={28} />
          </div>
        ) : rows.length === 0 ? (
          <div className="flex flex-col items-center px-6 py-20 text-center">
            <div className="mb-4 flex h-24 w-24 items-center justify-center rounded-full bg-surface">
              {mode === "folder" ? <UploadIcon size={40} className="text-brand" /> : <FolderGlyph size={44} />}
            </div>
            <div className="text-lg font-semibold">{empty.title}</div>
            {empty.hint && <div className="mt-1 max-w-sm text-sm text-muted">{empty.hint}</div>}
          </div>
        ) : groups ? (
          groups.map(([label, list]) => (
            <section key={label} className="mb-4">
              <h2 className="px-4 pt-3 text-sm font-semibold text-muted sm:px-6">{label}</h2>
              {renderRows(list)}
            </section>
          ))
        ) : (
          renderRows(rows)
        )}
      </div>

      {dropping && (
        <div className="pointer-events-none fixed inset-0 z-30 flex items-center justify-center border-4 border-dashed border-brand bg-brand/10 lg:left-60 lg:top-14">
          <div className="rounded-2xl bg-bg px-8 py-6 text-center shadow-xl">
            <UploadIcon size={36} className="mx-auto mb-2 text-brand" />
            <div className="font-semibold">Drop to upload</div>
            <div className="text-sm text-muted">Files and folders go into {data?.folder?.name ?? "My files"}</div>
          </div>
        </div>
      )}

      {menu && chosen.length > 0 && (
        <div
          className="fixed z-50 w-52 overflow-hidden rounded-xl border border-line bg-bg py-1 shadow-xl"
          style={{ left: menu.x, top: menu.y }}
          onClick={(e) => e.stopPropagation()}
        >
          {chosen.length === 1 && mode !== "trash" && (
            <MenuItem
              Icon={chosen[0].type === "folder" ? MoveIcon : DownloadIcon}
              label="Open"
              onClick={() => {
                setMenu(null);
                open(chosen[0]);
              }}
            />
          )}
          {actions.map(({ label, Icon, onClick, danger }) => (
            <MenuItem
              key={label}
              Icon={Icon}
              label={label}
              danger={danger}
              onClick={() => {
                setMenu(null);
                onClick();
              }}
            />
          ))}
        </div>
      )}

      <input
        ref={fileInput}
        type="file"
        multiple
        hidden
        onChange={async (e) => {
          if (e.target.files?.length) await uploads.add(collectPicked(e.target.files), folderId);
          e.target.value = "";
        }}
      />
      <input
        ref={folderInput}
        type="file"
        hidden
        // @ts-expect-error: non-standard, but every current browser supports it
        webkitdirectory=""
        onChange={async (e) => {
          if (e.target.files?.length) await uploads.add(collectPicked(e.target.files), folderId);
          e.target.value = "";
        }}
      />

      {dialog?.kind === "new-folder" && (
        <NameDialog
          title="New folder"
          label="Name"
          initial="New folder"
          action="Create"
          onClose={() => setDialog(null)}
          onSubmit={async (name) => {
            await api("/drive/folders", { method: "POST", json: { parent_id: folderId, name } });
            driveChanged(folderId);
          }}
        />
      )}
      {dialog?.kind === "rename" && (
        <NameDialog
          title={`Rename ${dialog.row.type}`}
          label="Name"
          initial={dialog.row.item.name}
          action="Rename"
          onClose={() => setDialog(null)}
          onSubmit={async (name) => {
            const path = dialog.row.type === "folder" ? `/drive/folders/${dialog.row.item.id}` : `/drive/files/${dialog.row.item.id}`;
            await api(path, { method: "PATCH", json: { name } });
            setSelected(new Set());
            driveChanged(folderId);
          }}
        />
      )}
      {(dialog?.kind === "move" || dialog?.kind === "copy") && (
        <MoveCopyDialog
          mode={dialog.kind}
          count={chosen.length}
          disabled={new Set(selection.folder_ids)}
          onClose={() => setDialog(null)}
          onSubmit={async (target) => {
            const outcome = await api<Outcome>(`/drive/${dialog.kind}`, {
              method: "POST",
              json: { ...selection, target_folder_id: target },
            });
            setToast(
              `${dialog.kind === "move" ? "Moved" : "Copied"} ${plural(count(outcome))}${
                outcome.renamed.length ? ` (renamed: ${outcome.renamed.join(", ")})` : ""
              }`,
            );
            setSelected(new Set());
            driveChanged(folderId);
          }}
        />
      )}
      {dialog?.kind === "delete-forever" && (
        <ConfirmDialog
          title="Delete forever?"
          danger
          action="Delete forever"
          message={`${plural(chosen.length)} and everything inside will be deleted from your cloud storage. This can't be undone.`}
          onClose={() => setDialog(null)}
          onConfirm={async () => {
            const o = await api<Outcome>("/drive/delete", { method: "POST", json: selection });
            setToast(`Deleted ${o.files} file${o.files === 1 ? "" : "s"} for good`);
            setSelected(new Set());
            driveChanged(null);
          }}
        />
      )}
      {dialog?.kind === "empty-trash" && (
        <ConfirmDialog
          title="Empty the trash?"
          danger
          action="Empty trash"
          message="Everything in the trash will be deleted from your cloud storage. This can't be undone."
          onClose={() => setDialog(null)}
          onConfirm={async () => {
            const o = await api<Outcome>("/drive/trash/empty", { method: "POST" });
            setToast(`Deleted ${o.files} file${o.files === 1 ? "" : "s"} for good`);
            driveChanged(null);
          }}
        />
      )}
      {pendingDrop && (
        <DropConfirm
          picked={pendingDrop}
          destination={data?.folder?.name ?? "My files"}
          onCancel={() => setPendingDrop(null)}
          onConfirm={async () => {
            const picked = pendingDrop;
            setPendingDrop(null);
            try {
              await uploads.add(picked, folderId);
            } catch (err) {
              setError(err instanceof Error ? err.message : "Could not start the upload");
            }
          }}
        />
      )}
      {viewer !== null && viewer >= 0 && <Viewer files={files} index={viewer} onClose={() => setViewer(null)} />}
      {toast && <Toast message={toast} onDone={() => setToast(null)} />}
    </div>
  );
}

function MenuItem({
  Icon,
  label,
  onClick,
  danger,
}: {
  Icon: typeof TrashIcon;
  label: string;
  onClick: () => void;
  danger?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex w-full items-center gap-3 px-3.5 py-2 text-left text-sm hover:bg-surface-hover ${danger ? "text-bad" : ""}`}
    >
      <Icon size={18} /> {label}
    </button>
  );
}

function SortPicker({
  sort,
  setSort,
}: {
  sort: { key: SortKey; desc: boolean };
  setSort: (s: { key: SortKey; desc: boolean }) => void;
}) {
  return (
    <label className="ml-auto flex items-center gap-2 text-sm text-muted">
      Sort
      <select
        value={`${sort.key}:${sort.desc ? "desc" : "asc"}`}
        onChange={(e) => {
          const [key, dir] = e.target.value.split(":");
          setSort({ key: key as SortKey, desc: dir === "desc" });
        }}
        className="rounded-lg border border-line bg-bg px-2 py-1.5 text-fg"
      >
        <option value="name:asc">Name A–Z</option>
        <option value="name:desc">Name Z–A</option>
        <option value="modified:desc">Newest first</option>
        <option value="modified:asc">Oldest first</option>
        <option value="size:desc">Largest first</option>
        <option value="size:asc">Smallest first</option>
      </select>
    </label>
  );
}

function DropConfirm({
  picked,
  destination,
  onCancel,
  onConfirm,
}: {
  picked: PickedFile[];
  destination: string;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const files = picked.filter((p) => p.file);
  const bytes = files.reduce((s, p) => s + p.file.size, 0);
  const tops = [...new Set(picked.filter((p) => p.relPath.includes("/")).map((p) => p.relPath.split("/")[0]))];
  const loose = files.filter((p) => !p.relPath.includes("/")).length;
  const what =
    tops.length === 1 && !loose ? `the folder “${tops[0]}”` : `${tops.length} folder${tops.length === 1 ? "" : "s"}${loose ? ` and ${loose} file${loose === 1 ? "" : "s"}` : ""}`;
  return (
    <Modal
      title={`Upload ${files.length} file${files.length === 1 ? "" : "s"}?`}
      onClose={onCancel}
      footer={
        <>
          <Button onClick={onCancel}>Cancel</Button>
          <Button variant="primary" data-autofocus onClick={onConfirm}>
            <UploadIcon size={16} /> Upload
          </Button>
        </>
      }
    >
      <div className="flex items-start gap-4">
        <FolderGlyph size={44} />
        <div className="text-sm">
          <p>
            Everything in {what} goes into <span className="font-medium">{destination}</span>, keeping its folders.
          </p>
          <p className="mt-2 text-muted">
            {files.length} file{files.length === 1 ? "" : "s"} · {formatBytes(bytes)}
          </p>
        </div>
      </div>
    </Modal>
  );
}

